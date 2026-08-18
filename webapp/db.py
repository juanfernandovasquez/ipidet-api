import re as _re
from datetime import datetime, timezone, date as _date, timedelta as _timedelta
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
companies_col = _db.companies
credito_col = _db.facturas_credito

MEDIOS_PAGO = [
    "Transferencia bancaria",
    "Depósito bancario",
    "Efectivo",
    "Yape / Plin",
    "Niubiz",
    "WooCommerce",
    "Cheque",
    "Otro",
]

BANCOS = [
    "BCP",
    "Interbank",
    "BBVA",
    "Scotiabank",
    "BanBif",
    "Banco de la Nación",
    "Otro",
]


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


def set_email_principal(member_id: str, email: str):
    # Quita principal de todos, luego lo pone solo en el indicado
    members_col.update_one(
        {"member_id": member_id},
        {"$set": {"emails.$[].principal": False}},
    )
    members_col.update_one(
        {"member_id": member_id, "emails.email": email},
        {"$set": {"emails.$.principal": True}},
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


def add_comentario(member_id: str, texto: str):
    comentario = {
        "id": str(ObjectId()),
        "texto": texto.strip(),
        "fecha": datetime.now(timezone.utc),
    }
    members_col.update_one(
        {"member_id": member_id},
        {"$push": {"comentarios": comentario}},
    )


def delete_comentario(member_id: str, comentario_id: str):
    members_col.update_one(
        {"member_id": member_id},
        {"$pull": {"comentarios": {"id": comentario_id}}},
    )


def update_member_estado(member_id: str, estado: str):
    members_col.update_one(
        {"member_id": member_id},
        {"$set": {"estado": estado}},
    )


# ── Payments ──────────────────────────────────────────────────────────────────

def get_payments(periodo: str = "2026", estado: str = "", empresa: str = "",
                 search: str = "", page: int = 1, per_page: int = 50,
                 comprobante_emitido: str = ""):
    query: dict = {}
    if periodo:
        query["periodo"] = periodo
    if estado:
        query["estado"] = estado
    if empresa:
        query["empresa_pagadora"] = {"$regex": empresa.strip(), "$options": "i"}
    if comprobante_emitido == "emitido":
        query["comprobante_emitido"] = True
    elif comprobante_emitido == "pendiente":
        query["$or"] = [{"comprobante_emitido": False}, {"comprobante_emitido": {"$exists": False}}]

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


def get_payment_with_member(payment_id: str) -> dict | None:
    """Devuelve un pago enriquecido con nombre_completo y email_principal del socio."""
    p = payments_col.find_one({"_id": ObjectId(payment_id)})
    if not p:
        return None
    m = members_col.find_one({"member_id": p["member_id"]}) or {}
    p["nombre_completo"] = f"{m.get('apellidos', '')} {m.get('nombres', '')}".strip()
    p["email_principal"] = next(
        (e["email"] for e in m.get("emails", []) if e.get("principal") and e.get("estado") == "habilitado"),
        next((e["email"] for e in m.get("emails", []) if e.get("estado") == "habilitado"), ""),
    )
    return _clean(p)


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
                   num_comprobante: str = None, tipo_comprobante: str = None,
                   link_constancia: str = None, banco_origen: str = None,
                   comprobante_emitido: bool = None,
                   fecha_emision_comprobante: str = None):
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
        # auto-marcar emitido cuando se registra el número
        if num_comprobante:
            fields["comprobante_emitido"] = True
    if tipo_comprobante is not None:
        fields["tipo_comprobante"] = tipo_comprobante or None
    if link_constancia is not None:
        fields["link_constancia"] = link_constancia or None
    if banco_origen is not None:
        fields["banco_origen"] = banco_origen or None
    if comprobante_emitido is not None and "comprobante_emitido" not in fields:
        fields["comprobante_emitido"] = comprobante_emitido
    if fecha_emision_comprobante is not None:
        fields["fecha_emision_comprobante"] = fecha_emision_comprobante or None
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


