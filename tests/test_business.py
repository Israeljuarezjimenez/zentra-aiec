from datetime import timedelta
from types import SimpleNamespace

from bson import json_util
import pytest

from aiec.business import business_id, current_messages
from aiec.migration import migrate_business
from aiec.repository import now
from aiec.service import Analysis, public_ticket
from aiec.telegram_profile import sync_telegram_profile


def legacy_ticket(repo, *, printing=True):
    since = now() - timedelta(hours=1)
    ticket_id, _ = repo.receive(111, 1, 'Cliente', 'Quiero una campaña. Mi presupuesto es $10,000.')
    original = repo.get(ticket_id)
    messages = [{**original['messages'][0], 'created_at':since-timedelta(hours=1)},
                {'role':'agencia', 'text':'Estudio Órbita propone una campaña de publicidad.',
                 'created_at':since-timedelta(minutes=1), 'telegram_message_id':2}]
    if printing:
        messages.append({'role':'cliente', 'text':'Necesito una lona de 12 × 5 m.',
                         'created_at':since+timedelta(minutes=1), 'telegram_message_id':3})
    for m in messages:
        m.pop('business_id', None)
    repo.tickets.update_one({'_id':ticket_id}, {
        '$unset':{'business_id':''}, '$set':{'messages':messages,
            'analysis':{'budget':'$10,000'}, 'revision':len(messages)},
    })
    return repo.get(ticket_id), since


def test_migration_preserves_history_and_recipient_with_private_backup_and_is_idempotent(repo, tmp_path):
    ticket, since = legacy_ticket(repo)
    result = migrate_business(repo, since, tmp_path)
    assert result == {'updated':1, 'archived_messages':2, 'conflicts':0, 'blocked':0}
    current = repo.get(ticket['_id'])
    assert current['chat_id'] == ticket['chat_id']
    assert [m['text'] for m in current['archived_messages']+current['messages']] == [m['text'] for m in ticket['messages']]
    assert current['revision'] == ticket['revision']+1
    assert current['analysis'] is None
    assert [m['text'] for m in current_messages(current)] == ['Necesito una lona de 12 × 5 m.']
    assert public_ticket(current, full=True)['archived_message_count'] == 2
    backup = next(tmp_path.glob('*.json'))
    assert backup.stat().st_mode & 0o777 == 0o600
    assert json_util.loads(backup.read_text(), json_options=json_util.JSONOptions(tz_aware=True))['tickets'][0] == ticket
    assert migrate_business(repo, since, tmp_path)['updated'] == 0
    assert repo.get(ticket['_id'])['revision'] == current['revision']


def test_old_business_is_not_listed_or_claimed_and_a_new_message_reopens_it(repo, tmp_path):
    ticket, since = legacy_ticket(repo, printing=False)
    migrate_business(repo, since, tmp_path)
    assert repo.list() == [] and sum(repo.counts().values()) == 0
    assert repo.claim_next(since-timedelta(days=2), debounce_seconds=0) is None
    tid, _ = repo.receive(ticket['chat_id'], 4, 'Cliente', 'Necesito tarjetas de presentación.')
    assert tid == ticket['_id']
    assert repo.get(tid)['business_id'] == business_id()
    assert len(repo.list('pendiente')) == 1
    assert len(public_ticket(repo.get(tid), full=True)['messages']) == 1


def test_unconfirmed_delivery_is_backed_up_and_not_modified_by_migration(repo, tmp_path):
    ticket, since = legacy_ticket(repo)
    repo.tickets.update_one({'_id':ticket['_id']}, {'$set':{'delivery_state':'incierto'}})
    before = repo.get(ticket['_id'])
    assert migrate_business(repo, since, tmp_path)['blocked'] == 1
    assert repo.get(ticket['_id']) == before


def test_archived_history_survives_the_active_conversation_history_limit(repo, tmp_path):
    ticket, since = legacy_ticket(repo)
    migrate_business(repo, since, tmp_path)
    archived = repo.get(ticket['_id'])['archived_messages']
    for mid in range(10, 115):
        repo.receive(ticket['chat_id'], mid, 'Cliente', 'Otra consulta de impresión.')
    current = repo.get(ticket['_id'])
    assert len(current['messages']) == 100
    assert current['archived_messages'] == archived
    assert current['archived_message_count'] == len(archived) == 2


