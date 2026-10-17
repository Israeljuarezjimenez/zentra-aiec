"""Operaciones atómicas sobre una conversación por cliente de Telegram."""
from datetime import datetime, timezone, timedelta
from uuid import uuid4

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
        self.tickets.create_index("chat_id", unique=True)
        self.tickets.create_index([('business_id', 1), ("status", 1), ("updated_at", -1)])

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

    def receive(self, chat_id, message_id, name, text):
        timestamp = now()
        active_id = business_id()
        base = {
            "_id": uuid4().hex, "chat_id": chat_id, "name": name,
            'business_id': active_id,
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
        result = self.tickets.update_one(
            {"_id": ticket["_id"], "messages.telegram_message_id": {"$ne": message_id}},
            {
                "$push": {"messages": {"$each": [{
                    "role": "cliente", "text": text, "created_at": timestamp,
                    'business_id': active_id,
                    "telegram_message_id": message_id,
                }], "$slice": -HISTORY_LIMIT}},
                "$inc": {"revision": 1},
                "$set": {"name": name, "updated_at": timestamp,
                         'business_id': active_id,
                         "last_customer_at": timestamp.isoformat(),
                         "status": "pendiente", "draft": None, "analysis": None,
                         "automation": {"failures": 0}},
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
            "analysis", "draft", "delivery_state", 'business_id', 'archived_message_count',
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
        attempt = {"id": uuid4().hex, "text": text, "revision": revision, 'business_id': business_id(),
                   "created_at": now(), "draft_id": draft_id, 'analysis': analysis, 'origin': origin}
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
        update = {
            "$set": {"delivery_state": "enviado", "attempt.telegram_message_id": telegram_message_id,
                     "attempt.finished_at": now(), "updated_at": now()},
            "$push": {"messages": {"$each": [{
                "role": "asistente", "text": attempt["text"], "created_at": now(),
                'business_id': attempt.get('business_id', 'legacy'),
                "telegram_message_id": telegram_message_id, "attempt_id": attempt_id,
                "analysis": attempt.get('analysis'), "origin": attempt.get('origin', 'claude_desktop'),
            }], "$slice": -HISTORY_LIMIT}},
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
