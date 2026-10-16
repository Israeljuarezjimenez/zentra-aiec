"""Configuración local: introducir credenciales sin publicarlas en el chat o consola."""
from getpass import getpass
import os
import secrets

from dotenv import dotenv_values, set_key


def configure(path, *, ask=input, ask_secret=getpass):
    values = dotenv_values(path)
    required = [
        ('WHATSAPP_ACCESS_TOKEN','Token de acceso de Meta: ',True),
        ('WHATSAPP_PHONE_NUMBER_ID','Phone Number ID: ',False),
        ('WHATSAPP_BUSINESS_ACCOUNT_ID','Identificador de la cuenta de WhatsApp: ',False),
        ('WHATSAPP_APP_SECRET','Secreto de la aplicación de Meta (no se muestra): ',True),
    ]
    updates = {}
    for key,label,hidden in required:
        value = (values.get(key) or (ask_secret(label) if hidden else ask(label))).strip()
        if not value or ('_ID' in key and not value.isdigit()) or (key=='WHATSAPP_APP_SECRET' and len(value)<16):
            raise ValueError('Dato de WhatsApp no válido.')
        updates[key] = value
    updates.update({'WHATSAPP_VERIFY_TOKEN':values.get('WHATSAPP_VERIFY_TOKEN') or secrets.token_urlsafe(32),
                    'WHATSAPP_GRAPH_VERSION':values.get('WHATSAPP_GRAPH_VERSION') or 'v26.0',
                    'WHATSAPP_WEBHOOK_PORT':values.get('WHATSAPP_WEBHOOK_PORT') or '5051',
                    'WHATSAPP_ENABLED':'true'})
    if not path.exists():
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        os.close(fd)
    for key,value in updates.items():
        set_key(path,key,value)
    path.chmod(0o600)
    print('WhatsApp configurado en el archivo privado. No se enviaron mensajes ni se habilitó la API de Claude.')
    print('Reinicia AIEC y abre http://127.0.0.1:5050/whatsapp para configurar el webhook en Meta.')
