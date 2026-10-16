import pytest

from aiec.repository import ConflictError
from aiec.service import Analysis, Service, validate_text
from aiec.telegram import DeliveryUncertain, TelegramRejected


def send(service, ticket):
    return service.send_response(ticket["_id"], ticket["draft"]["id"], ticket["revision"], ticket["draft"]["text"])


def test_replayed_message_does_not_duplicate_or_invalidate_draft(repo, drafted):
    ticket_id, inserted = repo.receive(drafted["chat_id"], 1, "Lucía", "Quiero volantes. Tengo $15,000 para el pedido.")
    current = repo.get(ticket_id)
    assert inserted is False
    assert current["revision"] == 1
    assert len(current["messages"]) == 1
    assert current["draft"]["id"] == drafted["draft"]["id"]


def test_new_message_invalidates_prepared_response(service, repo, telegram, drafted):
    repo.receive(drafted["chat_id"], 2, "Lucía", "Mi presupuesto cambió.")
    with pytest.raises(ConflictError):
        send(service, drafted)
    current = repo.get(drafted["_id"])
    assert current["revision"] == 2
    assert current["status"] == "pendiente"
    assert current["draft"] is None
    assert current["analysis"] is None
    assert telegram.calls == []


def test_old_context_cannot_save_analysis(service, repo, analysis, ticket):
    repo.receive(ticket["chat_id"], 2, "Lucía", "Necesito otro servicio.")
    with pytest.raises(ConflictError):
        service.save_draft(ticket["_id"], 1, analysis, "Hola")


def test_analysis_requires_exact_customer_evidence(service, analysis, ticket):
    analysis.evidence = ["Presupuesto ilimitado"]
    with pytest.raises(ValueError, match="cita exacta"):
        service.save_draft(ticket["_id"], 1, analysis, "Hola")


def test_analysis_rejects_unknown_fields_and_priority():
    with pytest.raises(ValueError):
        Analysis(service="inventado", summary="algo", priority="urgente", priority_reason="algo", evidence=["algo"])


def test_same_prepared_response_only_sends_once(service, repo, telegram, drafted):
    send(service, drafted)
    with pytest.raises(ConflictError):
        send(service, drafted)
    assert len(telegram.calls) == 1
    current = repo.get(drafted["_id"])
    assert current["status"] == "respondida"
    assert current["draft"] is None
    assert current["messages"][-1]["role"] == "asistente"


def test_changed_draft_cannot_be_sent_from_old_tab(service, analysis, telegram, drafted):
    service.save_draft(drafted["_id"], 1, analysis, "Nueva propuesta")
    with pytest.raises(ConflictError):
        send(service, drafted)
    assert telegram.calls == []


def test_network_timeout_blocks_retry_until_manual_resolution(service, repo, telegram, drafted):
    telegram.error = DeliveryUncertain("Sin confirmación")
    with pytest.raises(DeliveryUncertain):
        send(service, drafted)
    with pytest.raises(ConflictError):
        send(service, drafted)
    current = repo.get(drafted["_id"])
    assert current["delivery_state"] == "incierto"
    assert len(telegram.calls) == 1
    repo.resolve_send(current["_id"], current["attempt"]["id"], was_sent=True)
    assert repo.get(current["_id"])["status"] == "respondida"
    assert len(telegram.calls) == 1


def test_confirmed_rejection_can_be_retried(service, repo, telegram, drafted):
    telegram.error = TelegramRejected(429, retry_after=5)
    with pytest.raises(TelegramRejected):
        send(service, drafted)
    assert repo.get(drafted["_id"])["delivery_state"] == "rechazado"
    telegram.error = None
    send(service, drafted)
    assert repo.get(drafted["_id"])["status"] == "respondida"


def test_message_arriving_during_send_stays_pending(service, repo, telegram, drafted):
    telegram.before_result = lambda: repo.receive(drafted["chat_id"], 2, "Lucía", "Tengo una pregunta adicional.")
    send(service, drafted)
    current = repo.get(drafted["_id"])
    assert current["revision"] == 2
    assert current["status"] == "pendiente"
    assert current["draft"] is None
    assert any(m["text"] == "Tengo una pregunta adicional." for m in current["messages"])
    assert current["messages"][-1]["role"] == "asistente"


def test_repeated_delivery_confirmation_does_not_duplicate_history(service, repo, drafted):
    send(service, drafted)
    current = repo.get(drafted["_id"])
    repo.finish_send(current["_id"], current["attempt"]["id"], 900)
    assert len(repo.get(current["_id"])["messages"]) == 2


def test_delivery_state_and_draft_are_committed_in_one_write(service, repo, drafted, monkeypatch):
    from pymongo.errors import AutoReconnect
    original = repo.tickets.update_one
    def write_then_lose_confirmation(*args, **kwargs):
        result = original(*args, **kwargs)
        if args[1].get("$set", {}).get("delivery_state") == "enviado":
            raise AutoReconnect("Se perdió la respuesta después de guardar.")
        return result
    monkeypatch.setattr(repo.tickets, "update_one", write_then_lose_confirmation)
    with pytest.raises(AutoReconnect):
        send(service, drafted)
    current = repo.get(drafted["_id"])
    assert current["status"] == "respondida"
    assert current["draft"] is None
    with pytest.raises(ConflictError):
        send(service, drafted)


def test_closed_ticket_reopens_when_customer_writes(repo, ticket):
    repo.close(ticket["_id"], 1)
    assert repo.get(ticket["_id"])["status"] == "cerrada"
    repo.receive(ticket["chat_id"], 2, "Lucía", "Hola otra vez")
    assert repo.get(ticket["_id"])["status"] == "pendiente"


def test_offsets_survive_restart_and_are_separate_per_bot(repo):
    repo.checkpoint("bot-1", 100)
    repo.checkpoint("bot-1", 5)
    repo.checkpoint("bot-2", 7)
    assert repo.offset("bot-1") == 100
    assert repo.offset("bot-2") == 7


def test_ignores_non_text_and_group_updates(service, repo):
    for message in [{"chat": {"type": "group"}, "text": "Hola"},
                    {"chat": {"type": "private"}, "photo": []}]:
        assert service.receive_update({"message": message}) is None
    assert repo.tickets.count_documents({}) == 0


def test_history_is_bounded_but_revision_keeps_count(repo, ticket):
    for message_id in range(2, 105):
        repo.receive(ticket["chat_id"], message_id, "Lucía", "Mensaje")
    current = repo.get(ticket["_id"])
    assert len(current["messages"]) == 100
    assert current["revision"] == 104


def test_demo_refuses_real_send(repo, telegram, drafted):
    demo = Service(repo, telegram, demo=True)
    with pytest.raises(ValueError, match="demo no envía"):
        send(demo, drafted)
    assert telegram.calls == []


def test_empty_and_too_long_messages_are_rejected():
    for text in ("   ", "a" * 4001, "😀" * 2001):
        with pytest.raises(ValueError):
            validate_text(text)
