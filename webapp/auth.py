import os
import bcrypt
import certifi
from datetime import datetime, timezone
from bson import ObjectId
from pymongo import MongoClient
from config.settings import MONGODB_URI, DB_NAME, ADMIN_EMAIL, ADMIN_PASSWORD

_client = MongoClient(MONGODB_URI, tlsCAFile=certifi.where())
_db = _client[DB_NAME]
users_col = _db.users

# Secciones de la plataforma (clave, etiqueta, ícono FA)
SECCIONES = [
    ("members",          "Socios",           "fa-users"),
    ("billing",          "Cobranzas",        "fa-money-bill-wave"),
    ("fraccionamientos", "Fraccionamientos", "fa-calendar-days"),
    ("finanzas",         "Finanzas",         "fa-chart-line"),
    ("faqs",             "FAQs",             "fa-circle-question"),
    ("marketing",        "Marketing",        "fa-bullhorn"),
]
ALL_SECTIONS = [s[0] for s in SECCIONES]

# Mapeo prefijo de ruta → clave de sección
_PATH_MAP = [
    ("/members",          "members"),
    ("/billing",          "billing"),
    ("/fraccionamientos", "fraccionamientos"),
    ("/finanzas",         "finanzas"),
    ("/faqs",             "faqs"),
    ("/marketing",        "marketing"),
    ("/admin",            "__admin__"),
]

def section_for_path(path: str) -> str | None:
    """Devuelve la sección que controla el acceso a esa ruta, o None si es libre."""
    for prefix, section in _PATH_MAP:
        if path == prefix or path.startswith(prefix + "/") or path.startswith(prefix + "?"):
            return section
    return None  # /, /api/*, etc. → accesible a cualquier usuario autenticado


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def _serialize(doc: dict) -> dict:
    doc["_id"] = str(doc["_id"])
    doc.pop("password_hash", None)
    if isinstance(doc.get("created_at"), datetime):
        doc["created_at"] = doc["created_at"].isoformat()
    return doc


def get_user(email: str) -> dict | None:
    """Devuelve el usuario incluyendo password_hash (necesario para verificar en login)."""
    doc = users_col.find_one({"email": email.lower().strip(), "active": True})
    if not doc:
        return None
    d = dict(doc)
    d["_id"] = str(d["_id"])
    if isinstance(d.get("created_at"), datetime):
        d["created_at"] = d["created_at"].isoformat()
    return d


def list_users() -> list:
    return [_serialize(dict(d)) for d in users_col.find({}).sort("email", 1)]


def create_user(email: str, password: str, role: str = "viewer",
                permisos: list[str] | None = None) -> str:
    email = email.lower().strip()
    if permisos is None:
        permisos = ALL_SECTIONS if role == "viewer" else []
    existing = users_col.find_one({"email": email})
    if existing:
        users_col.update_one(
            {"email": email},
            {"$set": {"active": True, "password_hash": hash_password(password),
                      "role": role, "permisos": permisos}},
        )
        return str(existing["_id"])
    result = users_col.insert_one({
        "email": email,
        "password_hash": hash_password(password),
        "role": role,
        "permisos": permisos,
        "active": True,
        "created_at": datetime.now(timezone.utc),
    })
    return str(result.inserted_id)


def update_user_permisos(user_id: str, permisos: list[str]):
    users_col.update_one(
        {"_id": ObjectId(user_id)},
        {"$set": {"permisos": permisos}},
    )


def delete_user(user_id: str):
    users_col.delete_one({"_id": ObjectId(user_id)})


def seed_admin():
    if users_col.count_documents({}) == 0 and ADMIN_EMAIL and ADMIN_PASSWORD:
        create_user(ADMIN_EMAIL, ADMIN_PASSWORD, role="admin")
