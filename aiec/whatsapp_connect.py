"""Conectar la demo a Meta sin enviar mensajes a destinatarios."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import time

import httpx
from filelock import FileLock, Timeout

from .config import ROOT
from .whatsapp import WhatsApp
from .whatsapp_tunnel import ensure_tunnel


def private_json(path, data):
    descriptor = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    os.chmod(path, 0o600)
    with os.fdopen(descriptor, 'w') as output:
        json.dump(data, output, ensure_ascii=False, indent=2)


def restart_application(settings, folder):
    listeners = subprocess.run(['lsof', '-nP', f'-iTCP@127.0.0.1:{settings.port}',
        '-sTCP:LISTEN', '-t'], capture_output=True, text=True, timeout=5)
    pids = set(listeners.stdout.split())
    if len(pids) > 1 or any(not pid.isdigit() for pid in pids):
        raise ValueError('No se pudo identificar una única instancia de AIEC para reiniciarla.')
    for pid in pids:
        command = subprocess.check_output(['ps', '-p', pid, '-o', 'command='], text=True).strip()
        cwd = subprocess.check_output(['lsof', '-a', '-p', pid, '-d', 'cwd', '-Fn'], text=True)
        if not command.endswith('Python -m aiec run') or 'n'+str(ROOT)+'\n' not in cwd:
            raise ValueError('Otro programa ocupa el puerto del panel. No se detuvo ese programa.')
        os.kill(int(pid), signal.SIGINT)
    deadline = time.monotonic()+12
    while pids and time.monotonic() < deadline:
        active = subprocess.run(['lsof', '-nP', f'-iTCP@127.0.0.1:{settings.port}',
            '-sTCP:LISTEN', '-t'], capture_output=True, text=True, timeout=5)
        if not active.stdout.strip():
            break
        time.sleep(.2)
    else:
        if pids:
            raise ValueError('La instancia anterior sigue cerrando. No se inició una segunda.')
    path = Path(folder) / 'application.log'
    fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    os.chmod(path, 0o600)
    with os.fdopen(fd, 'ab') as output:
        process = subprocess.Popen([str(ROOT/'.venv/bin/python'), '-m', 'aiec', 'run'],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
            start_new_session=True)
    private_json(Path(folder)/'application-process.json', {'pid':process.pid})


def verify_callback(client, callback_url, verify_token, *, timeout=35):
    challenge = secrets.token_hex(16)
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        try:
            response = client.get(callback_url, params={'hub.mode':'subscribe',
                'hub.verify_token':verify_token, 'hub.challenge':challenge}, timeout=5)
            if response.status_code == 200 and response.text == challenge:
                return
        except httpx.HTTPError:
            pass
        time.sleep(.5)
    raise ValueError('La dirección pública no confirmó el receptor. No se registró un webhook sin verificar.')


def connect(settings, folder, **kwargs):
    folder = Path(folder)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    folder.chmod(0o700)
    try:
        with FileLock(str(folder/'connect.lock'), timeout=0):
            return _connect(settings, folder, **kwargs)
    except Timeout:
        raise ValueError('La conexión de WhatsApp ya está en proceso. Consulta el panel en un momento.') from None


def _connect(settings, folder, *, client=None, tunnel=ensure_tunnel, restart=restart_application,
            verify=verify_callback):
    settings.validate(telegram=settings.telegram_enabled, web=True)
    settings.validate_whatsapp(webhook=True)
    if not settings.whatsapp_enabled:
        raise ValueError('Primero guarda la configuración de WhatsApp con el configurador privado.')
    folder = Path(folder)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    folder.chmod(0o700)
    session = client or httpx.Client(timeout=httpx.Timeout(25, connect=10), trust_env=False,
                                     follow_redirects=False)
    try:
        sender = WhatsApp(settings.whatsapp_access_token, settings.whatsapp_phone_number_id,
            graph_version=settings.whatsapp_graph_version, client=session)
        sender.check(settings.whatsapp_business_account_id)
        application = sender._request('GET', 'app', params={'fields':'id,name'})
        app_id = application.get('id')
        if not isinstance(app_id, str) or not app_id.isdigit():
            raise ValueError('Meta no confirmó la aplicación asociada al token.')
        administrator = WhatsApp(app_id+'|'+settings.whatsapp_app_secret, settings.whatsapp_phone_number_id,
            graph_version=settings.whatsapp_graph_version, client=session)
        # Validar el secreto antes de detener una aplicación que ya funciona.
        previous = administrator._request('GET', app_id+'/subscriptions')
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        private_json(folder/('subscriptions-before-'+stamp+'.json'), previous)
        result = tunnel(folder, settings.whatsapp_webhook_port, settings.port)
        callback_url = result['callback_url']
        if not re.fullmatch(r'https://[a-z0-9-]+\.trycloudflare\.com/webhook/whatsapp', callback_url):
            raise ValueError('El túnel no devolvió una dirección válida para el receptor aislado.')
        restart(settings, folder)
        verify(session, callback_url, settings.whatsapp_verify_token)
        fields = {'messages'}
        for subscription in previous.get('data', []):
            if subscription.get('object') == 'whatsapp_business_account':
                fields.update(f['name'] for f in subscription.get('fields', [])
                    if isinstance(f, dict) and isinstance(f.get('name'), str)
                    and re.fullmatch('[a-z_]+', f['name']))
        registration = administrator._request('POST', app_id+'/subscriptions', data={
            'object':'whatsapp_business_account', 'callback_url':callback_url,
            'verify_token':settings.whatsapp_verify_token, 'fields':','.join(sorted(fields)),
        })
        if registration.get('success') is not True:
            raise ValueError('Meta no confirmó el registro del webhook.')
        linked = sender._request('POST', settings.whatsapp_business_account_id+'/subscribed_apps')
        if linked.get('success') is not True:
            raise ValueError('Meta no confirmó la suscripción de la cuenta de WhatsApp.')
        subscriptions = administrator._request('GET', app_id+'/subscriptions')
        accounts = sender._request('GET', settings.whatsapp_business_account_id+'/subscribed_apps')
        webhook_ok = any(row.get('object')=='whatsapp_business_account'
            and row.get('callback_url')==callback_url and row.get('active') is True
            and any(f.get('name')=='messages' for f in row.get('fields', []) if isinstance(f,dict))
            for row in subscriptions.get('data', []) if isinstance(row,dict))
        account_ok = any((row.get('whatsapp_business_api_data') or {}).get('id')==app_id
            for row in accounts.get('data', []) if isinstance(row,dict))
        if not webhook_ok or not account_ok:
            raise ValueError('La comprobación final de Meta no confirmó ambas suscripciones. Repite la conexión.')
        state = {'callback_url':callback_url, 'registered_at':datetime.now(timezone.utc).isoformat(),
            'webhook_registered':True, 'application_subscribed':True, 'temporary':True,
            'customer_messages_sent':0}
        private_json(folder/'connection.json', state)
        return state
    finally:
        if client is None:
            session.close()
