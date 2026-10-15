from datetime import timedelta
from types import SimpleNamespace

from aiec.automation import process_one
from aiec.repository import now
from aiec.service import Analysis
from aiec.telegram import DeliveryUncertain


class Responder:
    def __init__(self, before_result=None):
        self.before_result = before_result

    def prepare(self, service, ticket):
        text = ticket['messages'][-1]['text']
        if self.before_result:
            self.before_result()
        return SimpleNamespace(
            analysis=Analysis(service='offset', summary='Solicitud de impresión.', priority='media',
                              priority_reason='Sin fecha confirmada.', evidence=[text]),
            text='Podemos orientar tu impresión con las opciones del catálogo.',
            context_id=None, sources=[],
        )


def test_message_is_answered_without_opening_claude_or_approving(service, repo, telegram, ticket):
    service.responder = Responder()
    since = now() - timedelta(minutes=1)
    assert process_one(service, since, debounce_seconds=0)
    assert len(telegram.calls) == 1
    assert repo.get(ticket['_id'])['status'] == 'respondida'
    assert not process_one(service, since, debounce_seconds=0)


def test_new_message_during_generation_discards_the_old_answer(service, repo, telegram, ticket):
    service.responder = Responder(lambda: repo.receive(ticket['chat_id'], 2, 'Cliente', 'Ahora necesito 2,000.'))
    since = now() - timedelta(minutes=1)
    process_one(service, since, debounce_seconds=0)
    assert telegram.calls == []
    assert repo.get(ticket['_id'])['revision'] == 2
    service.responder = Responder()
    process_one(service, since, debounce_seconds=0)
    assert len(telegram.calls) == 1


def test_uncertain_delivery_is_not_retried_automatically(service, repo, telegram, ticket):
    service.responder = Responder()
    telegram.error = DeliveryUncertain('Timeout simulado')
    since = now() - timedelta(minutes=1)
    process_one(service, since, debounce_seconds=0)
    assert repo.get(ticket['_id'])['delivery_state'] == 'incierto'
    assert not process_one(service, since, debounce_seconds=0)
    assert len(telegram.calls) == 1


def test_only_one_worker_can_claim_a_conversation_and_expired_claims_recover(repo, ticket):
    since = now() - timedelta(minutes=1)
    claimed = repo.claim_next(since, debounce_seconds=0)
    assert claimed['_id'] == ticket['_id']
    assert repo.claim_next(since, debounce_seconds=0) is None
    repo.tickets.update_one({'_id': ticket['_id']}, {'$set': {'processing.until': since}})
    assert repo.claim_next(since, debounce_seconds=0)['processing']['id'] != claimed['processing']['id']


def test_historical_pending_messages_are_not_sent_when_automation_is_first_enabled(service, repo, telegram, ticket):
    service.responder = Responder()
    since = now()
    assert not process_one(service, since, debounce_seconds=0)
    repo.receive(ticket['chat_id'], 2, 'Cliente', 'Un nuevo mensaje para la imprenta.')
    assert process_one(service, since, debounce_seconds=0)
    assert len(telegram.calls) == 1


def test_failed_generation_does_not_send_or_mark_conversation_as_answered(service, repo, telegram, ticket):
    class Broken:
        def prepare(self, *_):
            raise ValueError('Fallo del motor')
    service.responder = Broken()
    process_one(service, now() - timedelta(minutes=1), debounce_seconds=0)
    current = repo.get(ticket['_id'])
    assert current['status'] == 'pendiente'
    assert current['automation']['error']
    assert telegram.calls == []


def test_failures_are_bounded_and_new_messages_can_retry(service, repo, ticket):
    class Broken:
        def prepare(self, *_):
            raise ValueError('Fallo del motor')
    service.responder = Broken()
    since = now() - timedelta(minutes=1)
    for _ in range(3):
        assert process_one(service, since, debounce_seconds=0)
        repo.tickets.update_one({'_id':ticket['_id']}, {'$set':{'automation.next_at':since}})
    assert not process_one(service, since, debounce_seconds=0)
    assert repo.get(ticket['_id'])['automation']['failures'] == 3
    repo.receive(ticket['chat_id'], 2, 'Cliente', 'Nueva consulta.')
    assert process_one(service, since, debounce_seconds=0)


def test_stop_during_generation_does_not_send(service, repo, telegram, ticket):
    import threading
    stop = threading.Event()
    service.responder = Responder(stop.set)
    assert not process_one(service, now() - timedelta(minutes=1), debounce_seconds=0, stop=stop)
    assert telegram.calls == []
