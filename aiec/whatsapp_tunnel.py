"""Dirección HTTPS gratuita para demostraciones; publica solo el receptor de WhatsApp."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

from filelock import FileLock

URL_PATTERN = r'https://[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com'


def read_state(folder):
    try:
        state = json.loads((Path(folder) / 'tunnel.json').read_text())
        if not isinstance(state, dict) or not isinstance(state.get('url'), str) or not re.fullmatch(URL_PATTERN, state['url']):
            return {}
        return state
    except (OSError, ValueError, TypeError):
        return {}


def process_matches(state, port):
    pid = state.get('pid')
    if not isinstance(pid, int) or pid <= 1 or state.get('port') != port:
        return False
    try:
        result = subprocess.run(['ps', '-p', str(pid), '-o', 'command='],
                                capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    expected = state.get('command')
    return bool(expected and result.returncode == 0 and result.stdout.strip() == expected)


def ensure_tunnel(folder, port, panel_port, *, timeout=45):
    if not isinstance(port, int) or not 1024 <= port <= 65535 or port == panel_port:
        raise ValueError('El túnel solo puede publicar el puerto separado de WhatsApp, nunca el panel.')
    binary = shutil.which('cloudflared')
    if not binary:
        raise ValueError('Falta cloudflared. En Mac se instala con: brew install cloudflared')
    folder = Path(folder)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    folder.chmod(0o700)
    with FileLock(str(folder / 'tunnel.lock'), timeout=5):
        existing = read_state(folder)
        if existing and process_matches(existing, port):
            return {**existing, 'reused':True}
        command = [binary, 'tunnel', '--no-autoupdate', '--url', f'http://127.0.0.1:{port}',
                   '--protocol', 'http2']
        log_path = folder / 'tunnel.log'
        descriptor = os.open(log_path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        os.chmod(log_path, 0o600)
        with os.fdopen(descriptor, 'wb') as output:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output,
                stderr=subprocess.STDOUT, start_new_session=True)
        try:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise ValueError('No se pudo abrir la dirección HTTPS de la demo. Los registros se conservan privados.')
                match = re.search(URL_PATTERN, log_path.read_text(errors='replace'))
                if match:
                    state = {'url':match.group(0), 'callback_url':match.group(0)+'/webhook/whatsapp',
                        'pid':process.pid, 'port':port, 'command':' '.join(command),
                        'created_at':datetime.now(timezone.utc).isoformat(), 'temporary':True}
                    target = folder / 'tunnel.json'
                    fd = os.open(target, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
                    with os.fdopen(fd, 'w') as output:
                        json.dump(state, output, indent=2)
                    return {**state, 'reused':False}
                time.sleep(.2)
            raise ValueError('Cloudflare no confirmó la dirección HTTPS a tiempo. Intenta de nuevo.')
        except BaseException:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            raise
