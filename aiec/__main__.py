import argparse
import json
import logging
import os
from pathlib import Path
import secrets
import sys

from dotenv import dotenv_values, set_key
from pymongo.errors import PyMongoError

from .config import ConfigurationError, ROOT, Settings


def setup():
    env_file = ROOT / ".env"
    if not env_file.exists():
        # Crear con permisos privados desde el primer momento.
        descriptor = os.open(env_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write((ROOT / ".env.example").read_text(encoding="utf-8"))
    if not dotenv_values(env_file).get("FLASK_SECRET_KEY"):
        set_key(env_file, "FLASK_SECRET_KEY", secrets.token_urlsafe(48))
    env_file.chmod(0o600)
    runtime = ROOT / ".runtime"
    runtime.mkdir(mode=0o700, exist_ok=True)
    (runtime / "claude_config.json").write_text(json.dumps(claude_config(), indent=2) + "\n", encoding="utf-8")
    print("Archivo privado .env preparado. Completa TELEGRAM_BOT_TOKEN y MONGODB_URI.")
    print("Configuración de Claude preparada en .runtime/claude_config.json (no contiene contraseñas).")


def claude_config():
    python = ROOT / ".venv" / "bin" / "python"
    return {"mcpServers": {"aiec": {
        "command": str(python), "args": [str(ROOT / "serve_mcp.py")],
    }}}


def configure_logs():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s", stream=sys.stderr)
    # httpx incluye la URL del bot en INFO; el token no debe aparecer en registros.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("pymongo").setLevel(logging.WARNING)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Impresos San Martín · WhatsApp, Telegram, RAG y Claude.")
    parser.add_argument("command", choices=["setup", "doctor", "run", "demo", "mcp", "claude-config", 'migrate-business', 'sync-telegram-profile',
                                            "load-examples", "rag-index", "rag-search", "rag-status", "rag-eval", 'whatsapp-check','configure-whatsapp','whatsapp-tunnel','whatsapp-connect'])
    parser.add_argument("query", nargs="?", help="Texto de búsqueda para rag-search.")
    parser.add_argument("--demo", action="store_true", help="Conversaciones de ejemplo en memoria; no envía mensajes.")
    parser.add_argument('--since', help='Inicio de la imprenta en ISO 8601 con zona horaria; solo para migrar historial.')
    parser.add_argument('--renew-token', action='store_true', help='Solicita un token nuevo en el configurador privado de WhatsApp.')
    args = parser.parse_args(argv)
    if args.renew_token and args.command != 'configure-whatsapp':
        parser.error('--renew-token se utiliza con configure-whatsapp.')
    configure_logs()
    if args.command == "setup":
        setup()
        return 0
    if args.command == "claude-config":
        print(json.dumps(claude_config(), ensure_ascii=False, indent=2))
        return 0
    if args.command == 'configure-whatsapp':
        from .whatsapp_setup import configure
        try:
            configure(ROOT / '.env', renew_token=args.renew_token)
            return 0
        except (ValueError, OSError):
            print('No se guardó una configuración válida de WhatsApp. Revisa los datos introducidos.',file=sys.stderr)
            return 1
    if args.command == 'whatsapp-tunnel':
        from .whatsapp_tunnel import ensure_tunnel
        try:
            settings = Settings.load()
            result = ensure_tunnel(ROOT / '.runtime' / 'whatsapp', settings.whatsapp_webhook_port, settings.port)
            print('Dirección HTTPS para la demo: '+result['callback_url'])
            print('Publica únicamente el receptor de WhatsApp. Para recibir mensajes aún deben estar configurados el secreto y la suscripción en Meta.')
            print('La dirección dura mientras el túnel esté abierto; cambia si creas otro.')
            return 0
        except (ValueError, OSError):
            print('No se pudo preparar el túnel. Comprueba cloudflared y la conexión a internet; consulta los registros privados.',file=sys.stderr)
            return 1

    from .bot import check_bot
    from .repository import Repository
    from .service import Service, catalog
    from .telegram import DeliveryUncertain, Telegram, TelegramRejected

    try:
        if args.command == "load-examples":
            from .demo_documents import load_examples
            result = load_examples()
            print(f"Ejemplos copiados: {result['copied']} · existentes conservados: {result['skipped']}.")
            print("Para preparar la búsqueda: .venv/bin/python -m aiec rag-index")
            return 0
        from .rag import KnowledgeBase
        if args.command.startswith("rag-"):
            knowledge = KnowledgeBase()
            if args.command == "rag-index":
                result = knowledge.index()
                count = len(result["documents"])
                print(f"✓ Base actualizada: {count} {'fuente' if count == 1 else 'fuentes'} y {result['chunks']} fragmentos.")
                print(f"Vectores nuevos: {result['embedded']} · reutilizados: {result['reused']}.")
            elif args.command == "rag-status":
                print(json.dumps(knowledge.status(), ensure_ascii=False, indent=2))
            elif args.command == "rag-eval":
                from .rag_evaluation import evaluate
                report = evaluate(knowledge.embedder, knowledge.reranker)
                print(json.dumps(report, ensure_ascii=False, indent=2))
                return 0 if report["passed"] else 1
            else:
                if not args.query:
                    parser.error('Ejemplo: python -m aiec rag-search "precio de tarjetas de presentación"')
                print(json.dumps(knowledge.search(args.query), ensure_ascii=False, indent=2))
            return 0
        settings = Settings.load()
        if args.command == 'whatsapp-connect':
            from .whatsapp_connect import connect
            result = connect(settings, ROOT / '.runtime' / 'whatsapp')
            print('✓ Receptor público verificado y registrado en Meta; cuenta de WhatsApp suscrita.')
            print('✓ AIEC reiniciado. No se enviaron mensajes a clientes ni se utilizó la API de Claude.')
            print('Dirección para la demo: '+result['callback_url'])
            print('Claude Desktop recogerá el canal al utilizar las herramientas AIEC. Si acabas de actualizar el código, reinícialo una vez.')
            return 0
        if args.command == 'migrate-business':
            from datetime import datetime
            from .migration import migrate_business
            if not args.since:
                parser.error('migrate-business requiere --since con la fecha real del cambio de negocio.')
            since = datetime.fromisoformat(args.since)
            repo = Repository.connect(settings)
            try:
                result = migrate_business(repo, since, ROOT / '.runtime' / 'business-backups')
                print(json.dumps(result, ensure_ascii=False))
                return 1 if result['conflicts'] or result['blocked'] else 0
            finally:
                repo.database.client.close()
        if args.command == "demo" or (args.command == "mcp" and args.demo):
            from .demo import demo_service
            service = demo_service()
        else:
            needs_telegram = args.command == 'sync-telegram-profile' or (
                settings.telegram_enabled and args.command in ('run','doctor','mcp'))
            settings.validate(telegram=needs_telegram, web=args.command == "run")
            whatsapp = None
            if settings.whatsapp_enabled or args.command == 'whatsapp-check':
                from .whatsapp import WhatsApp
                settings.validate_whatsapp(webhook=settings.whatsapp_enabled and args.command == 'run')
                whatsapp = WhatsApp(settings.whatsapp_access_token, settings.whatsapp_phone_number_id,
                    graph_version=settings.whatsapp_graph_version)
            repo = Repository.connect(settings)
            service = Service(repo, Telegram(settings.bot_token) if needs_telegram else None,
                              knowledge=KnowledgeBase(), whatsapp=whatsapp, settings=settings)
            if args.command == "run":
                from .responses import configured_responder
                service.responder = configured_responder(settings)

        if args.command == 'whatsapp-check':
            try:
                number = service.whatsapp.check(settings.whatsapp_business_account_id)
                from .repository import now
                service.repo.state.update_one({'_id':'whatsapp_config'}, {'$set':{
                    'credentials_checked_at':now(), 'credentials_valid':True,
                    'number_name':number.get('verified_name',''),
                }}, upsert=True)
                print('✓ Token de Meta válido; número vinculado a la cuenta de WhatsApp configurada.')
                print('✓ Comprobación de solo lectura: no se enviaron mensajes.')
                print('Webhook: listo para configurar.' if settings.whatsapp_app_secret else
                      'Webhook pendiente: guarda WHATSAPP_APP_SECRET en .env y configura la dirección HTTPS en Meta.')
                return 0
            finally:
                repo.database.client.close()

        if args.command == 'sync-telegram-profile':
            from .telegram_profile import sync_telegram_profile
            try:
                print(json.dumps(sync_telegram_profile(service.telegram, ROOT / '.runtime' / 'business-backups'), ensure_ascii=False))
                return 0
            finally:
                repo.database.client.close()

        if args.command == "doctor":
            print("✓ Conexión con MongoDB e índices disponibles.")
            if service.telegram is not None:
                me = check_bot(service.telegram)
                print(f"✓ Bot de Telegram válido: @{me['username']}.")
            if service.whatsapp is not None:
                service.whatsapp.check(settings.whatsapp_business_account_id)
                print('✓ Conexión de WhatsApp válida.')
            state = service.knowledge.status()
            print("Claude automático: configurado; sin probar consumo de API." if settings.auto_reply_enabled and settings.anthropic_api_key else
                  "✓ Envío directo desde Claude Desktop por MCP, sin API de pago. Generación en segundo plano: desactivada.")
            print("✓ Conocimiento actualizado." if state['ready'] and not state['stale'] else
                  "Prepara el conocimiento con: .venv/bin/python -m aiec rag-index")
            print('Negocio: ' + catalog()['business'] + '. Atención con referencias externas; cotización propia no habilitada.')
            unscoped = service.repo.tickets.count_documents({'business_id': {'$exists': False}})
            if unscoped:
                print(f'Hay {unscoped} conversaciones anteriores sin clasificar. Usa migrate-business con la fecha real del cambio antes de atenderlas.')
            print("Listo para ejecutar: .venv/bin/python -m aiec run")
            return 0

        if args.command == "mcp":
            from .mcp_server import create_mcp
            if not service.demo:
                from .whatsapp_setup import refresh_mcp_channel
                service.channel_refresh = refresh_mcp_channel
            create_mcp(service).run(transport="stdio")
            return 0

        from dataclasses import replace
        from .server import serve
        serve(service, replace(settings, secret_key=settings.secret_key or secrets.token_urlsafe(48)))
        return 0
    except ConfigurationError as error:
        print(f"Configuración pendiente: {error}", file=sys.stderr)
    except PyMongoError:
        print("No pudimos conectar con MongoDB. Revisa la conexión, usuario, contraseña y Network Access de Atlas. No se muestran credenciales.", file=sys.stderr)
    except TelegramRejected as error:
        print(str(error)+' Revisa la configuración del canal.', file=sys.stderr)
    except DeliveryUncertain:
        print("No pudimos confirmar la conexión del canal. Revisa tu conexión a internet.", file=sys.stderr)
    except ValueError as error:
        print(str(error), file=sys.stderr)
    except OSError:
        print("No se pudo iniciar el servicio. Revisa los permisos y que el puerto del panel esté libre.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
