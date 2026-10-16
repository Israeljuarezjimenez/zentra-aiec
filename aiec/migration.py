"""Separación verificable del historial anterior; nunca elimina mensajes ni destinatarios."""
from datetime import datetime, timezone
import os
from pathlib import Path
from uuid import uuid4

from bson import json_util

from .business import business_id
from .repository import now


def migrate_business(repo, since: datetime, backup_dir: Path):
    if since.tzinfo is None:
        raise ValueError('La fecha de cambio debe incluir zona horaria.')
    active_id = business_id()
    candidates = list(repo.tickets.find({'$or': [
        {'business_id': {'$exists': False}},
        {'messages': {'$elemMatch': {'business_id': {'$ne': active_id}}}},
    ]}))
    if not candidates:
        return {'updated': 0, 'archived_messages': 0, 'conflicts': 0, 'blocked': 0}
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup_dir.chmod(0o700)
    backup = backup_dir / f'business-{uuid4().hex}.json'
    fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as output:
        output.write(json_util.dumps({'since': since, 'tickets': candidates}))
        output.flush()
        os.fsync(output.fileno())
    result = {'updated': 0, 'archived_messages': 0, 'conflicts': 0, 'blocked': 0}
    for ticket in candidates:
        if ticket['delivery_state'] in ('enviando', 'incierto'):
            result['blocked'] += 1
            continue
        messages = []
        for original in ticket['messages']:
            message = dict(original)
            stamp = message['created_at']
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            message.setdefault('business_id', active_id if stamp >= since else 'legacy')
            if message['business_id'] == active_id and message['role'] == 'agencia':
                message['role'] = 'asistente'
            messages.append(message)
        active = [m for m in messages if m['business_id'] == active_id]
        archived = ticket.get('archived_messages', []) + [m for m in messages if m['business_id'] != active_id]
        has_customer = any(m['role'] == 'cliente' for m in active)
        last_reply = next((m for m in reversed(active) if m['role'] == 'asistente'), None)
        analysis = last_reply.get('analysis') if last_reply and ticket['status'] == 'respondida' else None
        changes = {
            'messages': active, 'business_id': active_id if has_customer else 'legacy',
            'archived_messages': archived, 'archived_message_count': len(archived),
            'analysis': analysis, 'draft': None,
            'status': ticket['status'] if has_customer and ticket['status'] != 'revision' else
                      'pendiente' if has_customer else 'cerrada',
            'updated_at': now(),
        }
        updated = repo.tickets.update_one(
            {'_id': ticket['_id'], 'revision': ticket['revision'], 'updated_at': ticket['updated_at'],
             'delivery_state': {'$nin': ['enviando', 'incierto']}},
            {'$set': changes, '$inc': {'revision': 1}},
        )
        if updated.modified_count:
            result['updated'] += 1
            result['archived_messages'] += len(archived)
        else:
            result['conflicts'] += 1
    repo.state.update_one({'_id': 'business_migration'}, {'$set': {
        'business_id': active_id, 'legacy_before': since, 'checked_at': now(), **result,
    }}, upsert=True)
    return result
