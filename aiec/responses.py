"""Claude por la API oficial. Inactivo sin clave y activación explícita en .env."""
import json
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, ConfigDict, Field

from .config import ConfigurationError
from .business import current_messages
from .prompts import REPLY_GUIDANCE
from .rag import SourceCitation
from .service import Analysis, catalog, validate_text


def configured_responder(settings):
    if not settings.auto_reply_enabled:
        return None
    return ClaudeResponder(settings.anthropic_api_key, settings.claude_model)


class ClaudeOutput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    analysis: Analysis
    response: str = Field(min_length=1, max_length=4000)
    sources: list[SourceCitation] = Field(max_length=5)


@dataclass
class PreparedResponse:
    analysis: Analysis
    text: str
    context_id: str | None
    sources: list[SourceCitation]


def api_schema(value):
    """La API limita restricciones de longitud; Pydantic las verifica al regresar."""
    if isinstance(value, list):
        return [api_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    unsupported = {'minLength', 'maxLength', 'minimum', 'maximum', 'minItems', 'maxItems'}
    return {key: api_schema(item) for key, item in value.items() if key not in unsupported}


class ClaudeResponder:
    def __init__(self, api_key, model='claude-haiku-5-5', *, client=None):
        if not api_key.strip():
            raise ConfigurationError('La atención automática con Claude necesita ANTHROPIC_API_KEY y créditos de API.')
        self._key = api_key
        self.model = model
        # No reenviar claves mediante redirecciones ni proxies tomados del entorno.
        self.client = client or httpx.Client(timeout=httpx.Timeout(90, connect=10),
                                              follow_redirects=False, trust_env=False)

    def prepare(self, service, ticket):
        if service.knowledge is None:
            raise ValueError('Falta el conocimiento de la imprenta.')
        context = service.knowledge_context(ticket)
        data = catalog()
        history, budget = [], 16000
        for message in reversed(current_messages(ticket)[-12:]):
            text = message['text'][:min(budget, 4000)]
            if not text:
                break
            history.append({'role': message['role'], 'text': text})
            budget -= len(text)
        payload = {
            'model': self.model, 'max_tokens': 1800,
            'system': REPLY_GUIDANCE + '\nDevuelve analysis, response y sources en el formato solicitado. '
                'evidence cita literalmente al cliente; sources cita chunk_id y quote literal de los fragmentos. '
                'Si no hay evidencia, sources queda vacío y missing_data lo registra. '
                'No repitas importes del presupuesto del cliente como si fueran precios. '
                'Todo precio externo lleva proveedor y URL en response, y una aclaración de que es referencia externa. '
                'Escribe los importes como $999.00 MXN, nunca como cantidades monetarias en palabras. '
                'No conviertas monedas ni calcules precios o impuestos nuevos. La respuesta se enviará sin aprobación manual.',
            'messages': [{'role': 'user', 'content': json.dumps({
                'business': data['business'], 'currency': data['currency'],
                'pricing_policy': data['pricing_policy'],
                'services': [{'id': s['id'], 'name': s['name']} for s in data['services']],
                'history_untrusted': list(reversed(history)),
                'knowledge_untrusted': [{k: hit[k] for k in ('id','source','title','text')} for hit in context['results']],
            }, ensure_ascii=False)}],
            'output_config': {'format': {'type': 'json_schema', 'schema': api_schema(ClaudeOutput.model_json_schema())}},
        }
        try:
            response = self.client.post('https://api.anthropic.com/v1/messages', json=payload,
                headers={'x-api-key': self._key, 'anthropic-version': '2023-06-01'})
        except httpx.TransportError:
            raise ValueError('No se confirmó la respuesta de Claude. No se envió ningún mensaje a Telegram.') from None
        if response.status_code != 200:
            raise ValueError(f'Claude rechazó la solicitud (HTTP {response.status_code}). Revisa la clave, créditos y modelo.')
        try:
            result = response.json()
            if result.get('stop_reason') != 'end_turn':
                raise ValueError('Respuesta incompleta de Claude.')
            blocks = result['content']
            text = ''.join(block['text'] for block in blocks if block.get('type') == 'text')
            output = ClaudeOutput.model_validate_json(text)
        except (ValueError, KeyError, TypeError, AttributeError):
            raise ValueError('Claude no devolvió una respuesta completa con el formato requerido.') from None
        text = validate_text(output.response)
        # Verifica fuentes y revisión antes de aceptar cualquier cifra monetaria.
        service.validate_response(ticket['_id'], ticket['revision'], output.analysis, text,
            rag_context_id=context['context_id'], sources=output.sources)
        return PreparedResponse(output.analysis, text, context['context_id'], output.sources)