def update_cuota(payment_id: str, numero: int, estado: str, fecha_pago: str = None,
                 medio_pago: str = None, num_comprobante: str = None,
                 tipo_comprobante: str = None, link_constancia: str = None,
                 banco_origen: str = None):
    set_fields = {
        "cuotas.$[el].estado":    estado,
        "cuotas.$[el].fecha_pago": fecha_pago or None,
    }
    if medio_pago is not None:
        set_fields["cuotas.$[el].medio_pago"] = medio_pago or None
    if num_comprobante is not None:
        set_fields["cuotas.$[el].num_comprobante"] = num_comprobante or None
    if tipo_comprobante is not None:
        set_fields["cuotas.$[el].tipo_comprobante"] = tipo_comprobante or None
    if link_constancia is not None:
        set_fields["cuotas.$[el].link_constancia"] = link_constancia or None
    if banco_origen is not None:
        set_fields["cuotas.$[el].banco_origen"] = banco_origen or None
    payments_col.update_one(
        {"_id": ObjectId(payment_id)},
        {"$set": set_fields},
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


def add_pago_parcial(payment_id: str, monto: float, fecha_pago: str = None,
                     medio: str = None, num_comprobante: str = None,
                     tipo_comprobante: str = None, link_constancia: str = None,
                     banco_origen: str = None):
    doc = payments_col.find_one({"_id": ObjectId(payment_id)}, {"pagos_parciales": 1})
    parciales = doc.get("pagos_parciales", []) if doc else []
    numero = max((p.get("numero", 0) for p in parciales), default=0) + 1
    payments_col.update_one(
        {"_id": ObjectId(payment_id)},
        {"$push": {"pagos_parciales": {
            "numero":           numero,
            "monto":            monto,
            "fecha_pago":       fecha_pago,
            "medio_pago":       medio,
            "num_comprobante":  num_comprobante,
            "tipo_comprobante": tipo_comprobante,
            "link_constancia":  link_constancia,
            "banco_origen":     banco_origen,
        }}},
    )
    _sync_estado_from_parciales(payment_id)


def update_pago_parcial(payment_id: str, numero: int, monto: float = None,
                        fecha_pago: str = None, medio: str = None,
                        num_comprobante: str = None, tipo_comprobante: str = None,
                        link_constancia: str = None, banco_origen: str = None):
    set_fields = {}
    if monto is not None:
        set_fields["pagos_parciales.$[el].monto"] = monto
    if fecha_pago is not None:
        set_fields["pagos_parciales.$[el].fecha_pago"] = fecha_pago or None
    if medio is not None:
        set_fields["pagos_parciales.$[el].medio_pago"] = medio or None
    if num_comprobante is not None:
        set_fields["pagos_parciales.$[el].num_comprobante"] = num_comprobante or None
    if tipo_comprobante is not None:
        set_fields["pagos_parciales.$[el].tipo_comprobante"] = tipo_comprobante or None
    if link_constancia is not None:
        set_fields["pagos_parciales.$[el].link_constancia"] = link_constancia or None
    if banco_origen is not None:
        set_fields["pagos_parciales.$[el].banco_origen"] = banco_origen or None
    if set_fields:
        payments_col.update_one(
            {"_id": ObjectId(payment_id)},
            {"$set": set_fields},
            array_filters=[{"el.numero": numero}],
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


# ── Fraccionamientos ──────────────────────────────────────────────────────────

def _parse_fecha_cuota(s) -> "_date | None":
    """Parsea fecha_venc en formato YYYY-MM-DD, DD/MM/YYYY o DD.MM.YYYY."""
    if not s:
        return None
    m = _re.match(r'(\d{4})-(\d{2})-(\d{2})', str(s))
    if m:
        try: return _date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except: pass
    m = _re.match(r'(\d{1,2})[./](\d{1,2})[./](\d{4})', str(s))
    if m:
        try: return _date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except: pass
    return None


def get_fraccionamientos(periodo: str = "2026", alerta: str = "",
                          search: str = "", page: int = 1, per_page: int = 50):
    query: dict = {"estado": "fraccionamiento"}
    if periodo:
        query["periodo"] = periodo
    if search:
        rx = {"$regex": search, "$options": "i"}
        ids = {m["member_id"] for m in members_col.find(
            {"$or": [{"apellidos": rx}, {"nombres": rx}, {"member_id": rx},
                     {"emails.email": rx}]}, {"member_id": 1}
        )}
        if not ids:
            return [], 0, {"total": 0, "cuotas_vencidas": 0,
                           "total_cobrado": 0.0, "total_pendiente": 0.0}
        query["member_id"] = {"$in": list(ids)}

    docs = list(payments_col.find(query))
    member_ids = [d["member_id"] for d in docs]
    member_map = {m["member_id"]: m for m in
                  members_col.find({"member_id": {"$in": member_ids}})}

    today = _date.today()

    for d in docs:
        m = member_map.get(d["member_id"], {})
        d["nombre_completo"] = f"{m.get('apellidos', '')} {m.get('nombres', '')}".strip()
        d["email_principal"] = next(
            (e["email"] for e in m.get("emails", [])
             if e.get("principal") and e.get("estado") == "habilitado"),
            next((e["email"] for e in m.get("emails", [])
                  if e.get("estado") == "habilitado"), "")
        )

        cuotas = d.get("cuotas", [])
        pagadas  = [c for c in cuotas if c.get("estado") == "pagado"]
        pendientes = [c for c in cuotas if c.get("estado") != "pagado"]

        d["cuotas_total"]        = len(cuotas)
        d["cuotas_pagadas_count"] = len(pagadas)
        d["total_cobrado"]       = round(sum(c.get("monto", 0) for c in pagadas), 2)
        d["total_pendiente"]     = round(sum(c.get("monto", 0) for c in pendientes), 2)

        vencidas, proximas, sin_fecha = [], [], []
        for c in pendientes:
            fv = _parse_fecha_cuota(c.get("fecha_venc"))
            if fv is None:
                sin_fecha.append(c)
            elif fv < today:
                vencidas.append((fv, c))
            else:
                proximas.append((fv, c))

        proximas.sort(key=lambda x: x[0])
        vencidas.sort(key=lambda x: x[0])

        d["cuotas_vencidas_count"] = len(vencidas)
        d["cuotas_vencidas_lista"] = [c for _, c in vencidas]

        if proximas:
            d["proxima_cuota"] = proximas[0][1]
            d["proxima_cuota_fecha_iso"] = proximas[0][0].isoformat()
            dias = (proximas[0][0] - today).days
            d["proxima_cuota_dias"] = dias
        elif sin_fecha and pendientes:
            d["proxima_cuota"] = sin_fecha[0]
            d["proxima_cuota_fecha_iso"] = None
            d["proxima_cuota_dias"] = None
        else:
            d["proxima_cuota"] = None
            d["proxima_cuota_fecha_iso"] = None
            d["proxima_cuota_dias"] = None

        if vencidas:
            d["alerta"] = "vencida"
        elif proximas and proximas[0][0] <= today + _timedelta(days=7):
            d["alerta"] = "proxima_7d"
        elif proximas and proximas[0][0] <= today + _timedelta(days=30):
            d["alerta"] = "proxima_30d"
        elif pendientes and not proximas and not vencidas:
            d["alerta"] = "sin_fechas"
        else:
            d["alerta"] = "al_dia"

    if alerta:
        docs = [d for d in docs if d.get("alerta") == alerta]

    stats = {
        "total":           len(docs),
        "cuotas_vencidas": sum(d.get("cuotas_vencidas_count", 0) for d in docs),
        "total_cobrado":   round(sum(d.get("total_cobrado", 0) for d in docs), 2),
        "total_pendiente": round(sum(d.get("total_pendiente", 0) for d in docs), 2),
    }

    # Ordenar: vencidas primero, luego por nombre
    docs_sorted = sorted(docs, key=lambda d: (
        0 if d["alerta"] == "vencida" else
        1 if d["alerta"] == "proxima_7d" else
        2 if d["alerta"] == "proxima_30d" else
        3 if d["alerta"] == "sin_fechas" else 4,
        d.get("nombre_completo", "")
    ))

    total = len(docs_sorted)
    skip = (page - 1) * per_page
    return [_clean(d) for d in docs_sorted[skip:skip + per_page]], total, stats


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


# ── Empresas ──────────────────────────────────────────────────────────────────

_SEED_COMPANIES = [
    {"nombre": "EY",           "ruc": "", "tipo": "auditora"},
    {"nombre": "BDO",          "ruc": "", "tipo": "auditora"},
    {"nombre": "PwC",          "ruc": "", "tipo": "auditora"},
    {"nombre": "KPMG",         "ruc": "", "tipo": "auditora"},
    {"nombre": "PPU",          "ruc": "", "tipo": "firma_legal"},
    {"nombre": "SABHA S.A.C.", "ruc": "", "tipo": "empresa"},
]

def seed_companies():
    if companies_col.count_documents({}) == 0:
        companies_col.insert_many(_SEED_COMPANIES)

def get_companies(search: str = "") -> list:
    query = {}
    if search.strip():
        query = {"nombre": {"$regex": _re.escape(search.strip()), "$options": "i"}}
    docs = list(companies_col.find(query, {"_id": 1, "nombre": 1, "ruc": 1, "tipo": 1}).limit(10))
    return _clean(docs)

def add_company(nombre: str, ruc: str = "", tipo: str = "empresa"):
    companies_col.insert_one({"nombre": nombre.strip(), "ruc": ruc.strip(), "tipo": tipo})

def delete_company(company_id: str):
    companies_col.delete_one({"_id": ObjectId(company_id)})


# ── Facturación ───────────────────────────────────────────────────────────────

def get_comprobantes_pendientes(periodo: str = "2026", search: str = "") -> list:
    """Devuelve todos los comprobantes pendientes de emitir para el período."""
    q: dict = {"periodo": periodo}
    payments = list(payments_col.find(q))

    member_ids = [p["member_id"] for p in payments]
    member_map = {m["member_id"]: m for m in members_col.find({"member_id": {"$in": member_ids}})}

    result = []
    srx = search.strip().lower() if search.strip() else None

    for p in payments:
        m   = member_map.get(p["member_id"], {})
        nombre = f"{m.get('apellidos','').strip()} {m.get('nombres','').strip()}".strip()
        email  = next(
            (e["email"] for e in m.get("emails", []) if e.get("principal") and e.get("estado") == "habilitado"),
            next((e["email"] for e in m.get("emails", []) if e.get("estado") == "habilitado"), ""),
        )
        if srx and srx not in nombre.lower() and srx not in p["member_id"].lower() and srx not in email.lower():
            continue

        pid = str(p["_id"])
        base = dict(payment_id=pid, member_id=p["member_id"], nombre=nombre,
                    email=email, empresa_pagadora=p.get("empresa_pagadora"), periodo=periodo)

        # 1) Pago principal pagado sin comprobante
        if p.get("estado") == "pagado" and not p.get("num_comprobante"):
            # monto: no hay campo fijo en el schema para cuota anual; usar monto_total si existe
            monto_principal = p.get("monto") or p.get("monto_total") or None
            tiene_datos = p.get("fecha_pago") or p.get("medio_pago") or p.get("empresa_pagadora")
            result.append({**base, "tipo": "principal", "numero": None,
                           "monto": monto_principal,
                           "fecha_pago": p.get("fecha_pago"),
                           "tipo_comprobante": p.get("tipo_comprobante", ""),
                           "medio_pago": p.get("medio_pago", ""),
                           "sin_datos_pago": not tiene_datos})

        # 2) Cuotas pagadas sin comprobante
        for c in p.get("cuotas", []):
            if c.get("estado") == "pagado" and not c.get("num_comprobante"):
                tiene_datos = c.get("fecha_pago") or c.get("medio_pago") or p.get("empresa_pagadora")
                result.append({**base, "tipo": "cuota", "numero": c.get("numero"),
                               "monto": c.get("monto"), "fecha_pago": c.get("fecha_pago"),
                               "tipo_comprobante": c.get("tipo_comprobante", ""),
                               "medio_pago": c.get("medio_pago", ""),
                               "sin_datos_pago": not tiene_datos})

        # 3) Pagos parciales sin comprobante
        for pp in p.get("pagos_parciales", []):
            if pp.get("monto") and not pp.get("num_comprobante"):
                tiene_datos = pp.get("fecha_pago") or pp.get("medio_pago") or p.get("empresa_pagadora")
                result.append({**base, "tipo": "parcial", "numero": pp.get("numero"),
                               "monto": pp.get("monto"), "fecha_pago": pp.get("fecha_pago"),
                               "tipo_comprobante": pp.get("tipo_comprobante", ""),
                               "medio_pago": pp.get("medio_pago", ""),
                               "sin_datos_pago": not tiene_datos})

    return sorted(result, key=lambda x: x["nombre"])


def emitir_comprobante(payment_id: str, tipo: str, numero: int | None,
                       num_comprobante: str, tipo_comprobante: str,
                       fecha_emision: str = ""):
    pid = ObjectId(payment_id)
    fe = fecha_emision.strip() or None
    if tipo == "principal":
        payments_col.update_one({"_id": pid}, {"$set": {
            "num_comprobante": num_comprobante,
            "tipo_comprobante": tipo_comprobante or None,
            "fecha_emision_comprobante": fe,
            "comprobante_emitido": True,
        }})
    elif tipo == "cuota":
        payments_col.update_one(
            {"_id": pid, "cuotas.numero": numero},
            {"$set": {"cuotas.$.num_comprobante": num_comprobante,
                      "cuotas.$.tipo_comprobante": tipo_comprobante or None,
                      "cuotas.$.fecha_emision_comprobante": fe}},
        )
    elif tipo == "parcial":
        payments_col.update_one(
            {"_id": pid, "pagos_parciales.numero": numero},
            {"$set": {"pagos_parciales.$.num_comprobante": num_comprobante,
                      "pagos_parciales.$.tipo_comprobante": tipo_comprobante or None,
                      "pagos_parciales.$.fecha_emision_comprobante": fe}},
        )


def get_pendientes_socio(member_id: str, periodo: str) -> list:
    """Ítems con comprobante pendiente para un socio y período concretos."""
    p = payments_col.find_one({"member_id": member_id, "periodo": periodo})
    if not p:
        return []
    pid = str(p["_id"])
    result = []
    if p.get("estado") == "pagado" and not p.get("num_comprobante"):
        result.append({"payment_id": pid, "tipo": "principal", "numero": None,
                       "monto": None, "fecha_pago": p.get("fecha_pago"),
                       "medio_pago": p.get("medio_pago", "")})
    for c in p.get("cuotas", []):
        if c.get("estado") == "pagado" and not c.get("num_comprobante"):
            result.append({"payment_id": pid, "tipo": "cuota", "numero": c.get("numero"),
                           "monto": c.get("monto"), "fecha_pago": c.get("fecha_pago"),
                           "medio_pago": c.get("medio_pago", "")})
    for pp in p.get("pagos_parciales", []):
        if pp.get("monto") and not pp.get("num_comprobante"):
            result.append({"payment_id": pid, "tipo": "parcial", "numero": pp.get("numero"),
                           "monto": pp.get("monto"), "fecha_pago": pp.get("fecha_pago"),
                           "medio_pago": pp.get("medio_pago", "")})
    return result


def emitir_comprobante_batch(items: list, num_comprobante: str,
                              tipo_comprobante: str, fecha_emision: str):
    """Aplica el mismo comprobante a múltiples ítems."""
    for item in items:
        emitir_comprobante(item["payment_id"], item["tipo"],
                           item.get("numero"), num_comprobante,
                           tipo_comprobante, fecha_emision)


# ── Marketing ─────────────────────────────────────────────────────────────────

def get_marketing_stats(periodo: str = "2026") -> dict:
    total_activos = members_col.count_documents({"estado": "activo"})
    con_email = members_col.count_documents({
        "estado": "activo",
        "emails": {"$elemMatch": {"estado": "habilitado"}},
    })

    by_titulo = list(members_col.aggregate([
        {"$match": {"estado": "activo"}},
        {"$group": {"_id": "$titulo", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 12},
    ]))
    by_ubicacion = list(members_col.aggregate([
        {"$match": {"estado": "activo"}},
        {"$group": {"_id": "$ubicacion", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]))
    by_pago = list(payments_col.aggregate([
        {"$match": {"periodo": periodo}},
        {"$group": {"_id": "$estado", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]))
    by_empresa = list(payments_col.aggregate([
        {"$match": {"periodo": periodo, "empresa_pagadora": {"$nin": [None, ""]}}},
        {"$group": {"_id": "$empresa_pagadora", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 8},
    ]))

    return {
        "total_activos": total_activos,
        "con_email": con_email,
        "sin_email": total_activos - con_email,
        "by_titulo":    [{"label": d["_id"] or "Sin título",    "count": d["count"]} for d in by_titulo],
        "by_ubicacion": [{"label": d["_id"] or "Sin ubicación", "count": d["count"]} for d in by_ubicacion],
        "by_pago":      [{"label": d["_id"] or "Sin estado",    "count": d["count"]} for d in by_pago],
        "by_empresa":   [{"label": d["_id"],                    "count": d["count"]} for d in by_empresa],
    }


def get_marketing_emails(titulo: str = "", ubicacion: str = "",
                          estado_pago: str = "", empresa: str = "",
                          periodo: str = "2026") -> list:
    q: dict = {"estado": "activo", "emails": {"$elemMatch": {"estado": "habilitado"}}}
    if titulo:
        q["titulo"] = titulo
    if ubicacion:
        q["ubicacion"] = ubicacion

    members = list(members_col.find(q, {
        "member_id": 1, "apellidos": 1, "nombres": 1,
        "titulo": 1, "ubicacion": 1, "centro_trabajo": 1, "emails": 1,
    }))

    if estado_pago or empresa:
        pay_q: dict = {"periodo": periodo}
        if estado_pago:
            pay_q["estado"] = estado_pago
        if empresa:
            pay_q["empresa_pagadora"] = {"$regex": _re.escape(empresa.strip()), "$options": "i"}
        valid_ids = {p["member_id"] for p in payments_col.find(pay_q, {"member_id": 1})}
        members = [m for m in members if m["member_id"] in valid_ids]

    member_ids = [m["member_id"] for m in members]
    pay_map = {
        p["member_id"]: p.get("estado", "")
        for p in payments_col.find(
            {"member_id": {"$in": member_ids}, "periodo": periodo},
            {"member_id": 1, "estado": 1},
        )
    }

    result = []
    for m in members:
        email = next(
            (e["email"] for e in m.get("emails", []) if e.get("principal") and e.get("estado") == "habilitado"),
            next((e["email"] for e in m.get("emails", []) if e.get("estado") == "habilitado"), None),
        )
        if not email:
            continue
        result.append({
            "member_id":     m["member_id"],
            "nombre":        f"{m.get('apellidos', '')} {m.get('nombres', '')}".strip(),
            "email":         email,
            "titulo":        m.get("titulo", ""),
            "ubicacion":     m.get("ubicacion", ""),
            "centro_trabajo":m.get("centro_trabajo", ""),
            "estado_pago":   pay_map.get(m["member_id"], ""),
        })

    return sorted(result, key=lambda x: x["nombre"])


# ── Comunicaciones ─────────────────────────────────────────────────────────────

comunicaciones_col = _db.comunicaciones


def get_comunicacion_destinatarios(
    periodo: str = "2026",
    estados_pago: list[str] | None = None,
    empresa: str = "",
    ubicacion: str = "",
    titulo: str = "",
) -> list:
    """Socios activos con email habilitado que coincidan con los filtros."""
    q: dict = {"estado": "activo", "emails": {"$elemMatch": {"estado": "habilitado"}}}
    if titulo:
        q["titulo"] = titulo
    if ubicacion:
        q["ubicacion"] = ubicacion

    members = list(members_col.find(q, {
        "member_id": 1, "apellidos": 1, "nombres": 1,
        "titulo": 1, "ubicacion": 1, "emails": 1,
    }))

    if estados_pago or empresa:
        pay_q: dict = {"periodo": periodo}
        if estados_pago:
            pay_q["estado"] = {"$in": estados_pago}
        if empresa:
            pay_q["empresa_pagadora"] = {"$regex": _re.escape(empresa.strip()), "$options": "i"}
        valid_ids = {p["member_id"] for p in payments_col.find(pay_q, {"member_id": 1})}
        members = [m for m in members if m["member_id"] in valid_ids]

    member_ids = [m["member_id"] for m in members]
    pay_map = {
        p["member_id"]: p.get("estado", "")
        for p in payments_col.find(
            {"member_id": {"$in": member_ids}, "periodo": periodo},
            {"member_id": 1, "estado": 1},
        )
    }

    result = []
    for m in members:
        email = next(
            (e["email"] for e in m.get("emails", [])
             if e.get("principal") and e.get("estado") == "habilitado"),
            next((e["email"] for e in m.get("emails", [])
                  if e.get("estado") == "habilitado"), None),
        )
        if not email:
            continue
        result.append({
            "member_id":   m["member_id"],
            "nombre":      f"{m.get('apellidos', '')} {m.get('nombres', '')}".strip(),
            "email":       email,
            "titulo":      m.get("titulo", ""),
            "ubicacion":   m.get("ubicacion", ""),
            "estado_pago": pay_map.get(m["member_id"], "sin registro"),
        })

    return sorted(result, key=lambda x: x["nombre"])


def save_comunicacion_log(asunto: str, plantilla: str, filtros: dict,
                           destinatarios: list, usuario: str = "") -> str:
    from datetime import datetime, timezone
    doc = {
        "fecha":          datetime.now(timezone.utc),
        "usuario":        usuario,
        "asunto":         asunto,
        "plantilla":      plantilla,
        "filtros":        filtros,
        "total_enviados": len(destinatarios),
        "destinatarios":  destinatarios,
    }
    return str(comunicaciones_col.insert_one(doc).inserted_id)


def get_comunicaciones_history(limit: int = 20) -> list:
    docs = list(comunicaciones_col.find({}, {"destinatarios": 0}).sort("fecha", -1).limit(limit))
    return _clean(docs)


def get_member_titulos() -> list[str]:
    return sorted(members_col.distinct("titulo", {"estado": "activo", "titulo": {"$nin": [None, ""]}}))


def get_member_ubicaciones() -> list[str]:
    return sorted(members_col.distinct("ubicacion", {"estado": "activo", "ubicacion": {"$nin": [None, ""]}}))


# ── Facturas a crédito ────────────────────────────────────────────────────────

def _sync_credito_estado(doc: dict) -> str:
    if doc.get("estado") == "cobrado":
        return "cobrado"
    venc = doc.get("fecha_vencimiento")
    if venc and venc < _date.today().isoformat():
        return "vencido"
    return "pendiente"


def get_facturas_credito(empresa: str = "", estado: str = "") -> list:
    q: dict = {}
    if empresa:
        q["empresa"] = {"$regex": _re.escape(empresa.strip()), "$options": "i"}
    if estado:
        q["estado"] = estado
    docs = list(credito_col.find(q).sort("fecha_vencimiento", 1))
    for d in docs:
        d["estado"] = _sync_credito_estado(d)
    return _clean(docs)


def create_factura_credito(empresa: str, numero_factura: str, monto: float,
                            fecha_emision: str, fecha_vencimiento: str,
                            concepto: str = "", socios: list | None = None) -> str:
    doc = {
        "empresa": empresa.strip(),
        "numero_factura": numero_factura.strip(),
        "monto": monto,
        "fecha_emision": fecha_emision,
        "fecha_vencimiento": fecha_vencimiento,
        "concepto": concepto.strip(),
        "socios": socios or [],
        "estado": "pendiente",
        "created_at": datetime.now(timezone.utc),
    }
    return str(credito_col.insert_one(doc).inserted_id)


def update_factura_credito_estado(factura_id: str, estado: str,
                                   fecha_cobro: str = "") -> None:
    fields: dict = {"estado": estado}
    if estado == "cobrado" and fecha_cobro:
        fields["fecha_cobro"] = fecha_cobro
    credito_col.update_one({"_id": ObjectId(factura_id)}, {"$set": fields})


def delete_factura_credito(factura_id: str) -> None:
    credito_col.delete_one({"_id": ObjectId(factura_id)})


def get_credito_stats() -> dict:
    docs = list(credito_col.find({}))
    pendientes = [d for d in docs if _sync_credito_estado(d) == "pendiente"]
    vencidos   = [d for d in docs if _sync_credito_estado(d) == "vencido"]
    cobrados   = [d for d in docs if d.get("estado") == "cobrado"]
    return {
        "total_pendiente": sum(d.get("monto", 0) for d in pendientes),
        "total_vencido":   sum(d.get("monto", 0) for d in vencidos),
        "total_cobrado":   sum(d.get("monto", 0) for d in cobrados),
        "n_pendientes": len(pendientes),
        "n_vencidos":   len(vencidos),
    }


# ── Eventos ───────────────────────────────────────────────────────────────────

eventos_col = _db.eventos_ipidet


def get_eventos(search: str = "", estado: str = "", page: int = 1, per_page: int = 40):
    q: dict = {}
    if search:
        q["$or"] = [
            {"titulo": {"$regex": search, "$options": "i"}},
            {"lugar":  {"$regex": search, "$options": "i"}},
        ]
    if estado:
        q["estado"] = estado
    total = eventos_col.count_documents(q)
    skip = (page - 1) * per_page
    docs = list(eventos_col.find(q).sort("fecha", 1).skip(skip).limit(per_page))
    for d in docs:
        d["_id"] = str(d["_id"])
        n_ins = len(d.get("inscritos", []))
        cupo = d.get("cupo_max")
        d["n_inscritos"] = n_ins
        d["cupo_disponible"] = (cupo - n_ins) if cupo else None
        d["lleno"] = bool(cupo and n_ins >= cupo)
    return docs, total


def get_evento(evento_id: str) -> dict | None:
    try:
        doc = eventos_col.find_one({"_id": ObjectId(evento_id)})
    except Exception:
        return None
    if not doc:
        return None
    doc["_id"] = str(doc["_id"])
    inscritos = doc.get("inscritos", [])
    doc["n_inscritos"]  = len(inscritos)
    doc["n_asistentes"] = sum(1 for i in inscritos if i.get("asistio") is True)
    doc["n_ausentes"]   = sum(1 for i in inscritos if i.get("asistio") is False)
    doc["n_pendientes"] = sum(1 for i in inscritos if i.get("asistio") is None)
    cupo = doc.get("cupo_max")
    doc["cupo_disponible"] = (cupo - doc["n_inscritos"]) if cupo else None
    doc["lleno"] = bool(cupo and doc["n_inscritos"] >= cupo)
    return doc


def create_evento(titulo: str, descripcion: str, fecha: str, hora: str,
                  lugar: str, cupo_max: int | None) -> str:
    doc = {
        "titulo":      titulo,
        "descripcion": descripcion or "",
        "fecha":       fecha,
        "hora":        hora or "",
        "lugar":       lugar or "",
        "cupo_max":    cupo_max,
        "estado":      "activo",
        "inscritos":   [],
        "created_at":  datetime.now(timezone.utc),
    }
    return str(eventos_col.insert_one(doc).inserted_id)


def update_evento(evento_id: str, titulo: str, descripcion: str, fecha: str,
                  hora: str, lugar: str, cupo_max: int | None, estado: str):
    eventos_col.update_one(
        {"_id": ObjectId(evento_id)},
        {"$set": {
            "titulo":      titulo,
            "descripcion": descripcion or "",
            "fecha":       fecha,
            "hora":        hora or "",
            "lugar":       lugar or "",
            "cupo_max":    cupo_max,
            "estado":      estado,
        }},
    )


def delete_evento(evento_id: str):
    eventos_col.update_one(
        {"_id": ObjectId(evento_id)},
        {"$set": {"estado": "cancelado"}},
    )


def inscribir_socio(evento_id: str, member_id: str) -> str | None:
    """Inscribe al socio. Devuelve None si OK, mensaje de error si falla."""
    evento = eventos_col.find_one({"_id": ObjectId(evento_id)})
    if not evento:
        return "Evento no encontrado"
    inscritos = evento.get("inscritos", [])
    if any(i["member_id"] == member_id for i in inscritos):
        return "El socio ya está inscrito"
    cupo = evento.get("cupo_max")
    if cupo and len(inscritos) >= cupo:
        return "El evento está lleno"
    member = members_col.find_one({"member_id": member_id})
    if not member:
        return "Socio no encontrado"
    email = next((e["email"] for e in member.get("emails", [])
                  if e.get("estado") == "habilitado"), "")
    nombre = f"{member.get('apellidos', '')} {member.get('nombres', '')}".strip()
    entrada = {
        "member_id": member_id,
        "nombre":    nombre,
        "email":     email,
        "fecha_ins": str(_date.today()),
        "asistio":   None,
    }
    eventos_col.update_one(
        {"_id": ObjectId(evento_id)},
        {"$push": {"inscritos": entrada}},
    )
    return None


def desinscribir_socio(evento_id: str, member_id: str):
    eventos_col.update_one(
        {"_id": ObjectId(evento_id)},
        {"$pull": {"inscritos": {"member_id": member_id}}},
    )


def marcar_asistencia(evento_id: str, member_id: str, asistio: bool | None):
    eventos_col.update_one(
        {"_id": ObjectId(evento_id), "inscritos.member_id": member_id},
        {"$set": {"inscritos.$.asistio": asistio}},
    )


def get_eventos_proximos(limit: int = 5) -> list:
    """Para API de bots: próximos eventos activos desde hoy."""
    hoy = str(_date.today())
    docs = list(
        eventos_col.find({"estado": "activo", "fecha": {"$gte": hoy}})
        .sort("fecha", 1).limit(limit)
    )
    return _clean(docs)


def get_evento_stats(evento_id: str) -> dict:
    evento = get_evento(evento_id)
    if not evento:
        return {}
    return {
        "titulo":       evento["titulo"],
        "fecha":        evento["fecha"],
        "lugar":        evento["lugar"],
        "cupo_max":     evento.get("cupo_max"),
        "n_inscritos":  evento["n_inscritos"],
        "n_asistentes": evento["n_asistentes"],
        "n_ausentes":   evento["n_ausentes"],
        "n_pendientes": evento["n_pendientes"],
        "pct_asistencia": round(
            evento["n_asistentes"] / evento["n_inscritos"] * 100, 1
        ) if evento["n_inscritos"] else 0,
    }
