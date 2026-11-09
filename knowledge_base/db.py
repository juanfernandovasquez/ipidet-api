"""Datos que comparten el bot de Telegram y los scripts de administración: FAQs, eventos y estado del bot.

La cola de correos del agente vive en webapp/agente_db.py.
"""
from datetime import datetime, timedelta, timezone
from bson import ObjectId
from bson.errors import InvalidId
from config.mongo_client import db as _db

faqs = _db.faqs
interactions = _db.interactions
eventos_col = _db.eventos_ipidet   # misma colección que la web admin (/eventos)
bot_state = _db.bot_state


def get_telegram_offset() -> int | None:
    doc = bot_state.find_one({"_id": "telegram_offset"})
    return doc["value"] if doc else None


def save_telegram_offset(value: int):
    bot_state.update_one(
        {"_id": "telegram_offset"},
        {"$set": {"value": value}},
        upsert=True,
    )


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


# ── Eventos ──────────────────────────────────────────────────────────────────

def _fecha_evento(fecha: str, hora: str = "") -> datetime | None:
    """Convierte fecha/hora del evento (texto en la web) a datetime."""
    if not fecha:
        return None
    for valor, formato in ((f"{fecha} {hora}", "%Y-%m-%d %H:%M"), (fecha, "%Y-%m-%d")):
        try:
            return datetime.strptime(valor, formato).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _evento_para_agente(doc: dict) -> dict:
    """Vista de un documento de eventos_ipidet con los campos que usa el agente."""
    cupo = doc.get("cupo_max")
    evento = {
        "_id": doc["_id"],
        "name": doc.get("titulo", ""),
        "description": doc.get("descripcion", ""),
        "date": _fecha_evento(doc.get("fecha", ""), doc.get("hora", "")),
        "location": doc.get("lugar") or doc.get("modalidad") or "por confirmar",
        "price": doc.get("precio") or "consultar",
        "registration_deadline": _fecha_evento(doc.get("fecha_limite_inscripcion", "")),
        "flyer_path": doc.get("flyer_path"),
        "flyer_url": doc.get("flyer_url"),
        "auto_approve": bool(doc.get("usar_en_agente")),
        "active": doc.get("estado") == "activo",
    }
    if cupo:
        evento["spots"] = max(0, cupo - len(doc.get("inscritos", [])) - len(doc.get("zoom_registrants", [])))
    if doc.get("link_registro"):
        evento["registration_url"] = doc["link_registro"]
    return evento


def save_event(name: str, description: str, date: datetime, location: str,
               price: str, spots: int, registration_deadline: datetime,
               registration_url: str, flyer_path: str = None) -> str:
    """Crea el evento (desde Telegram) en la misma colección que la web admin."""
    result = eventos_col.insert_one({
        "titulo": name,
        "descripcion": description,
        "fecha": date.strftime("%Y-%m-%d") if date else "",
        "hora": date.strftime("%H:%M") if date else "",
        "lugar": location,
        "precio": price,
        "cupo_max": spots or None,
        "fecha_limite_inscripcion": registration_deadline.strftime("%Y-%m-%d") if registration_deadline else "",
        "link_registro": registration_url,
        "flyer_path": flyer_path,
        "estado": "activo",
        "inscritos": [],
        "created_at": datetime.now(timezone.utc),
    })
    return str(result.inserted_id)


def get_upcoming_events() -> list:
    """Eventos activos de hoy en adelante, ordenados por fecha (hora de Lima)."""
    hoy = datetime.now(timezone(timedelta(hours=-5))).strftime("%Y-%m-%d")
    docs = eventos_col.find({"estado": "activo", "fecha": {"$gte": hoy}}).sort("fecha", 1)
    return [_evento_para_agente(d) for d in docs]


def get_event(event_id: str) -> dict | None:
    try:
        doc = eventos_col.find_one({"_id": ObjectId(event_id)})
    except InvalidId:  # el ID viene de la clasificación del modelo
        return None
    return _evento_para_agente(doc) if doc else None


def update_event_flyer(event_id: str, flyer_path: str):
    eventos_col.update_one({"_id": ObjectId(event_id)}, {"$set": {"flyer_path": flyer_path}})


def deactivate_event(event_id: str):
    eventos_col.update_one({"_id": ObjectId(event_id)}, {"$set": {"estado": "cancelado"}})


def set_event_auto_approve(event_id: str, value: bool):
    """Mismo interruptor que «Usar en las respuestas del agente» de /eventos/{id}."""
    eventos_col.update_one({"_id": ObjectId(event_id)}, {"$set": {"usar_en_agente": value}})
