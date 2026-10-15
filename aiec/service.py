import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .repository import ConflictError, Repository
from .telegram import DeliveryUncertain, Telegram, TelegramRejected


class Analysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: Literal["publicidad_redes", "diseno_web", "contenido", "otro", "desconocido"]
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
    def __init__(self, repository: Repository, telegram: Telegram | None = None, *, demo=False, knowledge=None):
        self.repo = repository
        self.telegram = telegram
        self.demo = demo
        self.knowledge = knowledge

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

    def save_draft(self, ticket_id, revision, analysis: Analysis, text, *, rag_context_id=None, sources=None):
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
        return self.repo.save_draft(ticket_id, revision, saved_analysis, text)

    def send_approved(self, ticket_id, draft_id, revision, text):
        if self.demo:
            raise ValueError("La demo no envía mensajes reales. Conecta Telegram y MongoDB para usar el envío.")
        if not self.telegram:
            raise ValueError("Falta configurar el bot de Telegram.")
        text = validate_text(text)
        if self.knowledge is not None:
            current = self.repo.get(ticket_id)
            self.knowledge.check_draft((current.get("analysis") or {}).get("knowledge"))
        ticket = self.repo.begin_send(ticket_id, draft_id, revision, text)
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
