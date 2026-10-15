"""Cola automática: recibir, recuperar fuentes, preparar y enviar sin aprobación manual."""
import logging

from pymongo.errors import PyMongoError

from .repository import ConflictError, now
from .telegram import DeliveryUncertain, TelegramRejected

logger = logging.getLogger(__name__)


def process_one(service, since, *, debounce_seconds=2, stop=None):
    ticket = service.repo.claim_next(since, debounce_seconds=debounce_seconds)
    if ticket is None:
        return False
    claim_id, error = ticket["processing"]["id"], None
    try:
        if service.demo or service.responder is None:
            raise ValueError("El motor automático no está configurado.")
        result = service.responder.prepare(service, ticket)
        if stop is not None and stop.is_set():
            return False
        current = service.repo.get(ticket["_id"])
        if current.get("processing", {}).get("id") != claim_id:
            raise ConflictError("La generación ya no tiene la reserva de esta conversación.")
        service.respond_to_customer(ticket['_id'], ticket['revision'], result.analysis, result.text,
            rag_context_id=result.context_id, sources=result.sources, origin='claude_api')
    except ConflictError:
        # Un mensaje nuevo vuelve a la cola con la revisión actual; no enviar texto viejo.
        pass
    except DeliveryUncertain:
        error = "Telegram no confirmó la entrega. No se repetirá el envío automáticamente."
    except TelegramRejected as exc:
        error = f"Telegram rechazó el envío (código {exc.code})."
    except (ValueError, OSError):
        # No guardar mensajes del proveedor o URLs que puedan contener datos privados.
        error = "No se pudo preparar una respuesta válida. Revisa el motor y las fuentes."
    except Exception:
        error = "El procesamiento se interrumpió. No se enviará una respuesta sin validar."
        logger.error("Falla de procesamiento; se conserva el límite de reintentos y se omiten datos privados.")
    finally:
        service.repo.release_claim(ticket["_id"], claim_id, revision=ticket["revision"], error=error)
    return True


def run(service, stop, since):
    while not stop.is_set():
        try:
            processed = process_one(service, since, stop=stop)
            service.repo.state.update_one({"_id": "automation"}, {"$set": {
                "status": "activo", "checked_at": now(),
            }}, upsert=True)
            stop.wait(.3 if processed else 1)
        except PyMongoError:
            logger.warning("La cola automática espera la reconexión con MongoDB.")
            stop.wait(5)
        except Exception:
            # Conservar el proceso y la reserva temporal ante una falla inesperada.
            logger.error("La atención automática tuvo una falla; no se muestran datos privados.")
            stop.wait(5)
