import secrets
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from datetime import timezone
from zoneinfo import ZoneInfo

from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from pymongo.errors import PyMongoError
from werkzeug.utils import secure_filename

from .repository import ConflictError
from .prompts import review_prompt
from .service import catalog
from .telegram import DeliveryUncertain, TelegramRejected
from .documents import EXTENSIONS, MAX_FILE_BYTES, read_document

STATUSES = {"pendiente": "Pendiente", "revision": "Para revisar", "respondida": "Respondida", "cerrada": "Cerrada"}
PRIORITIES = {"alta": "Alta", "media": "Media", "baja": "Baja"}


def create_app(service, secret_key):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=secret_key, MAX_CONTENT_LENGTH=24_000,
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
        TRUSTED_HOSTS=["127.0.0.1", "localhost"],
    )

    @app.before_request
    def local_requests():
        if request.remote_addr not in ("127.0.0.1", "::1"):
            abort(403)
        if request.endpoint == "knowledge_upload":
            request.max_content_length = MAX_FILE_BYTES + 128_000
        if request.method == "POST":
            expected = session.get("csrf", "")
            supplied = request.form.get("csrf", "")
            if not expected or not secrets.compare_digest(expected, supplied):
                abort(400, description="La sesión cambió. Actualiza la página e intenta otra vez.")

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; "
            "img-src 'self' data:; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
        )
        return response

    @app.context_processor
    def common():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return {"demo": service.demo, "statuses": STATUSES, "priorities": PRIORITIES,
                "batch_prompt": review_prompt(), "ticket_prompt": review_prompt,
                "service_names": {s["id"]: s["name"] for s in catalog()["services"]},
                "csrf": session["csrf"], "counts": service.repo.counts()}

    @app.template_filter("localdate")
    def localdate(value):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(ZoneInfo("America/Mexico_City")).strftime("%d/%m · %H:%M")

    @app.get("/")
    def inbox():
        status = request.args.get("estado", "")
        if status and status not in STATUSES:
            abort(400)
        tickets = service.repo.list(status or None, limit=100)
        return render_template("inbox.html", tickets=tickets, selected_status=status)

    @app.get("/solicitudes/<ticket_id>")
    def detail(ticket_id):
        try:
            ticket = service.repo.get(ticket_id)
        except ValueError:
            abort(404)
        knowledge_warning = None
        if service.knowledge is not None and ticket.get("draft"):
            try:
                service.knowledge.check_draft((ticket.get("analysis") or {}).get("knowledge"))
            except ValueError as error:
                knowledge_warning = str(error)
        return render_template("detail.html", ticket=ticket, knowledge_warning=knowledge_warning)

    def current_revision():
        try:
            return int(request.form["revision"])
        except (ValueError, KeyError):
            abort(400)

    @app.post("/solicitudes/<ticket_id>/enviar")
    def send(ticket_id):
        revision = current_revision()
        if request.form.get("confirm") != "si":
            abort(400, description="Confirma que revisaste la respuesta y su destinatario.")
        try:
            service.send_approved(ticket_id, request.form.get("draft_id", ""), revision,
                                  request.form.get("text", ""))
            flash("Respuesta enviada y registrada en el historial.", "success")
        except (ValueError, TelegramRejected) as error:
            flash(str(error), "error")
        except DeliveryUncertain:
            flash("No sabemos si Telegram entregó la respuesta. El envío quedó bloqueado para evitar duplicados. Verifica con el destinatario antes de resolverlo.", "error")
        except PyMongoError:
            flash("No pudimos confirmar el registro en MongoDB. Actualiza la página y verifica el envío antes de volver a intentarlo.", "error")
        return redirect(url_for("detail", ticket_id=ticket_id))

    @app.post("/solicitudes/<ticket_id>/cerrar")
    def close(ticket_id):
        revision = current_revision()
        try:
            service.repo.close(ticket_id, revision)
            flash("Solicitud cerrada. Un nuevo mensaje del cliente la abrirá de nuevo.", "success")
        except ConflictError as error:
            flash(str(error), "error")
        return redirect(url_for("detail", ticket_id=ticket_id))

    @app.post("/solicitudes/<ticket_id>/resolver")
    def resolve(ticket_id):
        if service.demo:
            abort(400)
        resolution = request.form.get("resolution")
        if resolution not in ("enviado", "no_enviado") or request.form.get("confirm") != "si":
            abort(400)
        try:
            service.repo.resolve_send(ticket_id, request.form.get("attempt_id", ""), resolution == "enviado")
            flash("Verificación registrada. No se envió ningún mensaje al resolverla.", "success")
        except ConflictError as error:
            flash(str(error), "error")
        return redirect(url_for("detail", ticket_id=ticket_id))

    @app.get("/health")
    def health():
        return {"status": "ok", "demo": service.demo}

    @app.get("/guia")
    def guide():
        return render_template("guide.html")

    @app.get("/conocimiento")
    def knowledge():
        state = service.knowledge.status() if service.knowledge is not None else None
        query = request.args.get("consulta", "").strip()
        result, error = None, None
        if query and service.knowledge is not None:
            try:
                result = service.knowledge.search(query)
            except ValueError as exc:
                error = str(exc)
        return render_template("knowledge.html", knowledge=state, query=query, result=result, search_error=error)

    @app.post("/conocimiento/actualizar")
    def knowledge_index():
        if service.demo or service.knowledge is None:
            abort(400)
        try:
            state = service.knowledge.index()
            flash(f"Base actualizada: {len(state['documents'])} fuentes y {state['chunks']} fragmentos. "
                  f"Se reutilizaron {state['reused']} vectores.", "success")
        except ValueError as error:
            flash(str(error), "error")
        return redirect(url_for("knowledge"))

    @app.post("/conocimiento/importar")
    def knowledge_upload():
        if service.demo or service.knowledge is None:
            abort(400)
        upload = request.files.get("document")
        name = secure_filename(upload.filename or "") if upload else ""
        if not name or Path(name).suffix.lower() not in EXTENSIONS:
            flash("Selecciona un archivo .md, .txt o PDF con texto.", "error")
            return redirect(url_for("knowledge"))
        folder = service.knowledge.folder
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        if folder.is_symlink():
            abort(400)
        target = folder / name
        if target.is_symlink() or (target.exists() and request.form.get("replace") != "si"):
            flash("Ese nombre ya existe. Marca Reemplazar si deseas actualizar ese documento.", "error")
            return redirect(url_for("knowledge"))
        temporary = None
        try:
            with NamedTemporaryFile(dir=folder, prefix=".upload-", suffix=Path(name).suffix, delete=False) as stream:
                temporary = Path(stream.name)
                content = upload.stream.read(MAX_FILE_BYTES + 1)
                if len(content) > MAX_FILE_BYTES:
                    raise ValueError("El límite es de 10 MB por archivo.")
                stream.write(content)
            read_document(temporary, name)
            if target.exists():
                archive = service.knowledge.storage / "previous"
                archive.mkdir(exist_ok=True, mode=0o700)
                # Conserva una copia privada del documento reemplazado.
                (archive / (secrets.token_hex(12) + target.suffix)).write_bytes(target.read_bytes())
            os.replace(temporary, target)
            temporary = None
            state = service.knowledge.index()
            flash(f"{name} importado. La base contiene {state['chunks']} fragmentos consultables.", "success")
        except ValueError as error:
            flash(str(error) + " Si el archivo ya se guardó, pulsa Actualizar base para reintentar.", "error")
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return redirect(url_for("knowledge"))

    @app.errorhandler(413)
    def file_too_large(_error):
        return "El archivo supera el límite permitido de 10 MB. Vuelve atrás y elige uno más pequeño.", 413

    @app.errorhandler(PyMongoError)
    def database_error(_error):
        # No mostrar errores del driver: pueden incluir datos de la conexión.
        return "MongoDB no está disponible. Revisa la conexión y vuelve a intentar.", 503

    return app
