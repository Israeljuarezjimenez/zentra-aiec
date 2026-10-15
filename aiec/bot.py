import logging
import threading

from pymongo.errors import PyMongoError

from .telegram import DeliveryUncertain, TelegramRejected
from .repository import now

logger = logging.getLogger(__name__)


def check_bot(telegram):
    me = telegram.get_me()
    if telegram.call("getWebhookInfo").get("url"):
        raise ValueError("Este bot ya tiene otra integración activa (webhook). Usa un bot nuevo de BotFather.")
    return me


def poll(service, stop: threading.Event):
    repo, telegram = service.repo, service.telegram
    failures = 0
    while not stop.is_set():
        try:
            offset = repo.offset(telegram.bot_id)
            updates = telegram.get_updates(offset)
            for update in updates:
                if stop.is_set():
                    break
                service.receive_update(update)
                repo.checkpoint(telegram.bot_id, update["update_id"] + 1)
            failures = 0
            repo.state.update_one({"_id": "bot_health"}, {"$set": {"status": "activo", 'checked_at': now()}}, upsert=True)
        except TelegramRejected as error:
            logger.warning("Telegram rechazó la recepción (código %s).", error.code)
            if error.code in (401, 409):
                repo.state.update_one({"_id": "bot_health"}, {"$set": {"status": "detenido"}}, upsert=True)
                logger.error("Revisa el token o cierra la otra instancia del bot y reinicia AIEC.")
                stop.set()
                return
            stop.wait(min(error.retry_after or 5, 60))
        except (DeliveryUncertain, PyMongoError):
            failures += 1
            logger.warning("Conexión interrumpida. Conservamos el punto de lectura y volveremos a intentar.")
            stop.wait(min(2 ** min(failures, 5), 30))
