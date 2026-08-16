import uuid
from datetime import datetime, timezone
from bson import ObjectId
from pymongo import MongoClient
from config.settings import MONGODB_URI, DB_NAME

_client = MongoClient(MONGODB_URI)
_db = _client[DB_NAME]

faqs = _db.faqs
interactions = _db.interactions
processed_emails = _db.processed_emails
pending_clarifications = _db.pending_clarifications
pending_approvals = _db.pending_approvals
events = _db.events
bot_state = _db.bot_state
blocked_senders = _db.blocked_senders


def get_telegram_offset() -> int | None:
    doc = bot_state.find_one({"_id": "telegram_offset"})
    return doc["value"] if doc else None


def save_telegram_offset(value: int):
    bot_state.update_one(
        {"_id": "telegram_offset"},
        {"$set": {"value": value}},
        upsert=True,
    )



def is_processed(email_id: str) -> bool:
    return processed_emails.find_one({"email_id": email_id}) is not None


def mark_processed(email_id: str, action: str) -> bool:
    """Retorna True si se insertó (primero), False si ya existía (carrera entre instancias).
    Usa upsert atómico — no necesita índice único previo."""
    result = processed_emails.update_one(
        {"email_id": email_id},
        {"$setOnInsert": {
            "email_id": email_id,
            "action": action,
            "processed_at": datetime.now(timezone.utc),
        }},
        upsert=True,
    )
    return result.upserted_id is not None


def get_active_faqs() -> list:
    return list(faqs.find({"active": True}))


def save_faq(question: str, answer: str, category: str, source: str = "human") -> str:
    result = faqs.insert_one({
        "question": question,
        "answer": answer,
        "category": category,
        "source": source,
        "active": True,
        "times_used": 0,
        "created_at": datetime.now(timezone.utc),
    })
    return str(result.inserted_id)


def increment_faq_usage(faq_id: str):
    faqs.update_one(
        {"_id": ObjectId(faq_id)},
        {"$inc": {"times_used": 1}, "$set": {"last_used": datetime.now(timezone.utc)}},
    )


def save_interaction(email_data: dict, classification: dict, human_response: str = None):
    interactions.insert_one({
        "email_id": email_data["id"],
        "thread_id": email_data["thread_id"],
        "from": email_data["from"],
        "subject": email_data["subject"],
        "body": email_data["body"][:2000],
        "classification": classification,
        "human_response": human_response,
        "converted_to_faq": False,
        "created_at": datetime.now(timezone.utc),
    })


def find_interaction_by_thread(thread_id: str) -> dict | None:
    return interactions.find_one({"thread_id": thread_id, "human_response": None})


def update_interaction_response(interaction_id, response: str):
    interactions.update_one(
        {"_id": interaction_id},
        {"$set": {"human_response": response, "responded_at": datetime.now(timezone.utc)}},
    )


def save_pending_clarification(email_data: dict, classification: dict):
    pending_clarifications.insert_one({
        "email_id": email_data["id"],
        "email_data": email_data,
        "classification": classification,
        "status": "waiting",
        "created_at": datetime.now(timezone.utc),
    })


def save_pending_approval(email_data: dict, response_text: str, faq_id: str = None, telegram_msg_id: int = None) -> str:
    approval_id = str(uuid.uuid4())[:8]
    pending_approvals.insert_one({
        "approval_id": approval_id,
        "email_data": email_data,
        "response_text": response_text,
        "faq_id": faq_id,
        "telegram_msg_id": telegram_msg_id,
        "status": "pending",
        "created_at": datetime.now(timezone.utc),
    })
    return approval_id


def get_pending_approval(approval_id: str) -> dict | None:
    return pending_approvals.find_one({"approval_id": approval_id, "status": "pending"})


def claim_approval(approval_id: str, from_telegram_msg_id: int = None) -> dict | None:
    """Atómicamente cambia status pending→sending y retorna el documento.
    Si from_telegram_msg_id se pasa, solo acepta si coincide con el msg_id actual
    (evita que un botón stale de una versión anterior apruebe una versión regenerada).
    Retorna None si ya fue tomado o si el msg_id no coincide."""
    query = {"approval_id": approval_id, "status": "pending"}
    if from_telegram_msg_id is not None:
        query["telegram_msg_id"] = from_telegram_msg_id
    return pending_approvals.find_one_and_update(
        query,
        {"$set": {"status": "sending", "sending_started_at": datetime.now(timezone.utc)}},
        return_document=False,
    )


