"""WhatsApp Cloud API: texto de servicio; sin plantillas ni campañas de pago."""
from datetime import datetime, timedelta, timezone
import re

import httpx

from .telegram import DeliveryUncertain, TelegramRejected


class WhatsAppRejected(TelegramRejected):
    def __init__(self, code, retry_after=None):
        super().__init__(code, retry_after)
        self.args = (f'WhatsApp rechazó la operación (código {code}).',)


class WhatsApp:
    def __init__(self, token, phone_number_id, *, graph_version='v26.0', client=None):
        if not phone_number_id.isdigit() or not re.fullmatch(r'v\d+\.0', graph_version):
            raise ValueError('Identificador de número o versión de Meta no válidos.')
        self.phone_number_id = phone_number_id
        self._token = token
        self._base = f'https://graph.facebook.com/{graph_version}'
        self.client = client or httpx.Client(timeout=httpx.Timeout(25, connect=10),
                                              trust_env=False, follow_redirects=False)

    def _request(self, method, path, **kwargs):
        try:
            response = self.client.request(method, self._base+'/'+path,
                headers={'Authorization':'Bearer '+self._token}, **kwargs)
        except httpx.TransportError:
            raise DeliveryUncertain('Meta no confirmó la operación. No repitas un envío sin consultar su estado.') from None
        if response.status_code >= 500:
            raise DeliveryUncertain('Meta no confirmó el resultado de la operación.')
        try:
            data = response.json()
        except ValueError:
            raise DeliveryUncertain('Meta devolvió un resultado que no pudo confirmarse.') from None
        if not response.is_success or not isinstance(data, dict) or data.get('error'):
            error = data.get('error', {}) if isinstance(data, dict) else {}
            raise WhatsAppRejected(error.get('code', response.status_code))
        return data

    def check(self, business_account_id):
        number = self._request('GET', self.phone_number_id,
            params={'fields':'id,display_phone_number,verified_name'})
        numbers = self._request('GET', business_account_id+'/phone_numbers', params={'fields':'id','limit':100})
        if not any(n.get('id') == self.phone_number_id for n in numbers.get('data', [])):
            raise ValueError('El número no aparece en la cuenta de WhatsApp configurada.')
        return number

    def send_message(self, recipient, text, *, last_customer_at):
        if not re.fullmatch(r'\d{6,20}', recipient):
            raise ValueError('El destinatario de WhatsApp no es válido.')
        if isinstance(last_customer_at, str):
            last_customer_at = datetime.fromisoformat(last_customer_at)
        if last_customer_at.tzinfo is None:
            last_customer_at = last_customer_at.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - last_customer_at
        if age < timedelta(minutes=-5) or age >= timedelta(hours=24):
            raise ValueError('La ventana de atención de WhatsApp está cerrada. Espera un mensaje nuevo del cliente; no se enviarán plantillas.')
        data = self._request('POST', self.phone_number_id+'/messages', json={
            'messaging_product':'whatsapp','recipient_type':'individual','to':recipient,
            'type':'text','text':{'preview_url':False,'body':text},
        })
        messages = data.get('messages', [])
        if (not isinstance(messages, list) or not messages or not isinstance(messages[0], dict)
                or not isinstance(messages[0].get('id'), str) or not messages[0]['id'].startswith('wamid.')
                or len(messages[0]['id']) > 512):
            raise DeliveryUncertain('Meta no devolvió el identificador del mensaje. No repitas el envío.')
        return {'message_id':messages[0]['id']}
