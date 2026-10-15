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
    parser = argparse.ArgumentParser(description="Impresos San Martín · Telegram, RAG y Claude.")
    parser.add_argument("command", choices=["setup", "doctor", "run", "demo", "mcp", "claude-config",
                                            "load-examples", "rag-index", "rag-search", "rag-status", "rag-eval"])
    parser.add_argument("query", nargs="?", help="Texto de búsqueda para rag-search.")
    parser.add_argument("--demo", action="store_true", help="Datos ficticios en memoria; solo para pruebas MCP.")
    args = parser.parse_args(argv)
    configure_logs()
    if args.command == "setup":
        setup()
        return 0
    if args.command == "claude-config":
        print(json.dumps(claude_config(), ensure_ascii=False, indent=2))
        return 0

    from .bot import check_bot
    from .repository import Repository
    from .service import Service
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
                    parser.error('Ejemplo: python -m aiec rag-search "campañas de publicidad"')
                print(json.dumps(knowledge.search(args.query), ensure_ascii=False, indent=2))
            return 0
        settings = Settings.load()
        if args.command == "demo" or (args.command == "mcp" and args.demo):
            from .demo import demo_service
            service = demo_service()
        else:
            settings.validate(telegram=args.command in ("run", "doctor", "mcp"), web=args.command == "run")
            repo = Repository.connect(settings)
            service = Service(repo, Telegram(settings.bot_token) if args.command in ("run", "doctor", "mcp") else None,
                              knowledge=KnowledgeBase())
            if args.command == "run":
                from .responses import configured_responder
                service.responder = configured_responder(settings)

        if args.command == "doctor":
            print("✓ Conexión con MongoDB e índices disponibles.")
            me = check_bot(service.telegram)
            print(f"✓ Bot de Telegram válido: @{me['username']}.")
            state = service.knowledge.status()
            print("Claude automático: configurado; sin probar consumo de API." if settings.auto_reply_enabled and settings.anthropic_api_key else
                  "✓ Envío directo desde Claude Desktop por MCP, sin API de pago. Generación en segundo plano: desactivada.")
            print("✓ Conocimiento actualizado." if state['ready'] and not state['stale'] else
                  "Prepara el conocimiento con: .venv/bin/python -m aiec rag-index")
            print("Listo para ejecutar: .venv/bin/python -m aiec run")
            return 0

        if args.command == "mcp":
            from .mcp_server import create_mcp
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
        print(f"Telegram rechazó la conexión (código {error.code}). Revisa el token y que no haya otro programa usando el bot.", file=sys.stderr)
    except DeliveryUncertain:
        print("No pudimos confirmar la conexión con Telegram. Revisa tu conexión a internet.", file=sys.stderr)
    except ValueError as error:
        print(str(error), file=sys.stderr)
    except OSError:
        print("No se pudo iniciar el servicio. Revisa los permisos y que el puerto del panel esté libre.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
