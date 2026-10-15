import httpx
import pytest

from aiec.telegram import DeliveryUncertain, Telegram, TelegramRejected

TOKEN = "123456:TEST_ONLY_NOT_A_VALID_TOKEN"


def test_polling_parameters_and_plain_text_send():
    payloads = []
    def handler(request):
        import json
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": []})
    bot = Telegram(TOKEN, httpx.Client(transport=httpx.MockTransport(handler)))
    bot.get_updates(50)
    bot.send_message(8000000000001, "Texto sin formato")
    assert payloads[0] == {"offset": 50, "timeout": 25, "limit": 50, "allowed_updates": ["message"]}
    assert "parse_mode" not in payloads[1]
    assert payloads[1]["chat_id"] == 8000000000001


def test_network_errors_do_not_reveal_bot_token():
    def handler(request):
        raise httpx.ReadTimeout(f"private URL: {request.url}")
    bot = Telegram(TOKEN, httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(DeliveryUncertain) as error:
        bot.send_message(1, "Hola")
    assert TOKEN not in str(error.value)


def test_rejections_are_distinct_from_uncertain_server_errors():
    bot = Telegram(TOKEN, httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(429, json={"ok": False, "error_code": 429, "parameters": {"retry_after": 7}}))))
    with pytest.raises(TelegramRejected) as error:
        bot.send_message(1, "Hola")
    assert error.value.retry_after == 7
    bot = Telegram(TOKEN, httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(502, text="Bad Gateway"))))
    with pytest.raises(DeliveryUncertain):
        bot.send_message(1, "Hola")