def test_message_arriving_during_migration_is_not_lost(repo, tmp_path, monkeypatch):
    ticket, since = legacy_ticket(repo)
    update = repo.tickets.update_one
    intercepted = False
    def arrive(query, changes, *args, **kwargs):
        nonlocal intercepted
        if '$inc' in changes and changes['$inc'].get('revision') and 'messages' in changes.get('$set', {}):
            if not intercepted:
                intercepted = True
                repo.receive(ticket['chat_id'], 5, 'Cliente', 'Ahora necesito dos lonas.')
        return update(query, changes, *args, **kwargs)
    monkeypatch.setattr(repo.tickets, 'update_one', arrive)
    assert migrate_business(repo, since, tmp_path)['conflicts'] == 1
    assert repo.get(ticket['_id'])['messages'][-1]['text'] == 'Ahora necesito dos lonas.'
    assert migrate_business(repo, since, tmp_path)['updated'] == 1
    assert current_messages(repo.get(ticket['_id']))[-1]['text'] == 'Ahora necesito dos lonas.'


def test_claude_and_rag_never_receive_old_business_messages_or_budget(service, repo, tmp_path):
    ticket, since = legacy_ticket(repo)
    migrate_business(repo, since, tmp_path)
    current = repo.get(ticket['_id'])
    captured = []
    service.knowledge = SimpleNamespace(search=lambda query, **kwargs: captured.append(query))
    service.knowledge_context(current)
    assert captured == ['Necesito una lona de 12 × 5 m.']
    public = public_ticket(current, full=True)
    assert 'Órbita' not in str(public) and '$10,000' not in str(public)
    service.knowledge = None
    analysis = Analysis(service='gran_formato', summary='Solicita una lona.', priority='baja',
        priority_reason='Sin urgencia.', evidence=['Necesito una lona de 12 × 5 m.'])
    analysis.budget = '$10,000'
    with pytest.raises(ValueError, match='presupuesto'):
        service.validate_response(current['_id'], current['revision'], analysis, 'Podemos orientarte sobre lona.')
    analysis.budget = None
    analysis.evidence = ['Mi presupuesto es $10,000.']
    with pytest.raises(ValueError, match='evidencia'):
        service.validate_response(current['_id'], current['revision'], analysis, 'Podemos orientarte sobre lona.')


@pytest.mark.parametrize('budget', ['10000 pesos', '10,000', '$10,000'])
def test_budget_is_preserved_as_written_without_requiring_a_currency_symbol(service, repo, budget):
    ticket_id, _ = repo.receive(222, 1, 'Cliente', f'Quiero volantes. Mi presupuesto es {budget}.')
    ticket = repo.get(ticket_id)
    analysis = Analysis(service='offset', summary='Solicita volantes.', priority='baja',
        priority_reason='Sin urgencia.', budget=budget, evidence=['Quiero volantes.'])
    validated = service.validate_response(ticket_id, ticket['revision'], analysis, 'Podemos orientarte sobre volantes.')
    assert validated['budget'] == budget


def test_panel_labels_old_history_without_presenting_it_as_the_print_shop(app, repo, tmp_path):
    ticket, since = legacy_ticket(repo)
    migrate_business(repo, since, tmp_path)
    with app.test_client() as client:
        html = client.get(f'/solicitudes/{ticket["_id"]}').get_data(as_text=True)
        assert 'Archivo del negocio anterior · 2 mensajes' in html
        assert 'Negocio anterior · archivo' in html
        assert 'Necesito una lona de 12 × 5 m.' in html
        assert 'Zentra' not in html
        profile = client.get('/negocio')
        assert profile.status_code == 200
        assert 'cotización propia' in profile.get_data(as_text=True)


def test_untagged_history_is_never_assumed_to_belong_to_the_print_shop(repo):
    ticket, _ = legacy_ticket(repo)
    assert current_messages(ticket) == []
    assert public_ticket(ticket, full=True)['messages'] == []


def test_migration_rejects_an_ambiguous_timezone_before_writing(repo, tmp_path):
    legacy_ticket(repo)
    with pytest.raises(ValueError, match='zona horaria'):
        migrate_business(repo, now().replace(tzinfo=None), tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_telegram_branding_updates_both_languages_and_never_sends_a_message(tmp_path):
    class ProfileTransport:
        def __init__(self):
            self.values = {}
            self.methods = []
        def call(self, method, **payload):
            self.methods.append(method)
            suffix = method[5:]
            field = {'Name':'name','Description':'description','ShortDescription':'short_description'}[suffix]
            key = (payload['language_code'], field)
            if method.startswith('setMy'):
                self.values[key] = payload[field]
                return True
            return {field:self.values.get(key, 'Marca anterior')}
    transport = ProfileTransport()
    result = sync_telegram_profile(transport, tmp_path)
    assert result['verified'] and result['updated_fields'] == 6
    assert set(transport.methods) == {'getMyName','setMyName','getMyDescription','setMyDescription',
                                      'getMyShortDescription','setMyShortDescription'}
    assert sync_telegram_profile(transport, tmp_path)['updated_fields'] == 0
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in tmp_path.glob('*.json'))
