from datetime import datetime, timezone
from bson import ObjectId
from pymongo import MongoClient
from config.settings import MONGODB_URI, DB_NAME


def _clean(obj):
    """Convierte ObjectId y datetime a tipos serializables por JSON."""
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    if isinstance(obj, ObjectId):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj

import certifi
_client = MongoClient(MONGODB_URI, tlsCAFile=certifi.where())
_db = _client[DB_NAME]

members_col = _db.members
payments_col = _db.payments
faqs_col = _db.faqs
events_col = _db.events


# ── Stats ─────────────────────────────────────────────────────────────────────

def get_stats() -> dict:
    total = members_col.count_documents({})
    activos = members_col.count_documents({"estado": "activo"})
    paid_2026 = payments_col.count_documents({"periodo": "2026", "estado": "pagado"})
    debe_2026 = payments_col.count_documents({"periodo": "2026", "estado": "debe"})
    fraccionamiento = payments_col.count_documents({"periodo": "2026", "estado": "fraccionamiento"})
    total_faqs = faqs_col.count_documents({"active": True})
    return {
        "total_miembros": total,
        "activos": activos,
        "pagados_2026": paid_2026,
        "deben_2026": debe_2026,
        "fraccionamiento_2026": fraccionamiento,
        "total_faqs": total_faqs,
    }


# ── Members ───────────────────────────────────────────────────────────────────

def get_members(search: str = "", estado: str = "", pago: str = "",
                ubicacion: str = "", page: int = 1, per_page: int = 50):
    query = {}
    if search:
        query["$or"] = [
            {"apellidos": {"$regex": search, "$options": "i"}},
            {"nombres":   {"$regex": search, "$options": "i"}},
            {"emails.email": {"$regex": search, "$options": "i"}},
            {"member_id": {"$regex": search, "$options": "i"}},
        ]
    if estado:
        query["estado"] = estado
    if ubicacion:
        query["ubicacion"] = {"$regex": ubicacion, "$options": "i"}

    # Filtro por pago 2026
    if pago:
        paying_ids = [
            p["member_id"] for p in
            payments_col.find({"periodo": "2026", "estado": pago}, {"member_id": 1})
        ]
        if paying_ids:
            query["member_id"] = {"$in": paying_ids}
        else:
            return [], 0

    total = members_col.count_documents(query)
    skip = (page - 1) * per_page
    docs = list(members_col.find(query).sort("apellidos", 1).skip(skip).limit(per_page))

    # Attach payment status for 2025 and 2026
    member_ids = [d["member_id"] for d in docs]
    all_payments = payments_col.find(
        {"member_id": {"$in": member_ids}, "periodo": {"$in": ["2025", "2026"]}}
    )
    pago_map: dict[str, dict] = {}
    for p in all_payments:
        mid = p["member_id"]
        periodo = p["periodo"]
        pago_map.setdefault(mid, {})[periodo] = p
    for d in docs:
        d["pago_2026"] = pago_map.get(d["member_id"], {}).get("2026", {})
        d["pago_2025"] = pago_map.get(d["member_id"], {}).get("2025", {})

    return [_clean(d) for d in docs], total


def get_member(member_id: str) -> dict | None:
    doc = members_col.find_one({"member_id": member_id})
    if not doc:
        return None
    doc["payments"] = list(payments_col.find({"member_id": member_id}).sort("periodo", 1))
    return _clean(doc)


def update_email_status(member_id: str, email: str, nuevo_estado: str):
    members_col.update_one(
        {"member_id": member_id, "emails.email": email},
        {"$set": {"emails.$.estado": nuevo_estado}},
    )


def add_email(member_id: str, email: str):
    email = email.strip().lower()
    members_col.update_one(
        {"member_id": member_id},
        {"$addToSet": {"emails": {"email": email, "estado": "habilitado", "principal": False}}},
    )


def update_member_notes(member_id: str, notas: str):
    members_col.update_one(
        {"member_id": member_id},
        {"$set": {"notas": notas}},
    )


def update_member_estado(member_id: str, estado: str):
    members_col.update_one(
        {"member_id": member_id},
        {"$set": {"estado": estado}},
    )


# ── Payments ──────────────────────────────────────────────────────────────────

