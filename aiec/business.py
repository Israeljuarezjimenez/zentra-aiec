"""Identidad y límites del negocio, compartidos por panel, Telegram y Claude."""
from functools import lru_cache
import json
from pathlib import Path

CATALOG_PATH = Path(__file__).with_name('catalog.json')


@lru_cache(maxsize=2)
def _read_catalog(modified_ns, size):
    return json.loads(CATALOG_PATH.read_text(encoding='utf-8'))


def catalog():
    stat = CATALOG_PATH.stat()
    return _read_catalog(stat.st_mtime_ns, stat.st_size)


def business_id():
    return catalog()['business_id']


def current_messages(ticket):
    """Los registros sin identidad se conservan como archivo; no se infiere su negocio."""
    active_id = business_id()
    return [m for m in ticket['messages'] if m.get('business_id') == active_id]


def commercial_status():
    data = catalog()
    profile = data['commercial_profile']
    return {
        'business_id': data['business_id'], 'business': data['business'],
        'mode': 'orientacion_con_referencias_externas',
        'can_confirm_orders': False, 'can_issue_own_quotes': False,
        'missing_data': [label for field, label in (
            ('own_price_list', 'Tarifario propio'), ('address', 'Dirección de atención'),
            ('hours', 'Horario comercial'), ('fulfillment', 'Condiciones de entrega'),
            ('payment_terms', 'Condiciones de pago'),
        ) if not profile.get(field)],
    }
