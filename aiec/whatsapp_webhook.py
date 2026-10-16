"""Entrada pública aislada del panel privado: verificar, persistir y confirmar a Meta."""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import logging
import re
import secrets

from flask import Flask, request
from pymongo.errors import PyMongoError
from werkzeug.serving import WSGIRequestHandler

from .repository import now

logger = logging.getLogger(__name__)

class PrivateWebhookLog(WSGIRequestHandler):
    def log_request(self, code='-', size='-'):
        # La URL de verificación contiene un secreto; no registrar la consulta.
        self.log('info', 'WhatsApp webhook HTTP %s', code)


def create_whatsapp_webhook(service, settings):
    settings.validate_whatsapp(webhook=True)
    app = Flask('aiec.whatsapp_webhook')
    app.config.update(MAX_CONTENT_LENGTH=1_000_000)

    @app.after_request
    def private_headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.get('/webhook/whatsapp')
    def verify():
        supplied = request.args.get('hub.verify_token', '')
        challenge = request.args.get('hub.challenge', '')
        if (request.args.get('hub.mode') != 'subscribe' or not challenge or len(challenge)>1000
                or not secrets.compare_digest(supplied.encode(), settings.whatsapp_verify_token.encode())):
            return 'Verificación no válida.', 403
        return challenge, 200, {'Content-Type':'text/plain; charset=utf-8'}

    @app.post('/webhook/whatsapp')
    def receive():
        raw = request.get_data()
        signature = request.headers.get('X-Hub-Signature-256', '')
        expected = 'sha256='+hmac.new(settings.whatsapp_app_secret.encode(), raw, hashlib.sha256).hexdigest()
        if not re.fullmatch(r'sha256=[0-9a-f]{64}', signature) or not hmac.compare_digest(signature, expected):
            return {'error':'Firma no válida.'}, 401
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or data.get('object') != 'whatsapp_business_account':
            return {'error':'Evento no válido.'}, 400
        accepted = 0
        try:
            entries = data.get('entry', [])
            if not isinstance(entries, list):
                return {'error':'Evento no válido.'}, 400
            for entry in entries:
                if not isinstance(entry, dict) or str(entry.get('id')) != settings.whatsapp_business_account_id:
                    continue
                changes = entry.get('changes', [])
                if not isinstance(changes, list):
                    continue
                for change in changes:
                    if not isinstance(change, dict) or change.get('field') != 'messages':
                        continue
                    value = change.get('value')
                    if not isinstance(value, dict):
                        continue
                    metadata = value.get('metadata') or {}
                    if not isinstance(metadata, dict) or metadata.get('phone_number_id') != settings.whatsapp_phone_number_id:
                        continue
                    contacts = value.get('contacts', [])
                    names = {}
                    for contact in contacts if isinstance(contacts, list) else []:
                        if isinstance(contact, dict) and isinstance(contact.get('profile'), dict):
                            name = contact['profile'].get('name')
                            if isinstance(name, str) and isinstance(contact.get('wa_id'), str):
                                names[contact.get('wa_id')] = name[:200]
                    messages = value.get('messages', [])
                    for message in messages if isinstance(messages, list) else []:
                        normalized = normalize_message(message)
                        if normalized is None:
                            continue
                        mid, sender, text, stamp = normalized
                        service.repo.queue_whatsapp(settings.whatsapp_phone_number_id, mid, sender,
                            names.get(sender) or 'Cliente de WhatsApp', text, stamp)
                        accepted += 1
                    statuses = value.get('statuses', [])
                    for status in statuses if isinstance(statuses, list) else []:
                        if (not isinstance(status, dict) or not isinstance(status.get('id'), str)
                                or not status['id'].startswith('wamid.') or len(status['id']) > 512):
                            continue
                        service.repo.whatsapp_status(status['id'], status.get('status'), now())
            service.repo.state.update_one({'_id':'whatsapp_health'}, {'$set':{
                'status':'activo', 'last_event_at':now(),
            }}, upsert=True)
        except PyMongoError:
            # Meta reintentará; la bandeja duradera evita repetir los eventos ya guardados.
            return {'error':'No se confirmó la persistencia. Reintentar.'}, 503
        return {'received':True, 'messages':accepted}

    return app


def normalize_message(message):
    if not isinstance(message, dict) or message.get('type') != 'text':
        return None
    sender, mid = message.get('from'), message.get('id')
    body = message.get('text')
    text = body.get('body') if isinstance(body, dict) else None
    if (not isinstance(sender,str) or not re.fullmatch(r'\d{6,20}',sender)
            or not isinstance(mid,str) or not mid.startswith('wamid.') or len(mid)>512
            or not isinstance(text,str) or not text.strip() or len(text)>10000):
        return None
    try:
        stamp = datetime.fromtimestamp(int(message['timestamp']), tz=timezone.utc)
    except (ValueError, TypeError, KeyError, OverflowError, OSError):
        return None
    if stamp > now()+timedelta(minutes=5):
        return None
    return mid, sender, text.strip(), stamp


def process_inbox(service, stop):
    while not stop.is_set():
        try:
            processed = service.repo.process_whatsapp_inbox()
            stop.wait(.1 if processed else .5)
        except PyMongoError:
            stop.wait(5)
        except Exception:
            logger.error('La recepción de WhatsApp espera un reintento; se omiten los datos privados del error.')
            stop.wait(5)