def get_payments(periodo: str = "2026", estado: str = "", empresa: str = "",
                 search: str = "", page: int = 1, per_page: int = 50):
    query: dict = {}
    if periodo:
        query["periodo"] = periodo
    if estado:
        query["estado"] = estado
    if empresa:
        query["empresa_pagadora"] = {"$regex": empresa.strip(), "$options": "i"}

    if search:
        rx = {"$regex": search, "$options": "i"}
        # IDs desde members (nombre, ID, centro de trabajo, email)
        ids_members = {
            m["member_id"] for m in members_col.find(
                {"$or": [
                    {"apellidos":      rx},
                    {"nombres":        rx},
                    {"member_id":      rx},
                    {"centro_trabajo": rx},
                    {"emails.email":   rx},
                ]},
                {"member_id": 1},
            )
        }
        # IDs desde payments (empresa_pagadora, pagado_por)
        pay_q: dict = {"$or": [{"empresa_pagadora": rx}, {"pagado_por": rx}]}
        if periodo:
            pay_q["periodo"] = periodo
        ids_payments = {
            p["member_id"] for p in payments_col.find(pay_q, {"member_id": 1})
        }
        all_ids = list(ids_members | ids_payments)
        if not all_ids:
            return [], 0
        query["member_id"] = {"$in": all_ids}

    total = payments_col.count_documents(query)
    skip = (page - 1) * per_page
    docs = list(payments_col.find(query).sort("member_id", 1).skip(skip).limit(per_page))

    member_ids = [d["member_id"] for d in docs]
    member_map = {
        m["member_id"]: m for m in
        members_col.find({"member_id": {"$in": member_ids}})
    }
    for d in docs:
        m = member_map.get(d["member_id"], {})
        d["nombre_completo"] = f"{m.get('apellidos', '')} {m.get('nombres', '')}".strip()
        d["centro_trabajo"] = m.get("centro_trabajo", "")
        d["email_principal"] = next(
            (e["email"] for e in m.get("emails", []) if e.get("principal") and e.get("estado") == "habilitado"),
            next((e["email"] for e in m.get("emails", []) if e.get("estado") == "habilitado"), "")
        )
        cuotas = d.get("cuotas", [])
        d["cuotas_total"]   = len(cuotas)
        d["cuotas_pagadas"] = sum(1 for c in cuotas if c.get("estado") == "pagado")
        parciales = d.get("pagos_parciales", [])
        d["monto_pagado"] = sum(p.get("monto", 0) for p in parciales)
        monto_total = d.get("monto_total") or 0
        d["monto_pendiente"] = round(max(0.0, monto_total - d["monto_pagado"]), 2) if monto_total else None

    return [_clean(d) for d in docs], total


def get_payments_export(periodo: str = "2026", estado: str = "", empresa: str = "", search: str = "") -> list:
    query: dict = {}
    if periodo:
        query["periodo"] = periodo
    if estado:
        query["estado"] = estado
    if empresa:
        query["empresa_pagadora"] = {"$regex": empresa.strip(), "$options": "i"}
    if search:
        rx = {"$regex": search, "$options": "i"}
        ids_members = {
            m["member_id"] for m in members_col.find(
                {"$or": [
                    {"apellidos":      rx},
                    {"nombres":        rx},
                    {"member_id":      rx},
                    {"centro_trabajo": rx},
                    {"emails.email":   rx},
                ]},
                {"member_id": 1},
            )
        }
        pay_q2: dict = {"$or": [{"empresa_pagadora": rx}, {"pagado_por": rx}]}
        if periodo:
            pay_q2["periodo"] = periodo
        ids_payments = {
            p["member_id"] for p in payments_col.find(pay_q2, {"member_id": 1})
        }
        all_ids = list(ids_members | ids_payments)
        if not all_ids:
            return []
        query["member_id"] = {"$in": all_ids}

    docs = list(payments_col.find(query).sort("member_id", 1))
    member_ids = [d["member_id"] for d in docs]
    member_map = {
        m["member_id"]: m for m in
        members_col.find({"member_id": {"$in": member_ids}})
    }
    for d in docs:
        m = member_map.get(d["member_id"], {})
        d["apellidos"]      = m.get("apellidos", "")
        d["nombres"]        = m.get("nombres", "")
        d["centro_trabajo"] = m.get("centro_trabajo", "")
        d["email_principal"] = next(
            (e["email"] for e in m.get("emails", []) if e.get("principal") and e.get("estado") == "habilitado"),
            next((e["email"] for e in m.get("emails", []) if e.get("estado") == "habilitado"), ""),
        )
    return [_clean(d) for d in docs]


def update_payment(payment_id: str, estado: str, empresa: str = None,
                   fecha_pago: str = None, medio: str = None, pagado_por: str = None,
                   num_comprobante: str = None):
    fields = {"estado": estado}
    if empresa is not None:
        fields["empresa_pagadora"] = empresa or None
    if fecha_pago is not None:
        fields["fecha_pago"] = fecha_pago or None
    if medio is not None:
        fields["medio_pago"] = medio or None
    if pagado_por is not None:
        fields["pagado_por"] = pagado_por or None
    if num_comprobante is not None:
        fields["num_comprobante"] = num_comprobante or None
    payments_col.update_one({"_id": ObjectId(payment_id)}, {"$set": fields})


