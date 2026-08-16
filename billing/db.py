import uuid
from datetime import datetime, timezone
from pymongo import MongoClient
from config.settings import MONGODB_URI, DB_NAME

_client = MongoClient(MONGODB_URI)
_db = _client[DB_NAME]

billing_sent_reminders = _db.billing_sent_reminders
billing_pending_proofs = _db.billing_pending_proofs
_bot_state = _db.bot_state


def reminder_already_sent(id_cuota: str) -> bool:
    return billing_sent_reminders.find_one({"id_cuota": id_cuota}) is not None


def save_sent_reminder(id_cuota: str, id_socio: str, email: str, message_id: str = None):
    billing_sent_reminders.update_one(
        {"id_cuota": id_cuota},
        {"$set": {
            "id_cuota": id_cuota,
            "id_socio": id_socio,
            "email": email,
            "thread_id": message_id,
            "sent_at": datetime.now(timezone.utc),
        }},
        upsert=True,
    )


def get_reminder_by_thread(thread_id: str) -> dict | None:
    return billing_sent_reminders.find_one({"thread_id": thread_id})


def save_pending_proof(email_data: dict, id_socio: str | None, id_cuota: str | None) -> str:
    approval_id = str(uuid.uuid4())[:8]
    billing_pending_proofs.insert_one({
        "approval_id": approval_id,
        "email_data": email_data,
        "id_socio": id_socio,
        "id_cuota": id_cuota,
        "status": "pending",
        "created_at": datetime.now(timezone.utc),
    })
    return approval_id


def get_pending_proof(approval_id: str) -> dict | None:
    return billing_pending_proofs.find_one({"approval_id": approval_id, "status": "pending"})


def claim_pending_proof(approval_id: str) -> dict | None:
    return billing_pending_proofs.find_one_and_update(
        {"approval_id": approval_id, "status": "pending"},
        {"$set": {"status": "processing"}},
        return_document=False,
    )


def update_proof_status(approval_id: str, status: str):
    billing_pending_proofs.update_one(
        {"approval_id": approval_id},
        {"$set": {"status": status, "resolved_at": datetime.now(timezone.utc)}},
    )


def last_overdue_alert() -> datetime | None:
    doc = _bot_state.find_one({"_id": "billing_last_overdue_alert"})
    return doc["sent_at"] if doc else None


def save_overdue_alert_time():
    _bot_state.update_one(
        {"_id": "billing_last_overdue_alert"},
        {"$set": {"sent_at": datetime.now(timezone.utc)}},
        upsert=True,
    )
