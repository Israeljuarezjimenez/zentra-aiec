"""Sincronizar la identidad pública del bot; esta operación no envía mensajes."""
import json
import os
from pathlib import Path
from uuid import uuid4

from .business import catalog


def sync_telegram_profile(telegram, backup_dir):
    profile = catalog()['telegram_profile']
    for field, limit in [('name',64), ('description',512), ('short_description',120)]:
        if not profile[field].strip() or len(profile[field]) > limit:
            raise ValueError(f'El campo {field} del perfil de Telegram no tiene una longitud válida.')
    fields = [('Name','name'), ('Description','description'), ('ShortDescription','short_description')]
    before = []
    for language in ('', 'es'):
        for suffix, field in fields:
            before.append({'language_code':language, 'field':field,
                           'value':telegram.call('getMy'+suffix, language_code=language)[field]})
    directory = Path(backup_dir)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    path = directory / f'telegram-profile-{uuid4().hex}.json'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as output:
        json.dump(before, output, ensure_ascii=False)
    updated = 0
    for old in before:
        if old['value'] == profile[old['field']]:
            continue
        suffix = next(suffix for suffix, field in fields if field == old['field'])
        telegram.call('setMy'+suffix, language_code=old['language_code'], **{old['field']:profile[old['field']]})
        updated += 1
    for language in ('', 'es'):
        for suffix, field in fields:
            if telegram.call('getMy'+suffix, language_code=language)[field] != profile[field]:
                raise ValueError('El perfil público de Telegram no coincide con la configuración del negocio.')
    return {'business':profile['name'], 'updated_fields':updated, 'verified':True}
