"""Reservar el puerto del panel antes de empezar a recibir mensajes reales."""
import threading
from datetime import datetime

from werkzeug.serving import make_server

from .bot import check_bot, poll
from .config import ConfigurationError
from .web import create_app
from .automation import run as run_automation
from .repository import now
from pymongo import ReturnDocument


def serve(service, settings):
    app = create_app(service, settings.secret_key)
    try:
        server = make_server("127.0.0.1", settings.port, app, threaded=True)
    except SystemExit as error:
        if error.code != 1:
            raise
        raise ConfigurationError(
            f"No se pudo abrir el puerto {settings.port}. Cierra el panel o la demo que ya esté abierta "
            "con Control+C y vuelve a iniciar AIEC. El receptor de Telegram no se inició."
        ) from None

    stop = threading.Event()
    try:
        if service.demo:
            print("DEMOSTRACIÓN: conversaciones de ejemplo en memoria y catálogo con referencias reales. Sin conexión a Claude, Telegram ni Atlas.", flush=True)
        else:
            me = check_bot(service.telegram)
            service.repo.state.update_one({'_id': 'bot_health'}, {'$set': {
                'status': 'activo', 'checked_at': now(),
            }}, upsert=True)
            if service.responder is not None:
                knowledge = service.knowledge.status() if service.knowledge else {}
                if not knowledge.get('ready') or knowledge.get('stale'):
                    raise ConfigurationError("Prepara el catálogo con .venv/bin/python -m aiec rag-index antes de iniciar.")
                started_at = now()
                activation = service.repo.state.find_one_and_update(
                    {"_id": "automation_activation_imprenta"},
                    {"$setOnInsert": {"since": started_at, "since_iso": started_at.isoformat()}}, upsert=True,
                    return_document=ReturnDocument.AFTER,
                )
            print(f"Bot preparado: @{me['username']}. Solo atendemos mensajes privados de texto.", flush=True)
            worker = threading.Thread(target=poll, args=(service, stop), daemon=True, name="telegram-poller")
            worker.start()
            if service.responder is not None:
                automation = threading.Thread(target=run_automation,
                    args=(service, stop, datetime.fromisoformat(activation.get('since_iso') or activation['since'].isoformat())),
                    daemon=True, name="automatic-replies")
                automation.start()
                print("Atención automática con Claude API y RAG. Sin aprobación manual.", flush=True)
            else:
                service.repo.state.update_one({"_id": "automation"}, {"$set": {
                    "status": "desactivado", "checked_at": now(),
                }}, upsert=True)
                print("Envío directo desde Claude Desktop: responder_cliente entrega por Telegram sin aprobación en el panel.", flush=True)
        print(f"Abre http://127.0.0.1:{settings.port} · Detener: Control+C", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
