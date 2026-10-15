import pytest

from aiec.repository import ConflictError
from aiec.telegram import DeliveryUncertain, TelegramRejected


TEXT = 'Podemos orientarte con las opciones documentadas para tu impresión.'


def reply(service, ticket, analysis, text=TEXT):
    return service.respond_to_customer(ticket['_id'], ticket['revision'], analysis, text)


def test_claude_response_is_sent_without_any_approval(service, repo, telegram, ticket, analysis):
    receipt = reply(service, ticket, analysis)
    assert receipt['sent'] is True and receipt['already_sent'] is False
    assert telegram.calls == [(ticket['chat_id'], TEXT)]
    current = repo.get(ticket['_id'])
    assert current['status'] == 'respondida' and current['draft'] is None
    assert current['messages'][-1]['analysis']['evidence'] == analysis.evidence
    assert current['messages'][-1]['origin'] == 'claude_desktop'


def test_repeating_the_same_tool_call_returns_receipt_without_sending_again(service, telegram, ticket, analysis):
    first = reply(service, ticket, analysis)
    second = reply(service, ticket, analysis)
    assert second['sent'] and second['already_sent']
    assert second['telegram_message_id'] == first['telegram_message_id']
    assert len(telegram.calls) == 1
    with pytest.raises(ConflictError, match='ya tiene'):
        reply(service, ticket, analysis, 'Una respuesta diferente.')
    assert len(telegram.calls) == 1


def test_concurrent_completed_reply_cannot_be_overwritten_and_resent(service, repo, telegram, ticket, analysis, monkeypatch):
    original = repo.save_draft
    intercepted = False
    def competing_reply(*args, **kwargs):
        nonlocal intercepted
        if not intercepted:
            intercepted = True
            reply(service, ticket, analysis)
        return original(*args, **kwargs)
    monkeypatch.setattr(repo, 'save_draft', competing_reply)
    result = reply(service, ticket, analysis)
    assert result['already_sent'] is True
    assert len(telegram.calls) == 1


def test_new_message_before_reply_blocks_old_context(service, repo, telegram, ticket, analysis):
    repo.receive(ticket['chat_id'], 2, 'Cliente', 'Ahora necesito otra cantidad.')
    with pytest.raises(ConflictError, match='mensaje nuevo'):
        reply(service, ticket, analysis)
    assert telegram.calls == []


def test_new_message_during_delivery_preserves_sent_evidence_and_pending_message(service, repo, telegram, ticket, analysis):
    telegram.before_result = lambda: repo.receive(ticket['chat_id'], 2, 'Cliente', 'Quiero otra medida.')
    receipt = reply(service, ticket, analysis)
    assert receipt['sent'] is True
    current = repo.get(ticket['_id'])
    assert current['status'] == 'pendiente' and current['revision'] == 2
    assert current['analysis'] is None
    assert current['messages'][-1]['analysis']['evidence'] == analysis.evidence


def test_uncertain_delivery_is_never_duplicated_by_a_repeated_claude_call(service, repo, telegram, ticket, analysis):
    telegram.error = DeliveryUncertain('No confirmado')
    with pytest.raises(DeliveryUncertain):
        reply(service, ticket, analysis)
    telegram.error = None
    with pytest.raises(ConflictError):
        reply(service, ticket, analysis)
    assert len(telegram.calls) == 1
    assert repo.get(ticket['_id'])['delivery_state'] == 'incierto'


def test_rejected_delivery_can_be_retried_without_approval(service, telegram, ticket, analysis):
    telegram.error = TelegramRejected(429)
    with pytest.raises(TelegramRejected):
        reply(service, ticket, analysis)
    telegram.error = None
    assert reply(service, ticket, analysis)['sent'] is True


def test_money_without_business_evidence_cannot_be_sent_from_desktop(service, telegram, ticket, analysis):
    with pytest.raises(ValueError, match='importe'):
        reply(service, ticket, analysis, 'Cuesta $10.00 MXN.')
    assert telegram.calls == []


def test_transport_cannot_send_text_that_differs_from_its_validated_reply(service, telegram, drafted):
    with pytest.raises(ConflictError):
        service.send_response(drafted['_id'], drafted['draft']['id'], drafted['revision'], 'Un texto modificado sin validar.')
    assert telegram.calls == []
