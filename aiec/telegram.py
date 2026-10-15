"""Cliente pequeño de Telegram. No imprime URLs que contienen el token."""
import httpx


class TelegramRejected(RuntimeError):
    def __init__(self, code, retry_after=None):
        self.code = code
        self.retry_after = retry_after
        super().__init__(f"Telegram rechazó la operación (código {code}).")


class DeliveryUncertain(RuntimeError):
    pass


class Telegram:
    def __init__(self, token, client=None):
        self.bot_id = token.split(":", 1)[0]
        self._base = f"https://api.telegram.org/bot{token}"
        self.client = client or httpx.Client(timeout=httpx.Timeout(40, connect=10))

    def call(self, method, **payload):
        try:
            response = self.client.post(f"{self._base}/{method}", json=payload)
        except httpx.TransportError:
            raise DeliveryUncertain("No se confirmó la respuesta de Telegram. No reintentar el envío automáticamente.") from None
        if response.status_code >= 500:
            raise DeliveryUncertain("Telegram no confirmó el resultado de la operación.")
        try:
            data = response.json()
        except ValueError:
            raise DeliveryUncertain("Telegram devolvió una respuesta que no pudimos confirmar.") from None
        if not data.get("ok"):
            raise TelegramRejected(data.get("error_code", response.status_code),
                                   data.get("parameters", {}).get("retry_after"))
        return data["result"]

    def get_me(self):
        return self.call("getMe")

    def get_updates(self, offset):
        return self.call("getUpdates", offset=offset, timeout=25, limit=50, allowed_updates=["message"])

    def send_message(self, chat_id, text):
        return self.call("sendMessage", chat_id=chat_id, text=text,
                         link_preview_options={"is_disabled": True})