def _sync_estado_from_cuotas(payment_id: str):
    doc = payments_col.find_one({"_id": ObjectId(payment_id)}, {"cuotas": 1, "estado": 1})
    if not doc:
        return
    cuotas = doc.get("cuotas", [])
    if not cuotas:
        return
    if all(c.get("estado") == "pagado" for c in cuotas):
        new_estado = "pagado"
    else:
        new_estado = "fraccionamiento"
    if doc.get("estado") != new_estado:
        payments_col.update_one({"_id": ObjectId(payment_id)}, {"$set": {"estado": new_estado}})


def add_cuota(payment_id: str, monto: float, fecha_venc: str = None):
    doc = payments_col.find_one({"_id": ObjectId(payment_id)}, {"cuotas": 1})
    cuotas = doc.get("cuotas", []) if doc else []
    numero = max((c.get("numero", 0) for c in cuotas), default=0) + 1
    cuota = {
        "numero":    numero,
        "monto":     monto,
        "fecha_venc": fecha_venc or None,
        "fecha_pago": None,
        "estado":    "pendiente",
    }
    payments_col.update_one(
        {"_id": ObjectId(payment_id)},
        {"$push": {"cuotas": cuota}, "$set": {"estado": "fraccionamiento"}},
    )


def update_cuota(payment_id: str, numero: int, estado: str, fecha_pago: str = None):
    payments_col.update_one(
        {"_id": ObjectId(payment_id)},
        {"$set": {
            "cuotas.$[el].estado":    estado,
            "cuotas.$[el].fecha_pago": fecha_pago or None,
        }},
        array_filters=[{"el.numero": numero}],
    )
    _sync_estado_from_cuotas(payment_id)


def delete_cuota(payment_id: str, numero: int):
    payments_col.update_one(
        {"_id": ObjectId(payment_id)},
        {"$pull": {"cuotas": {"numero": numero}}},
    )
    _sync_estado_from_cuotas(payment_id)


# ── Pagos parciales ───────────────────────────────────────────────────────────

def set_monto_total(payment_id: str, monto_total: float):
    payments_col.update_one(
        {"_id": ObjectId(payment_id)},
        {"$set": {"monto_total": monto_total}},
    )
    _sync_estado_from_parciales(payment_id)


def add_pago_parcial(payment_id: str, monto: float, fecha_pago: str = None, medio: str = None):
    doc = payments_col.find_one({"_id": ObjectId(payment_id)}, {"pagos_parciales": 1})
    parciales = doc.get("pagos_parciales", []) if doc else []
    numero = max((p.get("numero", 0) for p in parciales), default=0) + 1
    payments_col.update_one(
        {"_id": ObjectId(payment_id)},
        {"$push": {"pagos_parciales": {
            "numero": numero,
            "monto": monto,
            "fecha_pago": fecha_pago,
            "medio_pago": medio,
        }}},
    )
    _sync_estado_from_parciales(payment_id)


def delete_pago_parcial(payment_id: str, numero: int):
    payments_col.update_one(
        {"_id": ObjectId(payment_id)},
        {"$pull": {"pagos_parciales": {"numero": numero}}},
    )
    _sync_estado_from_parciales(payment_id)


def _sync_estado_from_parciales(payment_id: str):
    doc = payments_col.find_one(
        {"_id": ObjectId(payment_id)},
        {"pagos_parciales": 1, "monto_total": 1, "estado": 1},
    )
    if not doc:
        return
    parciales = doc.get("pagos_parciales", [])
    monto_total = doc.get("monto_total") or 0
    monto_pagado = sum(p.get("monto", 0) for p in parciales)
    if not parciales:
        new_estado = "debe"
    elif monto_total and monto_pagado >= monto_total:
        new_estado = "pagado"
    else:
        new_estado = "parcial"
    if doc.get("estado") != new_estado:
        payments_col.update_one({"_id": ObjectId(payment_id)}, {"$set": {"estado": new_estado}})


# ── FAQs ──────────────────────────────────────────────────────────────────────

def get_faqs(search: str = "", category: str = "") -> list:
    query = {"active": True}
    if search:
        query["$or"] = [
            {"question": {"$regex": search, "$options": "i"}},
            {"answer":   {"$regex": search, "$options": "i"}},
        ]
    if category:
        query["category"] = {"$regex": category, "$options": "i"}
    docs = list(faqs_col.find(query).sort("category", 1))
    return [_clean(d) for d in docs]


def save_faq(question: str, answer: str, category: str) -> str:
    result = faqs_col.insert_one({
        "question": question, "answer": answer, "category": category,
        "active": True, "times_used": 0, "created_at": datetime.now(timezone.utc),
    })
    return str(result.inserted_id)


def delete_faq(faq_id: str):
    faqs_col.update_one({"_id": ObjectId(faq_id)}, {"$set": {"active": False}})


def get_faq_categories() -> list[str]:
    return faqs_col.distinct("category", {"active": True})
