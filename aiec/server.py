"""Reservar el puerto del panel antes de empezar a recibir mensajes reales."""
import threading

from werkzeug.serving import make_server

from .bot import check_bot, poll
from .config import ConfigurationError
from .web import create_app


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
            print("DEMO: datos ficticios en memoria. Sin conexión a Claude, Telegram ni Atlas.", flush=True)
        else:
            me = check_bot(service.telegram)
            print(f"Bot preparado: @{me['username']}. Solo atendemos mensajes privados de texto.", flush=True)
            worker = threading.Thread(target=poll, args=(service, stop), daemon=True, name="telegram-poller")
            worker.start()
        print(f"Abre http://127.0.0.1:{settings.port} · Detener: Control+C", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
