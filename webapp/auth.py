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


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def get_user(email: str) -> dict | None:
    doc = users_col.find_one({"email": email.lower().strip(), "active": True})
    if doc:
        doc["_id"] = str(doc["_id"])
    return doc


def list_users() -> list:
    docs = list(users_col.find({}).sort("email", 1))
    for d in docs:
        d["_id"] = str(d["_id"])
        d.pop("password_hash", None)
    return docs


def create_user(email: str, password: str, role: str = "viewer") -> str:
    email = email.lower().strip()
    existing = users_col.find_one({"email": email})
    if existing:
        users_col.update_one(
            {"email": email},
            {"$set": {"active": True, "password_hash": hash_password(password), "role": role}},
        )
        return str(existing["_id"])
    result = users_col.insert_one({
        "email": email,
        "password_hash": hash_password(password),
        "role": role,
        "active": True,
        "created_at": datetime.now(timezone.utc),
    })
    return str(result.inserted_id)


def delete_user(user_id: str):
    users_col.delete_one({"_id": ObjectId(user_id)})


def seed_admin():
    if users_col.count_documents({}) == 0 and ADMIN_EMAIL and ADMIN_PASSWORD:
        create_user(ADMIN_EMAIL, ADMIN_PASSWORD, role="admin")
