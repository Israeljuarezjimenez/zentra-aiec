import json
import hashlib
from datetime import timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .repository import ConflictError, Repository, now
from .telegram import DeliveryUncertain, Telegram, TelegramRejected
from .validation import validate_commercial_reply


class Analysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: Literal["offset", "gran_formato", "sellos", "senaletica", "promocionales",
                     "credenciales", "textiles", "diseno", "pizarrones", "otro", "desconocido"]
    summary: str = Field(min_length=1, max_length=800)
    priority: Literal["baja", "media", "alta"]
    priority_reason: str = Field(min_length=1, max_length=400)
    # El monto se mantiene como texto para no confundir presupuesto y cotización.
    budget: str | None = Field(default=None, max_length=150)
    evidence: list[str] = Field(min_length=1, max_length=8)
    missing_data: list[str] = Field(default_factory=list, max_length=10)


def catalog():
    return json.loads(Path(__file__).with_name("catalog.json").read_text(encoding="utf-8"))


def validate_text(text):
    text = text.strip()
    if not text or len(text.encode("utf-16-le")) // 2 > 4000:
        raise ValueError("La respuesta debe contener entre 1 y 4000 caracteres (los emojis pueden ocupar dos).")
    return text


class Service:
    def __init__(self, repository: Repository, telegram: Telegram | None = None, *, demo=False, knowledge=None, responder=None):
        self.repo = repository
        self.telegram = telegram
        self.demo = demo
        self.knowledge = knowledge
        self.responder = responder

    def system_status(self):
        desktop = self.repo.state.find_one({'_id': 'claude_desktop'}) or {}
        receiver = self.repo.state.find_one({'_id': 'bot_health'}) or {}
        checked = receiver.get('checked_at')
        receiver_status = receiver.get('status', 'sin_iniciar')
        if checked and now() - checked > timedelta(seconds=90):
            receiver_status = 'sin_iniciar'
        knowledge = self.knowledge.status() if self.knowledge is not None else None
        return {'mode': 'demo' if self.demo else 'claude_api' if self.responder else 'claude_desktop',
                'delivery_ready': not self.demo and self.telegram is not None,
                'receiver': receiver_status,
                'claude_last_seen': desktop['last_seen_at'].isoformat() if desktop.get('last_seen_at') else None,
                'claude_last_tool': desktop.get('last_tool'),
                'background_enabled': self.responder is not None,
                'knowledge': {'ready': knowledge['ready'], 'stale': knowledge['stale'],
                              'documents': len(knowledge['documents']), 'chunks': knowledge['chunks'],
                              'generation': knowledge['generation']} if knowledge else None}

    def panel_state(self):
        status = self.system_status()
        latest = self.repo.list(limit=1)
        stamp = [(t['_id'], t['revision'], t['status'], t['delivery_state'], t['updated_at'].isoformat()) for t in latest]
        counts = self.repo.counts()
        version = hashlib.sha256(json.dumps([counts, stamp, status], sort_keys=True).encode()).hexdigest()[:20]
        return {'version': version, 'counts': counts, 'system': status}

    def knowledge_context(self, ticket, query=None):
        if self.knowledge is None:
            return {"status": "no_disponible", "results": [],
                    "instruction": "RAG no está conectado en esta demo. No inventar fuentes."}
        messages = [m["text"] for m in ticket["messages"] if m["role"] == "cliente"][-3:]
        query = query or "\n".join(reversed(messages))[:4000]
        return self.knowledge.search(query, ticket_id=ticket["_id"], revision=ticket["revision"])

    def receive_update(self, update):
        message = update.get("message", {})
        chat = message.get("chat", {})
        if chat.get("type") != "private" or message.get("from", {}).get("is_bot"):
            return None
        text = message.get("text", "").strip()
        if not text:
            return None
        name = " ".join(filter(None, [message.get("from", {}).get("first_name"),
                                     message.get("from", {}).get("last_name")])) or "Cliente"
        if text.split()[0].split("@")[0] in ("/start", "/help"):
            # También se guardan los comandos: deduplicación y confirmación de recepción.
            text = "[Inicio de conversación]" if text.startswith("/start") else "[Solicitud de ayuda]"
        return self.repo.receive(chat["id"], message["message_id"], name, text)

    def validate_response(self, ticket_id, revision, analysis: Analysis, text, *, rag_context_id=None, sources=None):
        text = validate_text(text)
        ticket = self.repo.get(ticket_id)
        customer_text = "\n".join(m["text"] for m in ticket["messages"] if m["role"] == "cliente")
        for evidence in analysis.evidence:
            if not evidence.strip() or evidence not in customer_text:
                raise ValueError("Cada evidencia debe ser una cita exacta y no vacía del mensaje del cliente.")
        saved_analysis = analysis.model_dump()
        if self.knowledge is not None:
            saved_analysis["knowledge"] = self.knowledge.validate_citations(
                rag_context_id, sources or [], ticket_id=ticket_id, revision=revision,
            )
            if saved_analysis["knowledge"]["status"] == "sin_evidencia" and not analysis.missing_data:
                raise ValueError("Registra en missing_data que no hay evidencia documental suficiente.")
        elif sources or rag_context_id:
            raise ValueError("RAG no está conectado; no se pueden adjuntar fuentes.")
        validate_commercial_reply(text, saved_analysis.get('knowledge'), catalog())
        return saved_analysis

    def save_draft(self, ticket_id, revision, analysis: Analysis, text, *, rag_context_id=None, sources=None):
        text = validate_text(text)
        saved_analysis = self.validate_response(ticket_id, revision, analysis, text,
            rag_context_id=rag_context_id, sources=sources)
        return self.repo.save_draft(ticket_id, revision, saved_analysis, text)

    def respond_to_customer(self, ticket_id, revision, analysis: Analysis, text, *, rag_context_id=None,
                            sources=None, origin='claude_desktop'):
        """Validar y enviar en una llamada. Una revisión solo puede recibir una respuesta."""
        if self.demo:
            raise ValueError('La demo no envía mensajes reales. El envío directo funciona con tu bot conectado.')
        if not self.telegram:
            raise ValueError('Falta conectar Telegram para enviar la respuesta de Claude.')
        text = validate_text(text)
        previous = self._delivery_receipt(ticket_id, revision, text)
        if previous:
            return {**previous, 'already_sent': True}
        try:
            draft = self.save_draft(ticket_id, revision, analysis, text,
                rag_context_id=rag_context_id, sources=sources)
            result = self.send_response(ticket_id, draft['draft']['id'], revision, text, origin=origin)
        except ConflictError:
            previous = self._delivery_receipt(ticket_id, revision, text)
            if previous:
                return {**previous, 'already_sent': True}
            raise
        return {'id': ticket_id, 'revision': revision, 'status': 'respondida', 'sent': True,
                'delivery_state': 'enviado', 'telegram_message_id': result['message_id'], 'already_sent': False,
                'message': 'Respuesta enviada directamente al cliente y registrada en el historial.'}

    def _delivery_receipt(self, ticket_id, revision, text):
        current = self.repo.get(ticket_id)
        attempt = current.get('attempt', {})
        if current['revision'] != revision:
            raise ConflictError('El cliente escribió un mensaje nuevo. Consulta la conversación actual antes de responder.')
        if current['delivery_state'] == 'enviado' and attempt.get('revision') == revision:
            if attempt.get('text') != text:
                raise ConflictError('Esta revisión ya tiene una respuesta enviada. No se enviará otra diferente.')
            return {'id': ticket_id, 'revision': revision, 'status': current['status'], 'sent': True,
                    'delivery_state': 'enviado', 'telegram_message_id': attempt.get('telegram_message_id'),
                    'message': 'Respuesta enviada directamente al cliente y registrada en el historial.'}
        return None

    def send_response(self, ticket_id, draft_id, revision, text, *, origin='claude_desktop'):
        if self.demo:
            raise ValueError("La demo no envía mensajes reales. Conecta Telegram y MongoDB para usar el envío.")
        if not self.telegram:
            raise ValueError("Falta configurar el bot de Telegram.")
        text = validate_text(text)
        current = self.repo.get(ticket_id)
        if self.knowledge is not None:
            self.knowledge.check_draft((current.get("analysis") or {}).get("knowledge"))
        ticket = self.repo.begin_send(ticket_id, draft_id, revision, text,
                                      analysis=current.get('analysis'), origin=origin)
        attempt_id = ticket["attempt"]["id"]
        try:
            result = self.telegram.send_message(ticket["chat_id"], text)
        except TelegramRejected:
            self.repo.send_failed(ticket_id, attempt_id, uncertain=False)
            raise
        except DeliveryUncertain:
            self.repo.send_failed(ticket_id, attempt_id, uncertain=True)
            raise
        # Si Mongo falla aquí, queda bloqueado como 'enviando' para evitar duplicados.
        self.repo.finish_send(ticket_id, attempt_id, result["message_id"])
        return result


def public_ticket(ticket, *, full=False):
    result = {
        "id": ticket["_id"], "client": ticket["name"], "revision": ticket["revision"],
        "status": ticket["status"], "analysis": ticket.get("analysis"),
        "draft": ticket.get("draft"), "delivery_state": ticket["delivery_state"],
        "updated_at": ticket["updated_at"].isoformat(),
    }
    messages = ticket["messages"][-20:] if full else ticket["messages"][-1:]
    result["messages"] = [{"role": m["role"], "text": m["text"],
                           "date": m["created_at"].isoformat()} for m in messages]
    if result["draft"]:
        result["draft"] = {**result["draft"], "created_at": result["draft"]["created_at"].isoformat()}
    return result
