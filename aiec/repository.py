"""Operaciones atómicas sobre conversaciones, separadas por canal y cliente."""
from datetime import datetime, timezone, timedelta
from uuid import uuid4
import hashlib

from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError

from .config import Settings
from .business import business_id

HISTORY_LIMIT = 100


def now():
    return datetime.now(timezone.utc)


class ConflictError(ValueError):
    """La conversación cambió o la operación ya fue ejecutada."""


class Repository:
    def __init__(self, database):
        self.database = database
        self.tickets = database.tickets
        self.state = database.state
        self.whatsapp_inbox = database.whatsapp_inbox
        self.whatsapp_delivery = database.whatsapp_delivery
        self.tickets.create_index("chat_id", unique=True)
        self.tickets.create_index([('business_id', 1), ("status", 1), ("updated_at", -1)])
        self.whatsapp_inbox.create_index([('processed', 1), ('received_at', 1)])
        self.whatsapp_inbox.create_index('expires_at', expireAfterSeconds=0)
        self.whatsapp_delivery.create_index('expires_at', expireAfterSeconds=0)

    @classmethod
    def connect(cls, settings: Settings):
        settings.validate()
        client = MongoClient(
            settings.mongodb_uri, serverSelectionTimeoutMS=7000,
            connectTimeoutMS=7000, socketTimeoutMS=15000, tz_aware=True,
            appname="AIEC", maxPoolSize=5,
        )
        client.admin.command("ping")
        return cls(client[settings.database])

    def receive(self, chat_id, message_id, name, text, *, channel='telegram', recipient_id=None, customer_at=None):
        if channel not in ('telegram', 'whatsapp'):
            raise ValueError('Canal de mensajes no válido.')
        timestamp = now()
        active_id = business_id()
        base = {
            "_id": uuid4().hex, "chat_id": chat_id, "name": name,
            'business_id': active_id,
            'channel': channel, 'recipient_id': recipient_id or str(chat_id),
            "created_at": timestamp, "updated_at": timestamp,
            "revision": 0, "status": "pendiente", "messages": [],
            "analysis": None, "draft": None, "delivery_state": "ninguno",
        }
        try:
            self.tickets.update_one({"chat_id": chat_id}, {"$setOnInsert": base}, upsert=True)
        except DuplicateKeyError:
            # Dos receptores podrían intentar crear la misma conversación.
            pass
        ticket = self.tickets.find_one({"chat_id": chat_id}, {"_id": 1})
        incoming = {'role':'cliente', 'text':text, 'created_at':timestamp,
                    'business_id':active_id, 'provider_message_id':message_id, 'channel':channel}
        if channel == 'telegram':
            incoming['telegram_message_id'] = message_id
        result = self.tickets.update_one(
            {"_id": ticket["_id"], "messages.telegram_message_id": {"$ne": message_id},
             'messages.provider_message_id': {'$ne': message_id}},
            {
                "$push": {"messages": {"$each": [incoming], "$slice": -HISTORY_LIMIT}},
                "$inc": {"revision": 1},
                "$set": {"name": name, "updated_at": timestamp,
                         'business_id': active_id,
                         'channel': channel, 'recipient_id': recipient_id or str(chat_id),
                         "status": "pendiente", "draft": None, "analysis": None,
                         "automation": {"failures": 0}},
                '$max': {'last_customer_at':(customer_at or timestamp).isoformat()},
            },
        )
        return ticket["_id"], bool(result.modified_count)

    def get(self, ticket_id):
        ticket = self.tickets.find_one({"_id": ticket_id})
        if not ticket:
            raise ValueError("No encontramos esa solicitud.")
        return ticket

    def list(self, status=None, limit=50):
        query = {'business_id': business_id()}
        if status:
            query['status'] = status
        projection = {key: 1 for key in (
            "chat_id", "name", "revision", "status", "created_at", "updated_at",
            "analysis", "draft", "delivery_state", 'business_id', 'archived_message_count', 'channel',
        )}
        projection["messages"] = {"$slice": -1}
        return list(self.tickets.find(query, projection).sort("updated_at", -1).limit(limit))

    def counts(self):
        counts = {key: 0 for key in ("pendiente", "revision", "respondida", "cerrada")}
        for row in self.tickets.aggregate([{'$match': {'business_id': business_id()}},
                                           {"$group": {"_id": "$status", "total": {"$sum": 1}}}]):
            counts[row["_id"]] = row["total"]
        return counts

    def claim_next(self, since, *, debounce_seconds=2, lease_seconds=180):
        """Una sola generación por conversación, con recuperación después de un reinicio.

        No activar automáticamente conversaciones históricas anteriores a la instalación.
        Un nuevo mensaje sí actualiza updated_at y vuelve a entrar en la cola.
        """
        timestamp = now()
        return self.tickets.find_one_and_update(
            {'business_id': business_id(), "status": {"$in": ["pendiente", "revision"]},
             "last_customer_at": {"$gte": since.isoformat()},
             "updated_at": {"$lte": timestamp - timedelta(seconds=debounce_seconds)},
             "delivery_state": {"$nin": ["enviando", "incierto"]},
             "$and": [
                 {"$or": [{"processing.until": {"$lte": timestamp}}, {"processing": {"$exists": False}}]},
                 {"$or": [{"automation.next_at": {"$lte": timestamp}}, {"automation.next_at": {"$exists": False}}]},
                 {"$or": [{"automation.failures": {"$lt": 3}}, {"automation.failures": {"$exists": False}}]},
             ]},
            {"$set": {"processing": {"id": uuid4().hex,
                                       "until": timestamp + timedelta(seconds=lease_seconds)}}},
            sort=[("updated_at", 1)], return_document=ReturnDocument.AFTER,
        )

    def release_claim(self, ticket_id, claim_id, *, revision, error=None):
        # Un mensaje nuevo no hereda el error ni la espera de una generación anterior.
        updates = {"$set": {"automation.error": error,
                   "automation.next_at": now() + timedelta(seconds=30 if error else 0)},
                   "$unset": {"processing": ""}}
        if error:
            updates["$inc"] = {"automation.failures": 1}
        else:
            updates["$set"]["automation.failures"] = 0
        self.tickets.update_one({"_id": ticket_id, "processing.id": claim_id, "revision": revision}, updates)
        self.tickets.update_one({"_id": ticket_id, "processing.id": claim_id},
                                {"$unset": {"processing": ""}})

    def automation_state(self):
        state = self.state.find_one({"_id": "automation"}) or {"status": "sin_iniciar"}
        if state.get("checked_at") and now() - state["checked_at"] > timedelta(minutes=1):
            state["status"] = "sin_iniciar"
        return state

    def save_draft(self, ticket_id, revision, analysis, text):
        draft = {"id": uuid4().hex, "revision": revision, "text": text, "created_at": now()}
        ticket = self.tickets.find_one_and_update(
            {"_id": ticket_id, 'business_id': business_id(), "revision": revision, "status": {"$in": ["pendiente", "revision"]},
             "delivery_state": {"$nin": ["enviando", "incierto"]}},
            {"$set": {"analysis": analysis, "draft": draft,
                       "status": "revision", "updated_at": now()}},
            return_document=ReturnDocument.AFTER,
        )
        if not ticket:
            raise ConflictError("La solicitud cambió, está cerrada o tiene un envío sin resolver. Vuelve a consultarla.")
        return ticket

    def begin_send(self, ticket_id, draft_id, revision, text, *, analysis=None, origin='claude_desktop'):
        channel = self.get(ticket_id).get('channel', 'telegram')
        attempt = {"id": uuid4().hex, "text": text, "revision": revision, 'business_id': business_id(),
                   "created_at": now(), "draft_id": draft_id, 'analysis': analysis, 'origin': origin, 'channel':channel}
        ticket = self.tickets.find_one_and_update(
            {"_id": ticket_id, 'business_id': business_id(), "revision": revision, "draft.id": draft_id,
             "draft.revision": revision, "draft.text": text, "status": "revision",
             "delivery_state": {"$nin": ["enviando", "incierto"]}},
            {"$set": {"delivery_state": "enviando", "attempt": attempt, "updated_at": now()}},
            return_document=ReturnDocument.AFTER,
        )
        if not ticket:
            raise ConflictError("El borrador cambió, ya fue enviado o hay un envío sin resolver. Actualiza la página.")
        return ticket

    def finish_send(self, ticket_id, attempt_id, telegram_message_id):
        ticket = self.get(ticket_id)
        attempt = ticket.get("attempt", {})
        if attempt.get("id") != attempt_id:
            raise ConflictError("El intento de envío ya no coincide.")
        query = {"_id": ticket_id, "attempt.id": attempt_id,
                 "delivery_state": {"$in": ["enviando", "incierto"]}}
        channel = attempt.get('channel', 'telegram')
        message = {
            'role':'asistente', 'text':attempt['text'], 'created_at':now(), 'channel':channel,
            'business_id':attempt.get('business_id', 'legacy'), 'provider_message_id':telegram_message_id,
            'attempt_id':attempt_id, 'analysis':attempt.get('analysis'),
            'origin':attempt.get('origin', 'claude_desktop'),
        }
        receipt = {'attempt.provider_message_id':telegram_message_id}
        if channel == 'telegram':
            message['telegram_message_id'] = telegram_message_id
            receipt['attempt.telegram_message_id'] = telegram_message_id
        else:
            receipt.update({'attempt.provider_delivery_status':'accepted', 'attempt.provider_status_rank':0})
        update = {
            "$set": {"delivery_state": "enviado", **receipt,
                     "attempt.finished_at": now(), "updated_at": now()},
            "$push": {"messages": {"$each": [message], "$slice": -HISTORY_LIMIT}},
        }
        # Historial, estado y eliminación del borrador se confirman en una sola escritura.
        # Un fallo después de esta escritura no permite reenviar el mismo borrador.
        same_context = {**query, "revision": attempt["revision"], "draft.id": attempt["draft_id"]}
        result = self.tickets.update_one(same_context, {
            **update, "$set": {**update["$set"], "status": "respondida", "draft": None},
        })
        if not result.matched_count:
            # Si el cliente escribió mientras enviábamos, conservamos su estado pendiente.
            self.tickets.update_one(query, update)
        if channel == 'whatsapp':
            self._apply_whatsapp_status(telegram_message_id)

    def send_failed(self, ticket_id, attempt_id, *, uncertain):
        self.tickets.update_one(
            {"_id": ticket_id, "attempt.id": attempt_id, "delivery_state": "enviando"},
            {"$set": {"delivery_state": "incierto" if uncertain else "rechazado",
                       "updated_at": now()}},
        )

    def resolve_send(self, ticket_id, attempt_id, was_sent):
        ticket = self.get(ticket_id)
        if ticket.get("attempt", {}).get("id") != attempt_id or ticket["delivery_state"] not in ("incierto", "enviando"):
            raise ConflictError("No hay un envío pendiente de verificar con ese identificador.")
        if was_sent:
            self.finish_send(ticket_id, attempt_id, None)
        else:
            self.tickets.update_one(
                {"_id": ticket_id, "attempt.id": attempt_id,
                 "delivery_state": {"$in": ["incierto", "enviando"]}},
                {"$set": {"delivery_state": "rechazado", "updated_at": now()}},
            )

    def close(self, ticket_id, revision):
        result = self.tickets.update_one(
            {"_id": ticket_id, "revision": revision,
             "delivery_state": {"$nin": ["enviando", "incierto"]}},
            {"$set": {"status": "cerrada", "draft": None, "updated_at": now()}},
        )
        if not result.matched_count:
            raise ConflictError("La solicitud cambió o tiene un envío sin resolver.")

    def offset(self, bot_id):
        return (self.state.find_one({"_id": f"telegram:{bot_id}"}) or {}).get("offset", 0)

    def checkpoint(self, bot_id, offset):
        self.state.update_one({"_id": f"telegram:{bot_id}"}, {"$max": {"offset": offset}}, upsert=True)

    def queue_whatsapp(self, phone_number_id, message_id, sender, name, text, customer_at):
        key = hashlib.sha256((phone_number_id+':'+message_id).encode()).hexdigest()
        result = self.whatsapp_inbox.update_one({'_id':key}, {'$setOnInsert': {
            'business_id':business_id(), 'phone_number_id':phone_number_id, 'message_id':message_id,
            'sender':sender, 'name':name, 'text':text, 'customer_at':customer_at,
            'received_at':now(), 'expires_at':now()+timedelta(days=30), 'processed':False,
        }}, upsert=True)
        return result.upserted_id is not None

    def process_whatsapp_inbox(self):
        event = self.whatsapp_inbox.find_one_and_update(
            {'processed':False, 'business_id':business_id(), '$or':[
                {'lease_until':{'$exists':False}}, {'lease_until':{'$lte':now()}}]},
            {'$set':{'lease_until':now()+timedelta(seconds=60)}},
            sort=[('received_at',1)], return_document=ReturnDocument.AFTER)
        if event is None:
            return False
        key = 'whatsapp:'+event['phone_number_id']+':'+event['sender']
        self.receive(key, event['message_id'], event['name'], event['text'],
                     channel='whatsapp', recipient_id=event['sender'], customer_at=event['customer_at'])
        self.whatsapp_inbox.update_one({'_id':event['_id']},
            {'$set':{'processed':True, 'processed_at':now()}, '$unset':{'lease_until':''}})
        return True

    def whatsapp_status(self, message_id, status, timestamp):
        if not isinstance(status, str):
            return
        rank = {'sent':1, 'failed':2, 'delivered':3, 'read':4}.get(status)
        if rank is None:
            return
        self.whatsapp_delivery.update_one({'_id':message_id}, {
            '$max': {'rank':rank}, '$set': {'updated_at':timestamp},
            '$setOnInsert':{'expires_at':now()+timedelta(days=30)},
        }, upsert=True)
        self._apply_whatsapp_status(message_id)

    def _apply_whatsapp_status(self, message_id):
        receipt = self.whatsapp_delivery.find_one({'_id':message_id})
        if not receipt:
            return
        rank = receipt['rank']
        status = {1:'sent',2:'failed',3:'delivered',4:'read'}[rank]
        self.tickets.update_one({'channel':'whatsapp', 'attempt.provider_message_id':message_id,
            'attempt.provider_status_rank':{'$lt':rank}}, {'$set':{
                'attempt.provider_delivery_status':status, 'attempt.provider_status_rank':rank,
                'attempt.provider_status_at':receipt['updated_at'], 'updated_at':now(),
            }})
