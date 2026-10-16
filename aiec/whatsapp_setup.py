"""Configuración local: introducir credenciales sin publicarlas en el chat o consola."""
from getpass import getpass
from dataclasses import replace
import os
import secrets

from dotenv import dotenv_values, set_key
from filelock import FileLock

from .config import ConfigurationError, ROOT


def refresh_mcp_channel(service):
    """Recoger el canal recién configurado sin cerrar la conversación de Claude."""
    if service.demo or service.settings is None:
        return
    values = dotenv_values(ROOT / '.env')
    if values.get('WHATSAPP_ENABLED', '').strip().lower() != 'true':
        service.whatsapp = None
        return
    candidate = replace(service.settings, whatsapp_enabled=True,
        **{name:values.get(name.upper(), '') or '' for name in (
            'whatsapp_access_token','whatsapp_phone_number_id','whatsapp_business_account_id',
            'whatsapp_app_secret','whatsapp_verify_token','whatsapp_graph_version')})
    try:
        candidate.validate_whatsapp(webhook=True)
    except ConfigurationError:
        service.whatsapp = None
        return
    old = service.settings
    if service.whatsapp is None or any(getattr(old,k)!=getattr(candidate,k) for k in (
            'whatsapp_access_token','whatsapp_phone_number_id','whatsapp_graph_version')):
        from .whatsapp import WhatsApp
        service.whatsapp = WhatsApp(candidate.whatsapp_access_token, candidate.whatsapp_phone_number_id,
                                   graph_version=candidate.whatsapp_graph_version)
    service.settings = candidate


def configure(path, *, ask=input, ask_secret=getpass, renew_token=False):
    before = path.read_bytes() if path.exists() else None
    values = dotenv_values(path)
    required = [
        ('WHATSAPP_ACCESS_TOKEN','Token de acceso de Meta: ',True),
        ('WHATSAPP_PHONE_NUMBER_ID','Phone Number ID: ',False),
        ('WHATSAPP_BUSINESS_ACCOUNT_ID','Identificador de la cuenta de WhatsApp: ',False),
        ('WHATSAPP_APP_SECRET','Secreto de la aplicación de Meta (no se muestra): ',True),
    ]
    updates = {}
    for key,label,hidden in required:
        saved = None if renew_token and key == 'WHATSAPP_ACCESS_TOKEN' else values.get(key)
        value = (saved or (ask_secret(label) if hidden else ask(label))).strip()
        if not value or ('_ID' in key and not value.isdigit()) or (key=='WHATSAPP_APP_SECRET' and len(value)<16):
            raise ValueError('Dato de WhatsApp no válido.')
        updates[key] = value
    updates.update({'WHATSAPP_VERIFY_TOKEN':values.get('WHATSAPP_VERIFY_TOKEN') or secrets.token_urlsafe(32),
                    'WHATSAPP_GRAPH_VERSION':values.get('WHATSAPP_GRAPH_VERSION') or 'v26.0',
                    'WHATSAPP_WEBHOOK_PORT':values.get('WHATSAPP_WEBHOOK_PORT') or '5051',
                    'WHATSAPP_ENABLED':'true'})
    with FileLock(str(path)+'.lock', timeout=5):
        current = path.read_bytes() if path.exists() else None
        if current != before:
            raise ValueError('La configuración cambió mientras introducías los datos. Ábrela de nuevo; se conservaron los cambios recientes.')
        if not path.exists():
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            os.close(fd)
        for key,value in updates.items():
            set_key(path,key,value)
        path.chmod(0o600)
    print('WhatsApp configurado en el archivo privado. No se enviaron mensajes ni se habilitó la API de Claude.')
    print('Reinicia AIEC y abre http://127.0.0.1:5050/whatsapp para configurar el webhook en Meta.')
