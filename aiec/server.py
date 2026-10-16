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
            "con Control+C y vuelve a iniciar AIEC. No se iniciaron receptores de mensajes."
        ) from None

    stop = threading.Event()
    whatsapp_server = None
    try:
        if service.demo:
            print("DEMOSTRACIÓN: conversaciones de ejemplo en memoria y catálogo con referencias reales. Sin conexión a Claude, Telegram ni Atlas.", flush=True)
        else:
            if settings.whatsapp_enabled:
                from .whatsapp_webhook import create_whatsapp_webhook, PrivateWebhookLog, process_inbox
                settings.validate_whatsapp(webhook=True)
                # El túnel público apunta solo a este puerto; nunca al panel con datos de clientes.
                try:
                    whatsapp_server = make_server('127.0.0.1', settings.whatsapp_webhook_port,
                        create_whatsapp_webhook(service, settings), threaded=True, request_handler=PrivateWebhookLog)
                except SystemExit:
                    raise ConfigurationError('El puerto del webhook de WhatsApp está ocupado.') from None
            me = check_bot(service.telegram) if service.telegram is not None else None
            if me is None and whatsapp_server is None:
                raise ConfigurationError('Activa Telegram o WhatsApp para recibir mensajes.')
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
            if me is not None:
                print(f"Bot preparado: @{me['username']}. Solo atendemos mensajes privados de texto.", flush=True)
                worker = threading.Thread(target=poll, args=(service, stop), daemon=True, name="telegram-poller")
                worker.start()
            if whatsapp_server is not None:
                threading.Thread(target=whatsapp_server.serve_forever, daemon=True, name='whatsapp-webhook').start()
                threading.Thread(target=process_inbox, args=(service,stop), daemon=True, name='whatsapp-inbox').start()
                print(f'WhatsApp: webhook aislado en 127.0.0.1:{settings.whatsapp_webhook_port}/webhook/whatsapp.',flush=True)
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
                print("Envío directo desde Claude Desktop: responder_cliente utiliza el canal de cada conversación, sin aprobación en el panel.", flush=True)
        print(f"Abre http://127.0.0.1:{settings.port} · Detener: Control+C", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        if whatsapp_server is not None:
            # No llamar shutdown antes de iniciar serve_forever: bloquearía la limpieza en un error inicial.
            if any(t.name == 'whatsapp-webhook' and t.is_alive() for t in threading.enumerate()):
                whatsapp_server.shutdown()
            whatsapp_server.server_close()
        server.server_close()