def claim_for_regeneration(reply_to_msg_id: int) -> dict | None:
    """Atómicamente toma el approval para regenerar. Solo una instancia gana."""
    return pending_approvals.find_one_and_update(
        {"telegram_msg_id": reply_to_msg_id, "status": "pending"},
        {"$set": {"status": "regenerating"}},
        return_document=False,
    )


def get_awaiting_edit() -> dict | None:
    """Retorna la aprobación que está esperando instrucción de edición del admin."""
    return pending_approvals.find_one({"status": "awaiting_edit"})


def set_awaiting_edit(approval_id: str):
    pending_approvals.update_one(
        {"approval_id": approval_id},
        {"$set": {"status": "awaiting_edit"}},
    )


def update_approval_response(approval_id: str, new_response: str, new_telegram_msg_id: int = None):
    """Guarda la nueva versión de la respuesta y vuelve a estado 'pending'."""
    fields = {"response_text": new_response, "status": "pending"}
    if new_telegram_msg_id:
        fields["telegram_msg_id"] = new_telegram_msg_id
    pending_approvals.update_one({"approval_id": approval_id}, {"$set": fields})


def update_approval_status(approval_id: str, status: str):
    pending_approvals.update_one(
        {"approval_id": approval_id},
        {"$set": {"status": status, "resolved_at": datetime.now(timezone.utc)}},
    )


# ── Eventos ──────────────────────────────────────────────────────────────────

def save_event(name: str, description: str, date: datetime, location: str,
               price: str, spots: int, registration_deadline: datetime,
               registration_url: str, flyer_path: str = None) -> str:
    result = events.insert_one({
        "name": name,
        "description": description,
        "date": date,
        "location": location,
        "price": price,
        "spots": spots,
        "registration_deadline": registration_deadline,
        "registration_url": registration_url,
        "flyer_path": flyer_path,
        "active": True,
        "created_at": datetime.now(timezone.utc),
    })
    return str(result.inserted_id)


def get_upcoming_events() -> list:
    """Retorna eventos futuros ordenados por fecha."""
    now = datetime.now(timezone.utc)
    return list(events.find({"active": True, "date": {"$gte": now}}).sort("date", 1))


def get_event(event_id: str) -> dict | None:
    return events.find_one({"_id": ObjectId(event_id)})


def update_event_flyer(event_id: str, flyer_path: str):
    events.update_one({"_id": ObjectId(event_id)}, {"$set": {"flyer_path": flyer_path}})


def deactivate_event(event_id: str):
    events.update_one({"_id": ObjectId(event_id)}, {"$set": {"active": False}})


def set_event_auto_approve(event_id: str, value: bool):
    events.update_one({"_id": ObjectId(event_id)}, {"$set": {"auto_approve": value}})


# ── Aclaraciones pendientes de confirmación ───────────────────────────────────

def save_pending_clarification_thread(thread_id: str, event_id: str, from_addr: str):
    """Guarda que este hilo está esperando confirmación sobre un evento específico."""
    bot_state.update_one(
        {"_id": f"clarification_thread:{thread_id}"},
        {"$set": {
            "thread_id": thread_id,
            "event_id": event_id,
            "from": from_addr,
            "created_at": datetime.now(timezone.utc),
        }},
        upsert=True,
    )


def get_pending_clarification_thread(thread_id: str) -> dict | None:
    return bot_state.find_one({"_id": f"clarification_thread:{thread_id}"})


def delete_pending_clarification_thread(thread_id: str):
    bot_state.delete_one({"_id": f"clarification_thread:{thread_id}"})


# ── Remitentes bloqueados ─────────────────────────────────────────────────────

def is_blocked_sender(from_addr: str) -> bool:
    from_addr = from_addr.lower()
    for entry in blocked_senders.find():
        pattern = entry["pattern"].lower()
        if pattern in from_addr:
            return True
    return False


def block_sender(pattern: str):
    pattern = pattern.strip().lower()
    blocked_senders.update_one(
        {"pattern": pattern},
        {"$set": {"pattern": pattern, "added_at": datetime.now(timezone.utc)}},
        upsert=True,
    )


def unblock_sender(pattern: str):
    pattern = pattern.strip().lower()
    result = blocked_senders.delete_one({"pattern": pattern})
    return result.deleted_count > 0


def list_blocked_senders() -> list:
    return list(blocked_senders.find({}, {"_id": 0, "pattern": 1}))
