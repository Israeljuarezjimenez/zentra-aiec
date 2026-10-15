import json
from types import SimpleNamespace

import httpx
import pytest

from aiec.config import ConfigurationError, Settings
from aiec.responses import ClaudeResponder, configured_responder


URL = 'https://impresshop.mx/promociones/'
QUOTE = 'Referencia externa EXT-FLY-1000. Impresshop: 1,000 volantes por $999.00 MXN más IVA. ' + URL
HIT = {'id': 'a' * 32, 'source': 'catalogo', 'title': 'Referencia Impresshop', 'text': QUOTE}


@pytest.fixture
def claude_service(service):
    def validate(context_id, sources, **kwargs):
        assert context_id == 'bound-context'
        if any(s.chunk_id != HIT['id'] or s.quote not in QUOTE for s in sources):
            raise ValueError('Cita inválida')
        return {'status':'con_evidencia', 'citations':[{'quote':s.quote, 'source_urls':[URL]} for s in sources]}
    service.knowledge = SimpleNamespace(
        search=lambda *_args, **_kwargs: {'context_id':'bound-context', 'results':[HIT]},
        validate_citations=validate,
    )
    return service


def output(analysis, text=None):
    return {'analysis': analysis.model_dump(), 'response': text or
            f'Referencia de Impresshop: 1,000 volantes por $999.00 MXN más IVA. {URL} No es tarifa de San Martín.',
            'sources': [{'chunk_id':HIT['id'], 'quote':QUOTE}]}


def client_for(value, *, status=200, stop='end_turn', inspector=None):
    def handle(request):
        assert str(request.url) == 'https://api.anthropic.com/v1/messages'
        assert request.headers['x-api-key'] == 'unit-test-not-a-real-key'
        if inspector:
            inspector(json.loads(request.content))
        return httpx.Response(status, json={'stop_reason':stop,
                'content':[{'type':'text', 'text':json.dumps(value)}]})
    return httpx.Client(transport=httpx.MockTransport(handle))


def test_disabled_mode_never_constructs_an_api_client(monkeypatch):
    def unexpected(*_args, **_kwargs):
        pytest.fail('No debe abrir una conexión ni generar cargos.')
    monkeypatch.setattr('aiec.responses.ClaudeResponder', unexpected)
    assert configured_responder(Settings()) is None
    assert configured_responder(Settings(anthropic_api_key='unit-test-not-a-real-key')) is None


def test_api_mode_requires_a_separate_key_before_any_request():
    with pytest.raises(ConfigurationError, match='ANTHROPIC_API_KEY'):
        configured_responder(Settings(auto_reply_enabled=True))


def test_claude_uses_bound_rag_and_structured_output(claude_service, ticket, analysis):
    def inspect(payload):
        assert payload['model'] == 'claude-haiku-5-5'
        assert payload['max_tokens'] <= 2000
        assert payload['output_config']['format']['type'] == 'json_schema'
        user = json.loads(payload['messages'][0]['content'])
        assert user['knowledge_untrusted'][0]['id'] == HIT['id']
        assert user['history_untrusted'][-1]['text'] == ticket['messages'][-1]['text']
        assert 'chat_id' not in user
    responder = ClaudeResponder('unit-test-not-a-real-key', client=client_for(output(analysis), inspector=inspect))
    result = responder.prepare(claude_service, ticket)
    assert result.context_id == 'bound-context'
    assert '$999.00 MXN' in result.text


@pytest.mark.parametrize('text', [
    f'Referencia de Impresshop: $1.00 MXN. {URL}',
    'El precio de San Martín es $999.00 MXN.',
    'Referencia de otra imprenta: $999.00 MXN.',
])
def test_unverified_or_misattributed_prices_are_rejected(claude_service, ticket, analysis, text):
    responder = ClaudeResponder('unit-test-not-a-real-key', client=client_for(output(analysis,text)))
    with pytest.raises(ValueError):
        responder.prepare(claude_service, ticket)


def test_truncated_or_invalid_model_output_cannot_be_sent(claude_service, ticket, analysis):
    for value,stop in [(output(analysis),'max_tokens'), ({'response':'Hola'},'end_turn')]:
        responder = ClaudeResponder('unit-test-not-a-real-key', client=client_for(value,stop=stop))
        with pytest.raises(ValueError, match='formato'):
            responder.prepare(claude_service, ticket)


def test_api_errors_do_not_expose_provider_response_or_keys(claude_service, ticket):
    responder = ClaudeResponder('unit-test-not-a-real-key', client=client_for({'secret':'private'},status=401))
    with pytest.raises(ValueError) as error:
        responder.prepare(claude_service, ticket)
    assert '401' in str(error.value)
    assert 'private' not in str(error.value)
    assert 'unit-test-not-a-real-key' not in str(error.value)
