import re as _re
import unicodedata as _unicodedata
from urllib.parse import quote as _quote, urlsplit as _urlsplit
from datetime import datetime, timezone, date as _date, timedelta as _timedelta
from bson import ObjectId
from pymongo.errors import DuplicateKeyError
from config import catalogos
from config.settings import DB_NAME


def _sf(val, default: float = 0.0) -> float:
    """Conversión segura a float — devuelve default si el valor es inválido."""
    try:
        return float(val) if val not in (None, "", "None") else default
    except (ValueError, TypeError):
        return default


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

from webapp.mongo_client import db as _db

members_col = _db.members
payments_col = _db.payments
faqs_col = _db.faqs
companies_col = _db.companies
credito_col = _db.facturas_credito
productos_col = _db.productos
comprobantes_col = _db.comprobantes
config_col = _db.config
publico_col = _db.publico
postulantes_col = _db.postulantes

redes_cuentas_col    = _db.redes_cuentas
redes_metricas_col   = _db.redes_metricas
redes_posts_col      = _db.redes_posts
redes_cronograma_col = _db.redes_cronograma

def get_medios_pago() -> list:
    return catalogos.get_catalogo("medios_pago")


def get_bancos() -> list:
    return catalogos.get_catalogo("bancos")


get_periodo_actual = catalogos.get_periodo_actual
get_periodo_anterior = catalogos.get_periodo_anterior
get_periodos = catalogos.get_periodos


def _periodo_o_actual(periodo: str | None) -> str:
    """None → período de trabajo. "" se respeta: en varios filtros significa «todos»."""
    return catalogos.get_periodo_actual() if periodo is None else periodo


# ── Stats ─────────────────────────────────────────────────────────────────────

def get_stats() -> dict:
    total = members_col.count_documents({})
    activos = members_col.count_documents({"estado": "activo"})
    periodo = catalogos.get_periodo_actual()
    pagados = payments_col.count_documents({"periodo": periodo, "estado": "pagado"})
    deben = payments_col.count_documents({"periodo": periodo, "estado": "debe"})
    fraccionamiento = payments_col.count_documents({"periodo": periodo, "estado": "fraccionamiento"})
    total_faqs = faqs_col.count_documents({"active": True})
    return {
        "total_miembros": total,
        "activos": activos,
        "periodo": periodo,
        "pagados": pagados,
        "deben": deben,
        "fraccionamiento": fraccionamiento,
        "total_faqs": total_faqs,
    }


def get_resumen_inicio() -> dict:
    """Datos de la pantalla de inicio: avance de cobranza del período de trabajo."""
    periodo = catalogos.get_periodo_actual()
    por_estado = {
        d["_id"]: d["n"] for d in payments_col.aggregate([
            {"$match": {"periodo": periodo}},
            {"$group": {"_id": "$estado", "n": {"$sum": 1}}},
        ])
    }
    # Quien no debe pagar este período no cuenta para el avance.
    fuera = ("exonerado", "no_aplica", "retirar")
    cobrables = sum(n for e, n in por_estado.items() if e not in fuera)
    pagados = por_estado.get("pagado", 0)
    return {
        "periodo": periodo,
        "por_estado": por_estado,
        "cobrables": cobrables,
        "pagados": pagados,
        "avance": round(pagados * 100 / cobrables) if cobrables else 0,
        "sin_comprobante": payments_col.count_documents({
            "periodo": periodo, "estado": "pagado", "comprobante_emitido": {"$ne": True},
        }),
        "socios_activos": members_col.count_documents({"estado": "activo"}),
        "socios_total": members_col.count_documents({}),
        "faqs": faqs_col.count_documents({"active": True}),
    }


def buscar_global(q: str, limite: int = 5) -> list:
    """Socios, público, empresas y comprobantes que coinciden con el texto."""
    q = q.strip()
    if len(q) < 2:
        return []
    rx = {"$regex": _re.escape(q), "$options": "i"}
    salida: list = []
    palabras = [{"$or": [{"apellidos": {"$regex": _re.escape(p), "$options": "i"}},
                         {"nombres": {"$regex": _re.escape(p), "$options": "i"}}]}
                for p in q.split()]
    socios = members_col.find(
        {"$or": [{"$and": palabras}, {"member_id": rx}, {"dni": rx}, {"emails.email": rx}]},
        {"member_id": 1, "apellidos": 1, "nombres": 1, "emails": 1},
    ).limit(limite)
    for m in socios:
        correo = next((e["email"] for e in m.get("emails", []) if e.get("principal")), "")
        salida.append({"grupo": "Socio", "titulo": f"{m.get('apellidos', '')} {m.get('nombres', '')}".strip(),
                       "detalle": " · ".join(x for x in (m["member_id"], correo) if x),
                       "url": f"/members/{m['member_id']}", "icono": "fa-solid fa-user"})
    for e in companies_col.find({"$or": [{"nombre": rx}, {"razon_social": rx}, {"ruc": rx}]},
                                {"nombre": 1, "ruc": 1}).limit(limite):
        salida.append({"grupo": "Empresa", "titulo": e.get("nombre", ""), "detalle": e.get("ruc") or "",
                       "url": f"/empresas?search={_quote(e.get('nombre', ''))}", "icono": "fa-solid fa-building"})
    for c in comprobantes_col.find({"$or": [{"numero": rx}, {"empresa": rx}]},
                                   {"numero": 1, "empresa": 1, "monto_total": 1}).sort("fecha_emision", -1).limit(limite):
        salida.append({"grupo": "Comprobante", "titulo": c.get("numero", ""), "detalle": c.get("empresa") or "",
                       "url": f"/comprobantes?search={_quote(c.get('numero', ''))}", "icono": "fa-solid fa-receipt"})
    for p in publico_col.find({"$or": [{"nombre": rx}, {"apellido": rx}, {"email": rx}]},
                              {"nombre": 1, "apellido": 1, "email": 1}).limit(limite):
        salida.append({"grupo": "Público", "titulo": f"{p.get('nombre', '')} {p.get('apellido', '')}".strip(),
                       "detalle": p.get("email") or "", "url": f"/publico/{p['_id']}",
                       "icono": "fa-solid fa-address-book"})
    for a in alianzas_col.find({"$or": [{"nombre": rx}, {"siglas": rx}]},
                               {"nombre": 1, "pais": 1}).limit(limite):
        salida.append({"grupo": "Alianza", "titulo": a.get("nombre", ""), "detalle": a.get("pais") or "",
                       "url": f"/alianzas?search={_quote(a.get('nombre', ''))}", "icono": "fa-solid fa-handshake"})
    for p in ponentes_col.find({"$or": [{"nombre": rx}, {"institucion": rx}]},
                               {"nombre": 1, "institucion": 1}).limit(limite):
        salida.append({"grupo": "Ponente", "titulo": p.get("nombre", ""), "detalle": p.get("institucion") or "",
                       "url": f"/ponentes?search={_quote(p.get('nombre', ''))}", "icono": "fa-solid fa-person-chalkboard"})
    return salida


# ── Members ───────────────────────────────────────────────────────────────────

def _push_or(query: dict, or_conditions: list) -> None:
    """Agrega una condición $or obligatoria sin pisar un $or existente."""
    new_clause = {"$or": or_conditions}
    if "$or" in query:
        query.setdefault("$and", []).extend([{"$or": query.pop("$or")}, new_clause])
    elif "$and" in query:
        query["$and"].append(new_clause)
    else:
        query["$or"] = or_conditions


def get_members(search: str = "", estado: str = "", pago: str = "",
                ubicacion: str = "", wp: str = "", page: int = 1, per_page: int = 50,
                sort: str = "nombre", sort_dir: str = "asc",
                email_est: str = "", tiene_dni: str = "", tiene_celular: str = "",
                tipo_socio: str = "", alt_email_est: str = ""):
    from pymongo import ASCENDING, DESCENDING
    query = {}
    if search:
        words = [w for w in search.split() if len(w) >= 2]
        if len(words) > 1:
            query["$and"] = [
                {"$or": [
                    {"apellidos": {"$regex": _re.escape(w), "$options": "i"}},
                    {"nombres":   {"$regex": _re.escape(w), "$options": "i"}},
                ]}
                for w in words
            ]
        else:
            query["$or"] = [
                {"apellidos":   {"$regex": _re.escape(search), "$options": "i"}},
                {"nombres":     {"$regex": _re.escape(search), "$options": "i"}},
                {"emails.email": {"$regex": _re.escape(search), "$options": "i"}},
                {"member_id":   {"$regex": _re.escape(search), "$options": "i"}},
                {"dni":         {"$regex": _re.escape(search), "$options": "i"}},
            ]
    if estado:
        query["estado"] = estado
    if ubicacion:
        query["ubicacion"] = {"$regex": _re.escape(ubicacion), "$options": "i"}
    if wp == "con":
        query["wp_user_id"] = {"$exists": True}
    elif wp == "sin":
        query["wp_user_id"] = {"$exists": False}

    periodo_actual = catalogos.get_periodo_actual()

    # Filtro por estado de pago del período de trabajo
    if pago:
        paying_ids = [
            p["member_id"] for p in
            payments_col.find({"periodo": periodo_actual, "estado": pago}, {"member_id": 1})
        ]
        if paying_ids:
            query["member_id"] = {"$in": paying_ids}
        else:
            return [], 0

    if tipo_socio:
        query["tipo_socio"] = tipo_socio

    # Filtros de email
    if email_est == "habilitado":
        query["emails"] = {"$elemMatch": {"principal": True, "estado": "habilitado"}}
    elif email_est == "problema":
        query["emails"] = {"$elemMatch": {"principal": True, "estado": {"$in": ["rebotado", "inhabilitado"]}}}
    elif email_est == "rebotado":
        query["emails"] = {"$elemMatch": {"estado": "rebotado"}}
    elif email_est == "sin_email":
        _push_or(query, [{"emails": {"$exists": False}}, {"emails": {"$size": 0}}])

    if tiene_dni == "si":
        query["dni"] = {"$exists": True, "$nin": [None, ""]}
    elif tiene_dni == "no":
        _push_or(query, [{"dni": {"$exists": False}}, {"dni": {"$in": [None, ""]}}])

    if tiene_celular == "si":
        query["celular"] = {"$exists": True, "$nin": [None, ""]}
    elif tiene_celular == "no":
        _push_or(query, [{"celular": {"$exists": False}}, {"celular": {"$in": [None, ""]}}])

    if alt_email_est == "con_alt":
        query["emails"] = {"$elemMatch": {"principal": False}}
    elif alt_email_est == "sin_alt":
        _push_or(query, [
            {"emails": {"$not": {"$elemMatch": {"principal": False}}}},
            {"emails": {"$exists": False}}, {"emails": {"$size": 0}},
        ])
    elif alt_email_est == "alt_rebotado":
        query["emails"] = {"$elemMatch": {"principal": False, "estado": {"$in": ["rebotado", "inhabilitado"]}}}

    # Orden
    sort_key   = "apellidos" if sort == "nombre" else "member_id"
    sort_order = ASCENDING if sort_dir == "asc" else DESCENDING

    total = members_col.count_documents(query)
    skip = (page - 1) * per_page
    docs = list(members_col.find(query).sort(sort_key, sort_order).skip(skip).limit(per_page))

    # Estado de pago del período de trabajo
    member_ids = [d["member_id"] for d in docs]
    pago_map = {
        p["member_id"]: p
        for p in payments_col.find({"member_id": {"$in": member_ids}, "periodo": periodo_actual})
    }
    for d in docs:
        d["pago_actual"] = pago_map.get(d["member_id"], {})

    return [_clean(d) for d in docs], total


def get_members_export(search: str = "", estado: str = "", pago: str = "",
                       ubicacion: str = "", tipo_socio: str = "",
                       email_est: str = "", tiene_dni: str = "",
                       tiene_celular: str = "", alt_email_est: str = "") -> list:
    """Igual que get_members pero sin paginación — devuelve todos los docs."""
    docs, _ = get_members(
        search=search, estado=estado, pago=pago, ubicacion=ubicacion,
        tipo_socio=tipo_socio, email_est=email_est,
        tiene_dni=tiene_dni, tiene_celular=tiene_celular,
        alt_email_est=alt_email_est,
        page=1, per_page=9999,
    )
    return docs


def _next_member_id() -> str:
    """Devuelve el siguiente IPIDET-XXXX disponible."""
    docs = members_col.find(
        {"member_id": {"$regex": r"^IPIDET-\d+$"}},
        {"member_id": 1},
    )
    nums = []
    for d in docs:
        try:
            nums.append(int(d["member_id"].split("-")[1]))
        except (IndexError, ValueError):
            pass
    return f"IPIDET-{(max(nums) + 1 if nums else 1):04d}"


def create_member(
    apellidos: str,
    nombres: str,
    titulo: str = "",
    email: str = "",
    celular: str = "",
    centro_trabajo: str = "",
    ubicacion: str = "",
    fecha_ingreso: str = "",
    notas: str = "",
    periodo_actual: str | None = None,
    linkedin: str = "",
) -> str:
    """Crea un socio nuevo y su registro de pago del período actual. Devuelve el member_id."""
    linkedin = normalizar_linkedin(linkedin)
    periodo_actual = periodo_actual or catalogos.get_periodo_actual()
    member_id = _next_member_id()
    # Guard against race condition where two requests call _next_member_id() simultaneously
    if member_id != "IPIDET-?" and members_col.find_one({"member_id": member_id}, {"_id": 1}):
        return member_id
    emails = []
    if email.strip():
        emails = [{"email": email.strip().lower(), "estado": "habilitado", "principal": True}]

    fecha_ing = None
    if fecha_ingreso:
        try:
            fecha_ing = datetime.strptime(fecha_ingreso, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    members_col.insert_one({
        "member_id":       member_id,
        "apellidos":       apellidos.strip().upper(),
        "nombres":         nombres.strip().title(),
        "titulo":          titulo.strip(),
        "celular":         celular.strip(),
        "centro_trabajo":  centro_trabajo.strip(),
        "ubicacion":       ubicacion.strip(),
        "fecha_ingreso":   fecha_ing,
        "fecha_nacimiento": None,
        "estado":          "activo",
        "emails":          emails,
        "notas":           notas.strip(),
        "linkedin":        linkedin,
        "comentarios":     [],
        "created_at":      datetime.now(timezone.utc),
    })

    pago = {"member_id": member_id, "periodo": periodo_actual, "estado": "pendiente"}
    producto_id = _producto_cuota_id(periodo_actual, _tipo_cuota(ubicacion))
    if producto_id:
        pago["producto_id"] = producto_id
    payments_col.insert_one(pago)
    catalogos.invalidar()

    return member_id


def get_member(member_id: str) -> dict | None:
    doc = members_col.find_one({"member_id": member_id})
    if not doc:
        return None
    doc["payments"] = list(payments_col.find({"member_id": member_id}).sort("periodo", 1))
    return _clean(doc)


def detalle_comprobantes_de_pagos(payments: list[dict]) -> dict[str, list[dict]]:
    """Comprobantes de cada pago del socio, para la ficha: {payment_id: [entradas]}.

    Solo por FK (R8): `comprobante_id` del pago, de sus cuotas y de sus parciales, más los comprobantes
    cuyas líneas apuntan al pago (`items.payment_id`). Cada entrada dice de qué parte del pago es
    (pago, cuota n, parcial n) y trae solo las líneas de ese pago."""
    ids: set[str] = set()
    for p in payments:
        ids.update(x for x in [p.get("comprobante_id")]
                   + [c.get("comprobante_id") for c in p.get("cuotas") or []]
                   + [c.get("comprobante_id") for c in p.get("pagos_parciales") or []] if x)
    pay_ids = [str(p["_id"]) for p in payments]
    oids = [ObjectId(i) for i in ids if ObjectId.is_valid(i)]
    comps = {str(c["_id"]): c for c in comprobantes_col.find(
        {"$or": [{"_id": {"$in": oids}}, {"items.payment_id": {"$in": pay_ids}}]},
        {"xml_raw": 0, "sunat_xml": 0})}
    con_xml = {str(c["_id"]) for c in comprobantes_col.find(
        {"_id": {"$in": [ObjectId(i) for i in comps]}, "sunat_xml": {"$nin": [None, ""]}}, {"_id": 1})}

    def entrada(comp_id: str | None, parte: str, pid: str, extra: dict) -> dict:
        c = comps.get(comp_id or "")
        if not c:
            return {"parte": parte, "comprobante": None, **extra}
        lineas = [i for i in c.get("items") or [] if i.get("payment_id") == pid]
        return {"parte": parte, **extra, "comprobante": {
            **_clean({k: v for k, v in c.items() if k != "items"}),
            "lineas": [_clean(i) for i in lineas],
            "num_lineas": len(c.get("items") or []),
            "tiene_xml": comp_id in con_xml,
        }}

    out: dict[str, list[dict]] = {}
    for p in payments:
        pid = str(p["_id"])
        lista: list[dict] = []
        vistos: set[str] = set()
        if p.get("comprobante_id") or p.get("num_comprobante"):
            lista.append(entrada(p.get("comprobante_id"), "Pago", pid, {
                "num_texto": p.get("num_comprobante") or "", "fecha_pago": p.get("fecha_pago") or ""}))
            vistos.add(p.get("comprobante_id") or "")
        for etiqueta, clave in (("Cuota", "cuotas"), ("Pago parcial", "pagos_parciales")):
            for c in p.get(clave) or []:
                if not (c.get("comprobante_id") or c.get("num_comprobante")):
                    continue
                lista.append(entrada(c.get("comprobante_id"), f"{etiqueta} {c.get('numero', '')}".strip(), pid, {
                    "num_texto": c.get("num_comprobante") or "", "fecha_pago": c.get("fecha_pago") or "",
                    "monto_parte": c.get("monto")}))
                vistos.add(c.get("comprobante_id") or "")
        # Comprobantes que apuntan al pago desde sus líneas, aunque el pago no los tenga anotados.
        for cid, c in comps.items():
            if cid not in vistos and any(i.get("payment_id") == pid for i in c.get("items") or []):
                lista.append(entrada(cid, "Pago", pid, {"num_texto": c.get("numero", ""), "fecha_pago": ""}))
        out[pid] = lista
    return out


def update_email_status(member_id: str, email: str, nuevo_estado: str):
    members_col.update_one(
        {"member_id": member_id, "emails.email": email},
        {"$set": {"emails.$.estado": nuevo_estado}},
    )
    if nuevo_estado in ("inhabilitado", "rebotado"):
        _maybe_promote_secondary(member_id)


def _maybe_promote_secondary(member_id: str):
    """Si el principal está inhabilitado/rebotado y hay un secundario habilitado, lo promueve."""
    member = members_col.find_one({"member_id": member_id}, {"emails": 1})
    if not member:
        return
    emails = member.get("emails") or []
    principal = next((e for e in emails if e.get("principal")), None)
    if not principal or principal.get("estado") == "habilitado":
        return
    candidate = next((e for e in emails if not e.get("principal") and e.get("estado") == "habilitado"), None)
    if candidate:
        set_email_principal(member_id, candidate["email"])


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


def rename_email(member_id: str, old_email: str, new_email: str):
    """Cambia la dirección de un email manteniendo estado, principal y bounce_type."""
    new_email = new_email.strip().lower()
    members_col.update_one(
        {"member_id": member_id, "emails.email": old_email},
        {"$set": {"emails.$.email": new_email}},
    )


def _rx_email(email: str) -> dict:
    return {"$regex": f"^{_re.escape(email.strip())}$", "$options": "i"}


def registrar_rebote(email: str, tipo: str, motivo: str = "", origen: str = "") -> dict:
    """Un correo no se pudo entregar a `email` (aviso de Gmail o webhook de Brevo).

    hard → el email del socio queda «rebotado» (no recibe comunicaciones) y, si era el principal, pasa a
    principal el siguiente habilitado; en Público la ficha queda con `email_estado: rebotado`.
    soft (buzón lleno) → el email sigue habilitado, pero si era el principal y el socio tiene otro
    habilitado, ese otro pasa a principal. En Público solo se anota. Devuelve qué se tocó."""
    email = (email or "").strip().lower()
    res: dict = {"email": email, "tipo": tipo, "socios": [], "publico": []}
    if not email or tipo not in ("hard", "soft"):
        return res
    ahora = datetime.now(timezone.utc).isoformat()
    for m in members_col.find({"emails.email": _rx_email(email)}, {"member_id": 1, "emails": 1}):
        actual = next((e for e in m.get("emails") or [] if (e.get("email") or "").lower() == email), {})
        cambios = {"emails.$.bounce_type": tipo if not (tipo == "soft" and actual.get("bounce_type") == "hard") else "hard",
                   "emails.$.bounce_at": ahora, "emails.$.bounce_reason": motivo[:300],
                   "emails.$.bounce_origen": origen}
        if tipo == "hard":
            cambios["emails.$.estado"] = "rebotado"
        members_col.update_one({"member_id": m["member_id"], "emails.email": actual.get("email", email)},
                               {"$set": cambios, "$inc": {"emails.$.bounce_count": 1}})
        era_principal = bool(actual.get("principal"))
        nuevo = ""
        if tipo == "soft" and era_principal:
            # Buzón lleno: no se inhabilita (puede liberarse), pero se escribe al otro email si lo hay.
            otro = next((e for e in m.get("emails") or [] if e.get("estado") == "habilitado"
                         and (e.get("email") or "").lower() != email and e.get("bounce_type") != "soft"), None)
            if otro:
                set_email_principal(m["member_id"], otro["email"])
        if tipo == "hard" or era_principal:
            if tipo == "hard":
                _maybe_promote_secondary(m["member_id"])
            doc = members_col.find_one({"member_id": m["member_id"]}, {"emails": 1}) or {}
            p = next((e for e in doc.get("emails") or [] if e.get("principal")), {})
            nuevo = p.get("email", "") if p.get("estado") == "habilitado" and p.get("email", "").lower() != email else ""
        res["socios"].append({"member_id": m["member_id"], "era_principal": era_principal, "nuevo_principal": nuevo})
    for p in publico_col.find({"email": _rx_email(email)}, {"_id": 1, "email_estado": 1}):
        cambios = {"bounce_type": "hard" if tipo == "hard" or p.get("email_estado") == "rebotado" else "soft",
                   "bounce_at": ahora, "bounce_reason": motivo[:300], "bounce_origen": origen}
        if tipo == "hard":
            cambios["email_estado"] = "rebotado"
        publico_col.update_one({"_id": p["_id"]}, {"$set": cambios, "$inc": {"bounce_count": 1}})
        res["publico"].append(str(p["_id"]))
    return res


def mark_email_bounce(email: str, bounce_type: str = "hard", reason: str = ""):
    """Compatibilidad: rebote informado por Brevo."""
    return registrar_rebote(email, bounce_type, reason, origen="brevo")


def restaurar_email_rebotado(email: str, member_id: str = "", prospecto_id: str = "") -> None:
    """Deshace un rebote (falso positivo): el email vuelve a recibir. No cambia cuál es el principal."""
    email = (email or "").strip().lower()
    quitar = {"bounce_type": "", "bounce_at": "", "bounce_reason": "", "bounce_origen": "", "bounce_count": ""}
    if member_id:
        members_col.update_one(
            {"member_id": member_id, "emails.email": _rx_email(email)},
            {"$set": {"emails.$.estado": "habilitado"},
             "$unset": {f"emails.$.{k}": "" for k in quitar}})
    if prospecto_id and ObjectId.is_valid(prospecto_id):
        publico_col.update_one({"_id": ObjectId(prospecto_id)},
                               {"$unset": {"email_estado": "", **quitar}})


def emails_publico_rebotados(emails: list[str]) -> set[str]:
    lista = [e.strip().lower() for e in emails if e]
    return {(p.get("email") or "").lower() for p in publico_col.find(
        {"email": {"$in": lista}, "email_estado": "rebotado"}, {"email": 1})}


def get_bounced_emails() -> dict:
    """Rebotes registrados en socios y en Público, más recientes primero.

    Para cada socio dice si le queda un email que funcione (o hay que pedirle uno nuevo)."""
    socios = []
    for m in members_col.find({"emails.bounce_type": {"$exists": True}},
                              {"member_id": 1, "nombres": 1, "apellidos": 1, "estado": 1, "emails": 1}):
        emails = m.get("emails") or []
        principal = next((e for e in emails if e.get("principal")), {})
        valido = principal.get("email", "") if principal.get("estado") == "habilitado" else ""
        for e in emails:
            if not e.get("bounce_type"):
                continue
            socios.append({
                "member_id": m["member_id"], "nombres": m.get("nombres", ""), "apellidos": m.get("apellidos", ""),
                "estado_socio": m.get("estado", ""), "email": e.get("email", ""), "estado": e.get("estado", ""),
                "bounce_type": e.get("bounce_type"), "bounce_at": e.get("bounce_at", ""),
                "bounce_reason": e.get("bounce_reason", ""), "bounce_origen": e.get("bounce_origen", ""),
                "bounce_count": e.get("bounce_count", 1), "principal_valido": valido,
                "otros_validos": [x.get("email") for x in emails
                                  if x.get("estado") == "habilitado" and x.get("email") != valido],
            })
    publico = [{
        "_id": str(p["_id"]), "nombre": f"{p.get('nombre', '')} {p.get('apellido', '')}".strip(),
        "email": p.get("email", ""), "estado": p.get("estado", ""), "email_estado": p.get("email_estado", ""),
        "bounce_type": p.get("bounce_type"), "bounce_at": p.get("bounce_at", ""),
        "bounce_reason": p.get("bounce_reason", ""), "bounce_origen": p.get("bounce_origen", ""),
        "bounce_count": p.get("bounce_count", 1),
    } for p in publico_col.find({"bounce_type": {"$exists": True}})]
    socios.sort(key=lambda r: str(r["bounce_at"]), reverse=True)
    publico.sort(key=lambda r: str(r["bounce_at"]), reverse=True)
    return {"socios": socios, "publico": publico,
            "sin_email_valido": len({r["member_id"] for r in socios if not r["principal_valido"]})}


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


def toggle_excluir_comunicaciones(member_id: str, excluir: bool):
    members_col.update_one(
        {"member_id": member_id},
        {"$set": {"excluir_comunicaciones": excluir}},
    )


def get_excluidos_comunicaciones() -> list:
    docs = list(members_col.find(
        {"excluir_comunicaciones": True},
        {"member_id": 1, "apellidos": 1, "nombres": 1, "emails": 1},
    ))
    result = []
    for m in docs:
        email = next(
            (e["email"] for e in m.get("emails", []) if e.get("principal")),
            next((e["email"] for e in m.get("emails", [])), ""),
        )
        result.append({
            "member_id": m["member_id"],
            "nombre": f"{m.get('apellidos', '')} {m.get('nombres', '')}".strip(),
            "email": email,
        })
    return sorted(result, key=lambda x: x["nombre"])


def update_member_tipo_socio(member_id: str, tipo_socio: str):
    if tipo_socio not in catalogos.get_valores_validos("tipos_socio"):
        return
    members_col.update_one(
        {"member_id": member_id},
        {"$set": {"tipo_socio": tipo_socio}},
    )


def update_member_dni(member_id: str, dni: str):
    members_col.update_one(
        {"member_id": member_id},
        {"$set": {"dni": dni}},
    )


def update_member_wp_user_id(member_id: str, wp_user_id: int | None):
    if wp_user_id:
        members_col.update_one({"member_id": member_id}, {"$set": {"wp_user_id": wp_user_id}})
    else:
        members_col.update_one({"member_id": member_id}, {"$unset": {"wp_user_id": ""}})


def normalizar_linkedin(valor: str | None) -> str:
    """Devuelve la URL limpia del perfil, "" si viene vacío. ValueError si no es de LinkedIn."""
    texto = (valor or "").strip()
    if not texto:
        return ""
    if not _re.match(r"^https?://", texto, _re.I):
        texto = "https://" + texto
    partes = _urlsplit(texto)
    host = (partes.hostname or "").lower()
    ruta = partes.path.rstrip("/")
    if not (host == "linkedin.com" or host.endswith(".linkedin.com")) or not ruta:
        raise ValueError("El LinkedIn debe ser un enlace de perfil, por ejemplo https://www.linkedin.com/in/nombre")
    return f"https://{host}{ruta}"


def update_member(member_id: str, fields: dict) -> None:
    if "linkedin" in fields:
        fields = {**fields, "linkedin": normalizar_linkedin(fields["linkedin"])}
    ALLOWED = {"apellidos", "nombres", "titulo", "centro_trabajo", "celular",
               "ubicacion", "tipo_socio", "estado", "dni",
               "fecha_ingreso", "fecha_nacimiento", "wp_user_id", "linkedin"}
    update: dict = {}
    unset:  dict = {}
    for k, v in fields.items():
        if k not in ALLOWED:
            continue
        if k == "apellidos":
            update[k] = (v or "").strip().upper()
        elif k == "nombres":
            update[k] = (v or "").strip()
        elif k == "wp_user_id":
            if v:
                try:
                    update[k] = int(v)
                except (ValueError, TypeError):
                    pass
            else:
                unset[k] = ""
        else:
            update[k] = (v or "").strip() if isinstance(v, str) else v
    ops: dict = {}
    if update:
        ops["$set"] = update
    if unset:
        ops["$unset"] = unset
    if ops:
        members_col.update_one({"member_id": member_id}, ops)


def auto_set_wp_user_id(member_id: str, wp_user_id: int):
    """Guarda wp_user_id solo si el socio todavía no lo tiene. No pisa vínculos manuales."""
    members_col.update_one(
        {"member_id": member_id, "wp_user_id": {"$exists": False}},
        {"$set": {"wp_user_id": wp_user_id}},
    )


# ── Payments ──────────────────────────────────────────────────────────────────

def _tipo_cuota(ubicacion: str | None) -> str:
    """Tipo de producto de cuota según la ubicación del socio (Lima vs. provincia)."""
    return "cuota_anual" if (ubicacion or "").strip().lower() == "lima" else "cuota_provincia"


def _producto_cuota_id(periodo: str, tipo: str) -> str | None:
    prod = productos_col.find_one({"tipo": tipo, "periodo": periodo, "activo": True}, {"_id": 1})
    return str(prod["_id"]) if prod else None


def generar_cobros_periodo(periodo: str) -> dict:
    """Crea registros 'debe' para todos los socios activos sin registro en ese período."""
    producto_por_tipo = {t: _producto_cuota_id(periodo, t) for t in catalogos.TIPOS_CUOTA}

    activos = list(members_col.find({"estado": "activo"}, {"member_id": 1, "ubicacion": 1}))
    existentes = {
        p["member_id"]
        for p in payments_col.find({"periodo": periodo}, {"member_id": 1})
    }
    faltantes = [m for m in activos if m["member_id"] not in existentes]
    creados = 0
    ya_existian = len(existentes)
    for m in faltantes:
        prod_id = producto_por_tipo[_tipo_cuota(m.get("ubicacion"))]
        doc: dict = {"member_id": m["member_id"], "periodo": periodo, "estado": "debe"}
        if prod_id:
            doc["producto_id"] = prod_id
        result = payments_col.update_one(
            {"member_id": m["member_id"], "periodo": periodo},
            {"$setOnInsert": doc},
            upsert=True,
        )
        if result.upserted_id:
            creados += 1
        else:
            ya_existian += 1
    catalogos.invalidar()
    return {"creados": creados, "ya_existian": ya_existian, "total_activos": len(activos)}


def get_payments(periodo: str | None = None, estado: str = "", empresa: str = "",
                 search: str = "", page: int = 1, per_page: int = 50,
                 comprobante_emitido: str = ""):
    periodo = _periodo_o_actual(periodo)
    query: dict = {}
    if periodo:
        query["periodo"] = periodo
    if estado:
        query["estado"] = estado
    if empresa:
        query.update(_build_empresa_filter(empresa))
    if comprobante_emitido == "emitido":
        query["comprobante_emitido"] = True
    elif comprobante_emitido == "pendiente":
        _push_or(query, [{"comprobante_emitido": False}, {"comprobante_emitido": {"$exists": False}}])

    if search:
        rx = {"$regex": _re.escape(search), "$options": "i"}
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
        d["estado_socio"] = m.get("estado", "activo")
        d["email_principal"] = next(
            (e["email"] for e in m.get("emails", []) if e.get("principal") and e.get("estado") == "habilitado"),
            next((e["email"] for e in m.get("emails", []) if e.get("estado") == "habilitado"), "")
        )
        cuotas = d.get("cuotas", [])
        d["cuotas_total"]   = len(cuotas)
        d["cuotas_pagadas"] = sum(1 for c in cuotas if c.get("estado") == "pagado")
        d["suma_cuotas"]    = round(sum(c.get("monto", 0) for c in cuotas), 2)
        monto_obj = d.get("monto_objetivo") or 0
        d["gap_cuotas"] = round(max(0.0, monto_obj - d["suma_cuotas"]), 2) if monto_obj else None
        todas_pagadas = d["cuotas_total"] > 0 and d["cuotas_pagadas"] == d["cuotas_total"]
        monto_ok = (not monto_obj) or (d["gap_cuotas"] is not None and d["gap_cuotas"] <= 0.01)
        d["cuotas_completo"] = todas_pagadas and monto_ok
        parciales = d.get("pagos_parciales", [])
        d["monto_pagado"] = sum(p.get("monto", 0) for p in parciales)
        monto_total = d.get("monto_total") or 0
        d["monto_pendiente"] = round(max(0.0, monto_total - d["monto_pagado"]), 2) if monto_total else None

    # Deudas en otros períodos para los mismos socios (una sola query)
    _ESTADOS_DEUDA = ["debe", "fraccionamiento", "parcial"]
    deuda_map: dict[str, list[str]] = {}
    for p in payments_col.find(
        {"member_id": {"$in": member_ids},
         "estado": {"$in": _ESTADOS_DEUDA},
         "periodo": {"$ne": periodo or ""}},
        {"member_id": 1, "periodo": 1},
    ):
        deuda_map.setdefault(p["member_id"], []).append(p["periodo"])
    for d in docs:
        d["deudas_otros_periodos"] = sorted(deuda_map.get(d["member_id"], []))

    return [_clean(d) for d in docs], total


def _cumple_estado(pago: dict | None, filtro: str) -> bool:
    """Filtro de estado de un período: un estado, «con_deuda» o «sin_registro»."""
    if not filtro:
        return True
    if filtro == "sin_registro":
        return pago is None
    if pago is None:
        return False
    if filtro == "con_deuda":
        return pago.get("estado") in catalogos.ESTADOS_CON_DEUDA
    return pago.get("estado") == filtro


def _tiene_comprobante(pago: dict) -> bool:
    return bool(pago.get("comprobante_emitido") or pago.get("comprobante_id"))


def _cumple_comprobante(pago: dict | None, filtro: str) -> bool:
    """«pendiente» = pagado y sin comprobante (lo que hay que emitir); «emitido» = con comprobante."""
    if not filtro:
        return True
    if pago is None:
        return False
    if filtro == "pendiente":
        return pago.get("estado") == "pagado" and not _tiene_comprobante(pago)
    return _tiene_comprobante(pago)


def get_conteos_cobranza() -> dict[str, dict[str, int]]:
    """Registros por estado en el período anterior y en el de trabajo (sin filtros), con «con_deuda»."""
    periodo_actual = catalogos.get_periodo_actual()
    periodos = [catalogos.get_periodo_anterior(periodo_actual), periodo_actual]
    out: dict[str, dict[str, int]] = {per: {} for per in periodos}
    for d in payments_col.aggregate([
        {"$match": {"periodo": {"$in": periodos}}},
        {"$group": {"_id": {"periodo": "$periodo", "estado": "$estado"}, "n": {"$sum": 1}}},
    ]):
        out[d["_id"]["periodo"]][d["_id"]["estado"]] = d["n"]
    for per, conteo in out.items():
        conteo["con_deuda"] = sum(n for e, n in conteo.items() if e in catalogos.ESTADOS_CON_DEUDA)
        conteo["sin_comprobante"] = payments_col.count_documents({
            "periodo": per, "estado": "pagado", "comprobante_emitido": {"$ne": True},
            "comprobante_id": {"$in": [None, ""]}})
    return out


def get_payments_grouped(estado: str = "", empresa: str = "", search: str = "",
                         comprobante_emitido: str = "", page: int = 1, per_page: int = 50,
                         estado_ant: str = "", comprobante_ant: str = "", estado_socio: str = ""):
    """Una fila por socio, con el pago del período de trabajo y el del anterior.

    Cada período se filtra por separado y los filtros se combinan (p. ej. debe el anterior y pagó el
    actual). `estado` / `comprobante_emitido` son del período de trabajo; `estado_ant` /
    `comprobante_ant`, del anterior. La empresa elige al socio si pagó por ella en alguno de los dos
    períodos, pero la fila siempre muestra los dos pagos."""
    periodo_actual = catalogos.get_periodo_actual()
    periodo_anterior = catalogos.get_periodo_anterior(periodo_actual)
    YEARS = [periodo_anterior, periodo_actual]

    # 1. Pagos de ambos períodos
    all_pays = list(payments_col.find({"periodo": {"$in": YEARS}}))

    # 2. Agrupar por socio
    by_mid: dict = {}
    for p in all_pays:
        mid = p["member_id"]
        if mid not in by_mid:
            by_mid[mid] = {"pago_anterior": None, "pago_actual": None}
        clave = "pago_actual" if p["periodo"] == periodo_actual else "pago_anterior"
        by_mid[mid][clave] = p

    # 3. Filtros por período (se combinan)
    by_mid = {mid: v for mid, v in by_mid.items()
              if _cumple_estado(v["pago_actual"], estado)
              and _cumple_estado(v["pago_anterior"], estado_ant)
              and _cumple_comprobante(v["pago_actual"], comprobante_emitido)
              and _cumple_comprobante(v["pago_anterior"], comprobante_ant)}

    # 4. Empresa pagadora en cualquiera de los dos períodos
    if empresa:
        con_empresa = {p["member_id"] for p in payments_col.find(
            {"periodo": {"$in": YEARS}, **_build_empresa_filter(empresa)}, {"member_id": 1})}
        by_mid = {mid: v for mid, v in by_mid.items() if mid in con_empresa}

    # 5. Fetch member info, applying search filter
    member_ids = list(by_mid.keys())
    member_query: dict = {"member_id": {"$in": member_ids}}
    if estado_socio:
        member_query["estado"] = estado_socio
    if search:
        rx = {"$regex": _re.escape(search), "$options": "i"}
        member_query["$and"] = [
            {"member_id": {"$in": member_ids}},
            {"$or": [
                {"apellidos":      rx},
                {"nombres":        rx},
                {"member_id":      rx},
                {"emails.email":   rx},
                {"centro_trabajo": rx},
            ]},
        ]
        del member_query["member_id"]

    members_map = {d["member_id"]: d for d in members_col.find(member_query)}

    # Also search in empresa_pagadora field of payments
    if search:
        rx = {"$regex": _re.escape(search), "$options": "i"}
        extra_mids = {p["member_id"] for p in payments_col.find(
            {"member_id": {"$in": member_ids},
             "periodo": {"$in": YEARS},
             "$or": [{"empresa_pagadora": rx}, {"pagado_por": rx}]},
            {"member_id": 1}
        )}
        extra_members = {d["member_id"]: d for d in members_col.find(
            {"member_id": {"$in": list(extra_mids)}, **({"estado": estado_socio} if estado_socio else {})}
        )}
        members_map.update(extra_members)

    def _enrich_pay(p: dict, mi: dict) -> dict:
        p = _clean(p)
        p["nombre_completo"] = f"{mi.get('apellidos','')} {mi.get('nombres','')}".strip()
        p["email_principal"] = next(
            (e["email"] for e in mi.get("emails", []) if e.get("principal") and e.get("estado") == "habilitado"),
            next((e["email"] for e in mi.get("emails", []) if e.get("principal")), "")
        )
        p["centro_trabajo"] = mi.get("centro_trabajo", "")
        p["estado_socio"]   = mi.get("estado", "activo")
        cuotas   = p.get("cuotas") or []
        parciales = p.get("pagos_parciales") or []
        p["cuotas_total"]   = len(cuotas)
        p["cuotas_pagadas"] = sum(1 for c in cuotas if c.get("estado") == "pagado")
        p["monto_pagado"]   = sum(c.get("monto", 0) for c in parciales)
        monto_total = p.get("monto_total") or 0
        p["monto_pendiente"] = round(max(0.0, monto_total - p["monto_pagado"]), 2) if monto_total else None
        return p

    # 6. Build result rows (only members that passed search)
    rows = []
    for mid, pays in by_mid.items():
        mi = members_map.get(mid)
        if mi is None:
            continue
        ep = next(
            (e["email"] for e in mi.get("emails", []) if e.get("principal") and e.get("estado") == "habilitado"),
            next((e["email"] for e in mi.get("emails", []) if e.get("principal")), "")
        )
        row = {
            "member_id":      mid,
            "nombre_completo": f"{mi.get('apellidos','')} {mi.get('nombres','')}".strip(),
            "email_principal": ep,
            "centro_trabajo":  mi.get("centro_trabajo", ""),
            "estado_socio":    mi.get("estado", "activo"),
            "pago_anterior": _enrich_pay(pays["pago_anterior"], mi) if pays["pago_anterior"] else None,
            "pago_actual":   _enrich_pay(pays["pago_actual"], mi) if pays["pago_actual"] else None,
        }
        rows.append(row)

    # 7. Sort by apellidos
    rows.sort(key=lambda r: r.get("nombre_completo", ""))

    total = len(rows)
    skip  = (page - 1) * per_page
    return rows[skip:skip + per_page], total


def get_nombre_producto_de_pago(pago: dict) -> str:
    """Nombre del producto vinculado al pago (FK producto_id)."""
    producto_id = pago.get("producto_id")
    if producto_id:
        try:
            prod = productos_col.find_one({"_id": ObjectId(producto_id)}, {"nombre": 1})
        except Exception:
            prod = None
        if prod and prod.get("nombre"):
            return prod["nombre"]
    return f"Cuota {pago.get('periodo', '')}".strip()


def get_nombre_producto_fraccionamiento() -> str:
    """Nombre del producto usado para cuotas de fraccionamiento sin producto propio."""
    prod = productos_col.find_one({"tipo": "fraccionamiento", "activo": True}, {"nombre": 1})
    return (prod or {}).get("nombre") or "Cuota de fraccionamiento"


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




def update_payment(payment_id: str, estado: str, empresa: str = None,
                   fecha_pago: str = None, medio: str = None, pagado_por: str = None,
                   num_comprobante: str = None, tipo_comprobante: str = None,
                   link_constancia: str = None, banco_origen: str = None,
                   comprobante_emitido: bool = None,
                   fecha_emision_comprobante: str = None,
                   comprobante_id: str = None):
    fields = {"estado": estado}
    if empresa is not None:
        if empresa.strip().lower() == "none":
            empresa = ""
        fields["empresa_pagadora"] = empresa or None
        if empresa:
            emp_doc = companies_col.find_one(
                {"nombre": {"$regex": f"^{_re.escape(empresa)}$", "$options": "i"}},
                {"_id": 1},
            )
            fields["empresa_id"] = str(emp_doc["_id"]) if emp_doc else None
        else:
            fields["empresa_id"] = None
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
    if comprobante_id is not None:
        fields["comprobante_id"] = comprobante_id or None
    payments_col.update_one(
        {"_id": ObjectId(payment_id)},
        {"$set": fields, "$unset": {"factura_credito_id": ""}},
    )


def _sync_estado_from_cuotas(payment_id: str):
    """Mantiene el estado como 'fraccionamiento' mientras haya cuotas.
    Nunca auto-cambia a 'pagado' — el administrador lo hace manualmente."""
    doc = payments_col.find_one(
        {"_id": ObjectId(payment_id)},
        {"cuotas": 1, "estado": 1},
    )
    if not doc or not doc.get("cuotas"):
        return
    if doc.get("estado") != "fraccionamiento":
        payments_col.update_one(
            {"_id": ObjectId(payment_id)},
            {"$set": {"estado": "fraccionamiento"}},
        )


def set_monto_objetivo(payment_id: str, monto_objetivo: float) -> None:
    payments_col.update_one(
        {"_id": ObjectId(payment_id)},
        {"$set": {"monto_objetivo": monto_objetivo}},
    )


def add_cuota(payment_id: str, monto: float, fecha_venc: str = None,
              producto_nombre: str = None):
    doc = payments_col.find_one(
        {"_id": ObjectId(payment_id)},
        {"cuotas": 1, "member_id": 1, "periodo": 1},
    )
    cuotas    = doc.get("cuotas", []) if doc else []
    numero    = max((c.get("numero", 0) for c in cuotas), default=0) + 1
    member_id = (doc or {}).get("member_id", "")
    periodo   = (doc or {}).get("periodo", "")
    cuota = {
        "numero":          numero,
        "monto":           monto,
        "fecha_venc":      fecha_venc or None,
        "fecha_pago":      None,
        "estado":          "pendiente",
        "producto_nombre": producto_nombre or None,
        "codigo_cuota":    f"{member_id}-{periodo}-C{numero:02d}" if member_id and periodo else None,
    }
    payments_col.update_one(
        {"_id": ObjectId(payment_id)},
        {"$push": {"cuotas": cuota}, "$set": {"estado": "fraccionamiento"}},
    )
    return cuota


def add_cuotas_batch(payment_id: str, cuotas: list) -> list:
    """Add multiple cuotas at once. cuotas = [{monto, fecha_venc}]"""
    doc = payments_col.find_one(
        {"_id": ObjectId(payment_id)},
        {"cuotas": 1, "member_id": 1, "periodo": 1},
    )
    existing  = doc.get("cuotas", []) if doc else []
    max_num   = max((c.get("numero", 0) for c in existing), default=0)
    member_id = (doc or {}).get("member_id", "")
    periodo   = (doc or {}).get("periodo", "")
    new_cuotas = []
    for c in cuotas:
        max_num += 1
        new_cuotas.append({
            "numero":          max_num,
            "monto":           _sf(c["monto"]),
            "fecha_venc":      c.get("fecha_venc") or None,
            "fecha_pago":      None,
            "estado":          "pendiente",
            "producto_nombre": c.get("producto_nombre") or None,
            "codigo_cuota":    f"{member_id}-{periodo}-C{max_num:02d}" if member_id and periodo else None,
        })
    if new_cuotas:
        payments_col.update_one(
            {"_id": ObjectId(payment_id)},
            {
                "$push": {"cuotas": {"$each": new_cuotas}},
                "$set":  {"estado": "fraccionamiento"},
            },
        )
    return new_cuotas


def decode_codigo_cuota(codigo: str) -> dict | None:
    """Parse 'IPIDET-0235-2026-C02' → {payment_id, cuota_numero, member_id, periodo}.
    Returns None if the code is invalid or no matching payment exists."""
    import re as _re
    m = _re.match(r'^(IPIDET-[\d?]+)-(\d{4})-C(\d+)$', codigo.strip(), _re.IGNORECASE)
    if not m:
        return None
    member_id = m.group(1).upper()
    periodo   = m.group(2)
    numero    = int(m.group(3))
    pay = payments_col.find_one({"member_id": member_id, "periodo": periodo}, {"_id": 1})
    if not pay:
        return None
    return {
        "payment_id":   str(pay["_id"]),
        "cuota_numero": numero,
        "member_id":    member_id,
        "periodo":      periodo,
    }


def backfill_cuotas_codigo() -> int:
    """Idempotent: fill missing codigo_cuota on existing cuota subdocuments."""
    count = 0
    for pay in payments_col.find(
        {"cuotas": {"$elemMatch": {"codigo_cuota": {"$exists": False}}}},
        {"member_id": 1, "periodo": 1, "cuotas": 1},
    ):
        member_id = pay.get("member_id", "")
        periodo   = pay.get("periodo", "")
        if not member_id or not periodo:
            continue
        cuotas  = pay.get("cuotas", [])
        updated = False
        for cuota in cuotas:
            if "codigo_cuota" not in cuota:
                n = cuota.get("numero", 0)
                cuota["codigo_cuota"] = f"{member_id}-{periodo}-C{n:02d}"
                updated = True
        if updated:
            payments_col.update_one({"_id": pay["_id"]}, {"$set": {"cuotas": cuotas}})
            count += 1
    return count


def update_cuota(payment_id: str, numero: int, estado: str, fecha_pago: str = None,
                 medio_pago: str = None, num_comprobante: str = None,
                 tipo_comprobante: str = None, link_constancia: str = None,
                 banco_origen: str = None, monto: float = None,
                 fecha_venc: str = None, comprobante_id: str = None):
    set_fields = {
        "cuotas.$[el].estado":    estado,
        "cuotas.$[el].fecha_pago": fecha_pago or None,
    }
    if monto is not None:
        set_fields["cuotas.$[el].monto"] = monto
    if fecha_venc is not None:
        set_fields["cuotas.$[el].fecha_venc"] = fecha_venc or None
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
    if comprobante_id is not None:
        set_fields["cuotas.$[el].comprobante_id"] = comprobante_id or None
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
    doc = payments_col.find_one({"_id": ObjectId(payment_id)}, {"cuotas": 1})
    if doc is not None:
        cuotas = doc.get("cuotas") or []
        if not cuotas:
            # Last cuota deleted — revert to "debe"
            payments_col.update_one(
                {"_id": ObjectId(payment_id)},
                {"$set": {"estado": "debe"}},
            )
        else:
            # Renumber remaining cuotas consecutively starting at 1
            cuotas_sorted = sorted(cuotas, key=lambda c: c.get("numero", 0))
            for i, c in enumerate(cuotas_sorted, 1):
                c["numero"] = i
            payments_col.update_one(
                {"_id": ObjectId(payment_id)},
                {"$set": {"cuotas": cuotas_sorted}},
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
                     banco_origen: str = None, comprobante_id: str = None):
    doc = payments_col.find_one({"_id": ObjectId(payment_id)}, {"pagos_parciales": 1})
    parciales = doc.get("pagos_parciales", []) if doc else []
    numero = max((p.get("numero", 0) for p in parciales), default=0) + 1
    parcial = {
        "numero":           numero,
        "monto":            monto,
        "fecha_pago":       fecha_pago,
        "medio_pago":       medio,
        "num_comprobante":  num_comprobante,
        "tipo_comprobante": tipo_comprobante,
        "link_constancia":  link_constancia,
        "banco_origen":     banco_origen,
        "comprobante_id":   comprobante_id or None,
    }
    payments_col.update_one(
        {"_id": ObjectId(payment_id)},
        {"$push": {"pagos_parciales": parcial}},
    )
    _sync_estado_from_parciales(payment_id)
    return parcial


def update_pago_parcial(payment_id: str, numero: int, monto: float = None,
                        fecha_pago: str = None, medio: str = None,
                        num_comprobante: str = None, tipo_comprobante: str = None,
                        link_constancia: str = None, banco_origen: str = None,
                        comprobante_id: str = None):
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
    if comprobante_id is not None:
        set_fields["pagos_parciales.$[el].comprobante_id"] = comprobante_id or None
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
    current = doc.get("estado", "")
    if not parciales:
        # Only revert to "debe" if we were actively in parcial mode; don't clobber
        # manual states like "pagado", "fraccionamiento", "exonerado", etc.
        new_estado = "debe" if current in ("parcial", "debe") else current
    elif monto_total and monto_pagado >= monto_total:
        new_estado = "pagado"
    else:
        new_estado = "parcial"
    if current != new_estado:
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


def get_fraccionamientos(periodo: str | None = None, alerta: str = "",
                          search: str = "", page: int = 1, per_page: int = 50):
    periodo = _periodo_o_actual(periodo)
    query: dict = {"estado": "fraccionamiento"}
    if periodo:
        query["periodo"] = periodo
    if search:
        rx = {"$regex": _re.escape(search), "$options": "i"}
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
        d["apellidos"]       = m.get("apellidos", "")
        d["titulo"]          = m.get("titulo", "")
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
        d["suma_cuotas"]         = round(sum(c.get("monto", 0) for c in cuotas), 2)
        monto_obj = d.get("monto_objetivo") or 0
        d["monto_objetivo"]      = monto_obj
        d["gap_cuotas"]          = round(max(0.0, monto_obj - d["suma_cuotas"]), 2) if monto_obj else None
        todas_pagadas = len(cuotas) > 0 and len(pendientes) == 0
        monto_ok = (not monto_obj) or (d["gap_cuotas"] is not None and d["gap_cuotas"] <= 0.01)
        d["cuotas_completo"]     = todas_pagadas and monto_ok

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


def _enrich_fracc_doc(d: dict, m: dict, today) -> dict:
    """Enriquece un doc de payments (fraccionamiento) con campos calculados."""
    d["nombre_completo"] = f"{m.get('apellidos', '')} {m.get('nombres', '')}".strip()
    d["apellidos"]       = m.get("apellidos", "")
    d["titulo"]          = m.get("titulo", "")
    d["email_principal"] = next(
        (e["email"] for e in m.get("emails", [])
         if e.get("principal") and e.get("estado") == "habilitado"),
        next((e["email"] for e in m.get("emails", [])
              if e.get("estado") == "habilitado"), "")
    )
    cuotas     = d.get("cuotas", [])
    pagadas    = [c for c in cuotas if c.get("estado") == "pagado"]
    pendientes = [c for c in cuotas if c.get("estado") != "pagado"]

    d["cuotas_total"]         = len(cuotas)
    d["cuotas_pagadas_count"] = len(pagadas)
    d["total_cobrado"]        = round(sum(c.get("monto", 0) for c in pagadas), 2)
    d["total_pendiente"]      = round(sum(c.get("monto", 0) for c in pendientes), 2)
    d["suma_cuotas"]          = round(sum(c.get("monto", 0) for c in cuotas), 2)
    monto_obj = d.get("monto_objetivo") or 0
    d["monto_objetivo"]       = monto_obj
    d["gap_cuotas"]           = round(max(0.0, monto_obj - d["suma_cuotas"]), 2) if monto_obj else None
    todas_pagadas = len(cuotas) > 0 and len(pendientes) == 0
    monto_ok = (not monto_obj) or (d["gap_cuotas"] is not None and d["gap_cuotas"] <= 0.01)
    d["cuotas_completo"]      = todas_pagadas and monto_ok

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
        d["proxima_cuota"]           = proximas[0][1]
        d["proxima_cuota_fecha_iso"] = proximas[0][0].isoformat()
        d["proxima_cuota_dias"]      = (proximas[0][0] - today).days
    elif sin_fecha and pendientes:
        d["proxima_cuota"]           = sin_fecha[0]
        d["proxima_cuota_fecha_iso"] = None
        d["proxima_cuota_dias"]      = None
    else:
        d["proxima_cuota"]           = None
        d["proxima_cuota_fecha_iso"] = None
        d["proxima_cuota_dias"]      = None

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
    return d


_ALERTA_ORDER = {"vencida": 0, "proxima_7d": 1, "proxima_30d": 2, "sin_fechas": 3, "al_dia": 4}


def get_fraccionamientos_agrupados(search: str = "", alerta: str = "",
                                    periodo: str = "", page: int = 1,
                                    per_page: int = 50):
    """Devuelve fraccionamientos agrupados por socio, paginando por socio."""
    query: dict = {"estado": "fraccionamiento"}
    if periodo:
        query["periodo"] = periodo
    if search:
        rx = {"$regex": _re.escape(search), "$options": "i"}
        ids = {m["member_id"] for m in members_col.find(
            {"$or": [{"apellidos": rx}, {"nombres": rx}, {"member_id": rx},
                     {"emails.email": rx}]}, {"member_id": 1}
        )}
        if not ids:
            return [], 0, {"total": 0, "cuotas_vencidas": 0,
                           "total_cobrado": 0.0, "total_pendiente": 0.0}
        query["member_id"] = {"$in": list(ids)}

    docs = list(payments_col.find(query))
    member_ids = list({d["member_id"] for d in docs})
    member_map = {m["member_id"]: m for m in
                  members_col.find({"member_id": {"$in": member_ids}})}

    today = _date.today()
    enriched = [_enrich_fracc_doc(d, member_map.get(d["member_id"], {}), today)
                for d in docs]

    if alerta:
        enriched = [d for d in enriched if d.get("alerta") == alerta]

    groups: dict[str, list] = {}
    for d in enriched:
        groups.setdefault(d["member_id"], []).append(d)

    miembros = []
    for mid, fracs in groups.items():
        fracs_sorted = sorted(fracs, key=lambda x: x.get("periodo", ""), reverse=True)
        worst_alerta = min(
            (f.get("alerta", "al_dia") for f in fracs_sorted),
            key=lambda a: _ALERTA_ORDER.get(a, 4),
            default="al_dia",
        )
        miembros.append({
            "member_id":       mid,
            "nombre_completo": fracs_sorted[0].get("nombre_completo", ""),
            "apellidos":       fracs_sorted[0].get("apellidos", ""),
            "titulo":          fracs_sorted[0].get("titulo", ""),
            "email_principal": fracs_sorted[0].get("email_principal", ""),
            "fraccionamientos": [_clean(f) for f in fracs_sorted],
            "worst_alerta":    worst_alerta,
            "total_pendiente": round(sum(f.get("total_pendiente", 0) for f in fracs_sorted), 2),
            "total_cobrado":   round(sum(f.get("total_cobrado", 0) for f in fracs_sorted), 2),
            "cuotas_vencidas": sum(f.get("cuotas_vencidas_count", 0) for f in fracs_sorted),
            "periodos_count":  len(fracs_sorted),
        })

    miembros.sort(key=lambda m: (
        _ALERTA_ORDER.get(m["worst_alerta"], 4),
        m.get("apellidos", ""),
    ))

    stats = {
        "total":           len(miembros),
        "cuotas_vencidas": sum(m["cuotas_vencidas"] for m in miembros),
        "total_cobrado":   round(sum(m["total_cobrado"] for m in miembros), 2),
        "total_pendiente": round(sum(m["total_pendiente"] for m in miembros), 2),
    }

    total = len(miembros)
    skip = (page - 1) * per_page
    return miembros[skip:skip + per_page], total, stats


# ── FAQs ──────────────────────────────────────────────────────────────────────

def get_faqs(search: str = "", category: str = "") -> list:
    query = {"active": True}
    if search:
        query["$or"] = [
            {"question": {"$regex": _re.escape(search), "$options": "i"}},
            {"answer":   {"$regex": _re.escape(search), "$options": "i"}},
        ]
    if category:
        query["category"] = {"$regex": _re.escape(category), "$options": "i"}
    docs = list(faqs_col.find(query).sort("category", 1))
    return [_clean(d) for d in docs]


def save_faq(question: str, answer: str, category: str, autorizada: bool = False) -> str:
    """`autorizada`: el agente de correos puede enviar sin revisión una respuesta basada en ella."""
    now = datetime.now(timezone.utc)
    result = faqs_col.insert_one({
        "question": question, "answer": answer, "category": category, "autorizada": autorizada,
        "active": True, "times_used": 0, "created_at": now, "updated_at": now,
    })
    return str(result.inserted_id)


def update_faq(faq_id: str, question: str, answer: str, category: str, autorizada: bool) -> None:
    faqs_col.update_one({"_id": ObjectId(faq_id)}, {"$set": {
        "question": question, "answer": answer, "category": category,
        "autorizada": autorizada, "updated_at": datetime.now(timezone.utc),
    }})


def set_faq_autorizada(faq_id: str, autorizada: bool) -> None:
    faqs_col.update_one({"_id": ObjectId(faq_id)},
                        {"$set": {"autorizada": autorizada, "updated_at": datetime.now(timezone.utc)}})


def delete_faq(faq_id: str):
    faqs_col.update_one({"_id": ObjectId(faq_id)}, {"$set": {"active": False}})


def get_faq_categories() -> list[str]:
    return faqs_col.distinct("category", {"active": True})


# ── Empresas ──────────────────────────────────────────────────────────────────

def get_all_companies(search: str = "", tipo: str = "") -> list:
    """Lista completa de empresas para la página de gestión."""
    query: dict = {}
    if search.strip():
        rx = {"$regex": _re.escape(search.strip()), "$options": "i"}
        query["$or"] = [{"nombre": rx}, {"ruc": rx}, {"razon_social": rx},
                        {"contacto_nombre": rx}, {"contacto_email": rx}]
    if tipo:
        query["tipo"] = tipo
    docs = list(companies_col.find(query).sort("nombre", 1))
    return _clean(docs)

def get_companies(search: str = "") -> list:
    """Autocomplete: busca por nombre, razón social o RUC (máx. 15 resultados)."""
    query: dict = {}
    if search.strip():
        rx = {"$regex": _re.escape(search.strip()), "$options": "i"}
        query["$or"] = [{"nombre": rx}, {"ruc": rx}, {"razon_social": rx}]
    docs = list(companies_col.find(query, {"_id": 1, "nombre": 1, "ruc": 1, "tipo": 1, "razon_social": 1, "contacto_email": 1}).sort("nombre", 1).limit(15))
    return _clean(docs)

def _build_empresa_filter(empresa: str) -> dict:
    """Filtro de payments por empresa: FK empresa_id primero; el texto solo cubre
    los pagos históricos sin FK. Acepta nombre, razón social o RUC."""
    texto = empresa.strip()
    rx = {"$regex": _re.escape(texto), "$options": "i"}
    conditions: list = [{"empresa_pagadora": rx}]
    matches = list(companies_col.find(
        {"$or": [{"nombre": rx}, {"ruc": rx}, {"razon_social": rx}]},
        {"nombre": 1},
    ))
    if matches:
        conditions.append({"empresa_id": {"$in": [str(m["_id"]) for m in matches]}})
        conditions.append({"empresa_pagadora": {"$in": [m["nombre"] for m in matches]}})
    return {"$or": conditions}

def add_company(nombre: str, ruc: str = "", razon_social: str = "",
                tipo: str = "empresa", contacto_nombre: str = "",
                contacto_email: str = "", contacto_telefono: str = "") -> str:
    result = companies_col.insert_one({
        "nombre":            nombre.strip(),
        "razon_social":      razon_social.strip(),
        "ruc":               ruc.strip(),
        "tipo":              tipo,
        "contacto_nombre":   contacto_nombre.strip(),
        "contacto_email":    contacto_email.strip(),
        "contacto_telefono": contacto_telefono.strip(),
        "activo":            True,
        "created_at":        datetime.now(timezone.utc),
    })
    return str(result.inserted_id)

def update_company(company_id: str, nombre: str, ruc: str = "",
                   razon_social: str = "", tipo: str = "empresa",
                   contacto_nombre: str = "", contacto_email: str = "",
                   contacto_telefono: str = ""):
    companies_col.update_one(
        {"_id": ObjectId(company_id)},
        {"$set": {
            "nombre":            nombre.strip(),
            "razon_social":      razon_social.strip(),
            "ruc":               ruc.strip(),
            "tipo":              tipo,
            "contacto_nombre":   contacto_nombre.strip(),
            "contacto_email":    contacto_email.strip(),
            "contacto_telefono": contacto_telefono.strip(),
        }},
    )

def delete_company(company_id: str):
    companies_col.delete_one({"_id": ObjectId(company_id)})


def get_socios_por_empresa(empresa_nombre: str, periodo: str) -> list:
    """Socios cuyo payment del período tiene empresa_pagadora == empresa_nombre (bidirectional, case-insensitive)."""
    emp = empresa_nombre.strip()
    company = companies_col.find_one(
        {"nombre": {"$regex": f"^{_re.escape(emp)}$", "$options": "i"}},
        {"nombre": 1, "razon_social": 1},
    )
    variants = {emp.lower()}
    if company:
        if company.get("nombre"):
            variants.add(company["nombre"].lower())
        if company.get("razon_social") and company["razon_social"].strip():
            variants.add(company["razon_social"].lower().strip())

    # Find matching empresa_pagadora values in payments (bidirectional substring)
    all_emp_vals = payments_col.distinct("empresa_pagadora", {"periodo": periodo})
    matching = [v for v in all_emp_vals if v and any(
        (v.lower() in var or var in v.lower()) for var in variants
    )]
    condiciones: list = [{"empresa_pagadora": {"$in": matching}}]
    if company:
        condiciones.append({"empresa_id": str(company["_id"])})
    if not matching and not company:
        return []
    pipeline = [
        {"$match": {"periodo": periodo, "$or": condiciones}},
        {"$lookup": {
            "from": "members",
            "localField": "member_id",
            "foreignField": "member_id",
            "as": "m",
        }},
        {"$unwind": {"path": "$m", "preserveNullAndEmptyArrays": True}},
        {"$project": {
            "member_id": 1,
            "estado": 1,
            "nombre_completo": {
                "$concat": [
                    {"$ifNull": ["$m.apellidos", ""]},
                    ", ",
                    {"$ifNull": ["$m.nombres", ""]},
                ]
            },
            "titulo": "$m.titulo",
            "ubicacion": "$m.ubicacion",
        }},
        {"$sort": {"nombre_completo": 1}},
    ]
    docs = list(payments_col.aggregate(pipeline))
    return _clean(docs)


# ── Facturación ───────────────────────────────────────────────────────────────

def get_comprobantes_pendientes(periodo: str | None = None, search: str = "") -> list:
    """Devuelve todos los comprobantes pendientes de emitir para el período."""
    periodo = _periodo_o_actual(periodo)
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
                       fecha_emision: str = "", comprobante_id: str = None):
    pid = ObjectId(payment_id)
    fe = fecha_emision.strip() or None
    cid = comprobante_id or None
    if tipo == "principal":
        upd = {
            "num_comprobante": num_comprobante,
            "tipo_comprobante": tipo_comprobante or None,
            "fecha_emision_comprobante": fe,
            "comprobante_emitido": True,
        }
        if cid:
            upd["comprobante_id"] = cid
        payments_col.update_one({"_id": pid}, {"$set": upd})
    elif tipo == "cuota":
        upd = {"cuotas.$.num_comprobante": num_comprobante,
               "cuotas.$.tipo_comprobante": tipo_comprobante or None,
               "cuotas.$.fecha_emision_comprobante": fe}
        if cid:
            upd["cuotas.$.comprobante_id"] = cid
        payments_col.update_one(
            {"_id": pid, "cuotas.numero": numero},
            {"$set": upd},
        )
    elif tipo == "parcial":
        upd = {"pagos_parciales.$.num_comprobante": num_comprobante,
               "pagos_parciales.$.tipo_comprobante": tipo_comprobante or None,
               "pagos_parciales.$.fecha_emision_comprobante": fe}
        if cid:
            upd["pagos_parciales.$.comprobante_id"] = cid
        payments_col.update_one(
            {"_id": pid, "pagos_parciales.numero": numero},
            {"$set": upd},
        )


def get_or_create_payment(member_id: str, periodo: str) -> str:
    """Devuelve el payment_id del socio para el período; lo crea si no existe.
    Usa upsert atómico para evitar duplicados bajo concurrencia."""
    existing = payments_col.find_one({"member_id": member_id, "periodo": periodo}, {"_id": 1})
    if existing:
        return str(existing["_id"])
    new_id = ObjectId()
    result = payments_col.update_one(
        {"member_id": member_id, "periodo": periodo},
        {"$setOnInsert": {
            "_id": new_id,
            "member_id": member_id,
            "periodo": periodo,
            "estado": "pendiente",
            "created_at": datetime.now(timezone.utc),
        }},
        upsert=True,
    )
    if result.upserted_id:
        return str(result.upserted_id)
    # Another process inserted it first
    p = payments_col.find_one({"member_id": member_id, "periodo": periodo}, {"_id": 1})
    return str(p["_id"]) if p else str(new_id)


_TIPOS_CUOTA_COMPLETA = ("cuota_anual", "cuota_provincia")
_TIPOS_FRACCION       = ("fraccionamiento",)
_TIPOS_PARCIAL        = ("parcial",)
_TIPOS_PAGO           = _TIPOS_CUOTA_COMPLETA + _TIPOS_FRACCION + _TIPOS_PARCIAL
_ESTADOS_NO_MODIFICAR = ("pagado", "exonerado", "no_aplica")

_CRUCE_LABELS: dict = {
    "ok":                  ("Cruzado",              "green"),
    "ya_pagado":           ("Ya pagado",             "blue"),
    "no_fk":               ("No vinculado",           "amber"),
    "no_product":          ("Producto no encontrado","red"),
    "tipo_no_pago":        ("Tipo no aplica",        "amber"),
    "sin_periodo":         ("Sin período",            "red"),
    "sin_cuota_num":       ("Sin N° cuota",           "red"),
    "estado_incompatible": ("Estado incompatible",    "amber"),
    "cuota_no_existe":     ("Cuota no existe",        "red"),
    "nota_credito":        ("Con nota de crédito",    "slate"),
    "error":               ("Error interno",          "red"),
}


def sync_comprobante_to_payments(items: list, numero: str, tipo: str,
                                  fecha_emision: str, empresa: str = "",
                                  comprobante_id: str = None,
                                  medio_pago: str = "",
                                  fecha_pago: str = "") -> list[dict]:
    """Cruza cada item del comprobante con el payment exacto via FK directa.
    Requiere payment_id + tipo_pago en cada item. Sin ellos el item queda sin sincronizar.
    Una nota de crédito nunca marca pagos: su efecto se aplica con registrar_nota_credito."""
    results: list[dict] = []
    seen: set = set()
    if tipo == "nota_credito":
        return results
    if comprobante_id:
        try:
            _c = comprobantes_col.find_one({"_id": ObjectId(comprobante_id)}, {"tipo": 1})
        except Exception:
            _c = None
        if _c and _c.get("tipo") == "nota_credito":
            return results

    def _res(item: dict, status: str, msg: str) -> dict:
        label, color = _CRUCE_LABELS.get(status, (status, "slate"))
        return {
            "member_id":    item.get("member_id") or "",
            "producto":     item.get("producto_nombre") or item.get("codigo_sunat") or "—",
            "codigo_sunat": item.get("codigo_sunat") or "",
            "status":       status,
            "label":        label,
            "color":        color,
            "msg":          msg,
        }

    for item in items:
        mid = item.get("member_id")
        if not mid:
            continue
        if item.get("nc_id"):
            # Línea anulada o corregida con nota de crédito: su pago lo decidió la nota.
            results.append(_res(item, "nota_credito", "Línea afectada por una nota de crédito: no se sincroniza"))
            continue

        monto        = _sf(item.get("monto"))
        direct_pid   = item.get("payment_id")
        direct_tipo  = item.get("tipo_pago")
        direct_cuota = item.get("cuota_numero")

        # ── FAST PATH: FK directa ────────────────────────────────────────────
        if direct_pid and direct_tipo:
            key = (direct_pid, direct_tipo, direct_cuota)
            if key in seen:
                continue
            seen.add(key)
            try:
                pay = payments_col.find_one({"_id": ObjectId(direct_pid)}, {"estado": 1, "cuotas": 1})
            except Exception:
                results.append(_res(item, "error", "payment_id inválido (FK directa)"))
                continue
            if not pay:
                results.append(_res(item, "error", "Payment no encontrado (FK directa)"))
                continue

            if direct_tipo == "principal":
                current_estado = pay.get("estado")
                if current_estado in _ESTADOS_NO_MODIFICAR:
                    # Ya finalizado: solo vincular el comprobante, sin cambiar estado.
                    fp_new = fecha_pago or fecha_emision or None
                    fp = fp_new if not pay.get("fecha_pago") else None
                    update_payment(direct_pid, estado=current_estado,
                                   num_comprobante=numero, tipo_comprobante=tipo,
                                   fecha_emision_comprobante=fecha_emision or None,
                                   fecha_pago=fp,
                                   empresa=empresa,
                                   comprobante_id=comprobante_id)
                    results.append(_res(item, "ok",
                        f"Comprobante vinculado (estado '{current_estado}' preservado)"))
                else:
                    # Pago completo (tipo principal) siempre resulta en "pagado".
                    # Si había un fraccionamiento con cuotas sin pagar, se cancela el plan.
                    if current_estado == "fraccionamiento":
                        cuotas = pay.get("cuotas") or []
                        ninguna_pagada = not any(c.get("estado") == "pagado" for c in cuotas)
                        if ninguna_pagada:
                            payments_col.update_one(
                                {"_id": ObjectId(direct_pid)},
                                {"$set": {"cuotas": []}},
                            )
                    update_payment(direct_pid, estado="pagado",
                                   fecha_pago=fecha_pago or fecha_emision or None,
                                   num_comprobante=numero, tipo_comprobante=tipo,
                                   fecha_emision_comprobante=fecha_emision or None,
                                   empresa=empresa,
                                   medio=medio_pago or None,
                                   comprobante_id=comprobante_id)
                    results.append(_res(item, "ok", "Pago marcado como pagado"))

            elif direct_tipo == "cuota":
                cuotas = pay.get("cuotas") or []
                target = next((c for c in cuotas if c.get("numero") == direct_cuota), None)
                if not target:
                    results.append(_res(item, "cuota_no_existe",
                        f"Cuota #{direct_cuota} no existe"))
                    continue
                if target.get("estado") == "pagado":
                    # Cuota ya pagada: solo vincular el comprobante
                    update_cuota(direct_pid, direct_cuota, estado="pagado",
                                 num_comprobante=numero, tipo_comprobante=tipo,
                                 comprobante_id=comprobante_id)
                    results.append(_res(item, "ok",
                        f"Comprobante vinculado en cuota #{direct_cuota} (ya estaba pagada)"))
                else:
                    update_cuota(direct_pid, direct_cuota, estado="pagado",
                                 fecha_pago=fecha_pago or fecha_emision or None,
                                 num_comprobante=numero, tipo_comprobante=tipo,
                                 medio_pago=medio_pago or None,
                                 comprobante_id=comprobante_id)
                    results.append(_res(item, "ok", f"Cuota #{direct_cuota} marcada como pagada"))

            elif direct_tipo == "parcial":
                # Idempotency: if a partial with this comprobante_id already exists, update it
                existing_pay = payments_col.find_one(
                    {"_id": ObjectId(direct_pid), "pagos_parciales.comprobante_id": comprobante_id},
                    {"pagos_parciales.$": 1},
                ) if comprobante_id else None
                if existing_pay and existing_pay.get("pagos_parciales"):
                    existing_num = existing_pay["pagos_parciales"][0].get("numero")
                    update_pago_parcial(direct_pid, existing_num, monto=monto,
                                        fecha_pago=fecha_pago or fecha_emision or None,
                                        num_comprobante=numero, tipo_comprobante=tipo,
                                        medio=medio_pago or None,
                                        comprobante_id=comprobante_id)
                    results.append(_res(item, "ok", f"Pago parcial #{existing_num} actualizado"))
                else:
                    add_pago_parcial(direct_pid, monto=monto,
                                     fecha_pago=fecha_pago or fecha_emision or None,
                                     num_comprobante=numero, tipo_comprobante=tipo,
                                     medio=medio_pago or None,
                                     comprobante_id=comprobante_id)
                    results.append(_res(item, "ok", f"Pago parcial de S/ {monto:.2f} registrado"))
            continue

        # ── SIN FK: no se sincroniza ─────────────────────────────────────────
        results.append(_res(item, "no_fk",
            "Sin payment_id + tipo_pago — vincula el comprobante desde Cobranzas"))

    return results


def mark_payment_empresa(payment_id: str, empresa: str, num_comprobante: str,
                          tipo_comprobante: str, fecha_emision: str,
                          comprobante_id: str | None = None):
    """Marca el pago principal como pagado por empresa y registra el comprobante."""
    from bson import ObjectId
    empresa_id = None
    if empresa:
        emp_doc = companies_col.find_one(
            {"nombre": {"$regex": f"^{_re.escape(empresa)}$", "$options": "i"}},
            {"_id": 1},
        )
        empresa_id = str(emp_doc["_id"]) if emp_doc else None
    payments_col.update_one(
        {"_id": ObjectId(payment_id)},
        {"$set": {
            "estado":             "pagado",
            "empresa_pagadora":   empresa,
            "empresa_id":         empresa_id,
            "num_comprobante":    num_comprobante or None,
            "comprobante_id":     comprobante_id or None,
            "tipo_comprobante":   tipo_comprobante or None,
            "fecha_emision_comprobante": fecha_emision or None,
            "comprobante_emitido": bool(num_comprobante),
        }},
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


# ── Marketing ─────────────────────────────────────────────────────────────────

def get_marketing_stats(periodo: str | None = None) -> dict:
    periodo = _periodo_o_actual(periodo)
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
                          periodo: str | None = None) -> list:
    periodo = _periodo_o_actual(periodo)
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
            pay_q.update(_build_empresa_filter(empresa))
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
        habilitados = [e["email"] for e in m.get("emails", []) if e.get("estado") == "habilitado"]
        principal   = next(
            (e["email"] for e in m.get("emails", []) if e.get("principal") and e.get("estado") == "habilitado"),
            habilitados[0] if habilitados else None,
        )
        if not principal:
            continue
        secundarios = [e for e in habilitados if e != principal]
        result.append({
            "member_id":       m["member_id"],
            "nombre":          f"{m.get('apellidos', '')} {m.get('nombres', '')}".strip(),
            "email":           principal,
            "email_secundario": secundarios[0] if secundarios else "",
            "titulo":          m.get("titulo", ""),
            "ubicacion":       m.get("ubicacion", ""),
            "centro_trabajo":  m.get("centro_trabajo", ""),
            "estado_pago":     pay_map.get(m["member_id"], ""),
        })

    return sorted(result, key=lambda x: x["nombre"])


# ── Campañas por lotes ─────────────────────────────────────────────────────────

campanas_col = _db["campanas"]


def create_campana(asunto: str, cuerpo: str, fuente: str, filtros: dict,
                   destinatarios: list, disclaimer: bool = True) -> str:
    """fuente: 'socios' | 'publico'. Congela la lista completa de destinatarios."""
    from datetime import datetime, timezone
    ahora = datetime.now(timezone.utc)
    dest_docs = [
        {
            "email":      d.get("email", ""),
            "nombre":     d.get("nombre", "") or d.get("nombres", ""),
            "apellido":   d.get("apellido", "") or d.get("apellidos", ""),
            "id":         str(d.get("member_id") or d.get("_id") or ""),
            "estado":     "pendiente",
            "enviado_at": None,
            "rebotado":   False,
            "error":      None,
        }
        for d in destinatarios if d.get("email")
    ]
    doc = {
        "asunto":        asunto.strip(),
        "cuerpo":        cuerpo.strip(),
        "fuente":        fuente,
        "filtros":       filtros,
        "disclaimer":    disclaimer,
        "estado":        "pendiente",
        "total":         len(dest_docs),
        "enviados":      0,
        "fallidos":      0,
        "rebotados":     0,
        "destinatarios": dest_docs,
        "created_at":    ahora,
        "updated_at":    ahora,
    }
    return str(campanas_col.insert_one(doc).inserted_id)


def get_campanas(limit: int = 50) -> list:
    docs = list(campanas_col.find({}, {"destinatarios": 0}).sort("created_at", -1).limit(limit))
    return _clean(docs)


def get_campana(campana_id: str) -> dict | None:
    from bson import ObjectId
    try:
        doc = campanas_col.find_one({"_id": ObjectId(campana_id)})
    except Exception:
        return None
    return _clean(doc) if doc else None


def marcar_rebote_campana(email: str) -> None:
    """Llamado desde el webhook de Brevo: marca el email como rebotado en todas las campañas activas."""
    from datetime import datetime, timezone
    campanas_col.update_many(
        {"destinatarios": {"$elemMatch": {"email": email, "estado": "enviado"}}},
        {"$set":  {"destinatarios.$[elem].rebotado": True},
         "$inc":  {"rebotados": 1}},
        array_filters=[{"elem.email": email, "elem.estado": "enviado"}],
    )


def delete_campana(campana_id: str) -> None:
    from bson import ObjectId
    campanas_col.delete_one({"_id": ObjectId(campana_id)})


# ── Comunicaciones ─────────────────────────────────────────────────────────────

comunicaciones_col = _db.comunicaciones
plantillas_col     = _db.plantillas_comunicaciones

_PLANTILLAS_DEFAULT = [
    {
        "key": "libre",
        "nombre": "Libre",
        "asunto": "",
        "cuerpo": "",
        "orden": 0,
    },
    {
        "key": "recordatorio",
        "nombre": "Recordatorio cuota",
        "asunto": "Recordatorio de cuota IPIDET",
        "cuerpo": (
            "Estimado/a asociado/a,\n\n"
            "Le recordamos que tiene pendiente el pago de su cuota anual de membresía IPIDET.\n\n"
            "Para realizar su pago o consultar opciones de fraccionamiento, comuníquese con nosotros.\n\n"
            "Cordialmente,\nEquipo IPIDET"
        ),
        "orden": 1,
    },
    {
        "key": "mora",
        "nombre": "Aviso de mora",
        "asunto": "Aviso de mora — IPIDET",
        "cuerpo": (
            "Estimado/a asociado/a,\n\n"
            "Le informamos que su cuota de membresía se encuentra vencida. "
            "Le pedimos regularizar su situación a la brevedad posible para mantener los beneficios de su membresía.\n\n"
            "Para coordinar el pago, comuníquese con nosotros.\n\n"
            "Cordialmente,\nEquipo IPIDET"
        ),
        "orden": 2,
    },
    {
        "key": "evento",
        "nombre": "Invitación a evento",
        "asunto": "Invitación — Próximo evento IPIDET",
        "cuerpo": (
            "Estimado/a asociado/a,\n\n"
            "Tenemos el agrado de invitarle al próximo evento organizado por IPIDET.\n\n"
            "[Detalla aquí: nombre del evento, fecha, hora, modalidad y enlace de inscripción]\n\n"
            "Esperamos contar con su presencia.\n\n"
            "Cordialmente,\nEquipo IPIDET"
        ),
        "orden": 3,
    },
    {
        "key": "actualizacion_datos",
        "nombre": "Actualización de datos",
        "asunto": "Actualización de datos — IPIDET",
        "cuerpo": (
            "Estimado/a asociado/a,\n\n"
            "Nos comunicamos para solicitarle la actualización de sus datos en nuestra base de socios "
            "(correo electrónico, celular, centro de trabajo y DNI).\n\n"
            "Puede enviarnos sus datos actualizados respondiendo a este correo.\n\n"
            "Su información actualizada nos permite brindarle un mejor servicio y mantenerle informado/a "
            "sobre las actividades y beneficios de IPIDET.\n\n"
            "Agradecemos su colaboración.\n\n"
            "Cordialmente,\nEquipo IPIDET"
        ),
        "orden": 4,
    },
]


def get_plantillas(tipo: str = "") -> list:
    q: dict = {"activa": {"$ne": False}}
    if tipo:
        q["$or"] = [{"tipo": tipo}, {"tipo": {"$exists": False}}] if tipo == "socios" else [{"tipo": tipo}]
    docs = list(plantillas_col.find(q).sort("orden", 1))
    if not docs and not tipo:
        _seed_plantillas()
        docs = list(plantillas_col.find(q).sort("orden", 1))
    return [_clean(d) for d in docs]


def _seed_plantillas():
    now = datetime.now(timezone.utc)
    for p in _PLANTILLAS_DEFAULT:
        plantillas_col.update_one(
            {"key": p["key"]},
            {"$setOnInsert": {**p, "activa": True, "created_at": now}},
            upsert=True,
        )


def create_plantilla(nombre: str, asunto: str, cuerpo: str, tipo: str = "socios") -> str:
    max_orden = plantillas_col.find_one(sort=[("orden", -1)]) or {}
    orden = (max_orden.get("orden") or 0) + 1
    import uuid as _uuid
    key = _uuid.uuid4().hex[:8]
    result = plantillas_col.insert_one({
        "key": key,
        "nombre": nombre,
        "asunto": asunto,
        "cuerpo": cuerpo,
        "tipo": tipo,
        "orden": orden,
        "activa": True,
        "created_at": datetime.now(timezone.utc),
    })
    return str(result.inserted_id)


def update_plantilla(plantilla_id: str, nombre: str = None, asunto: str = None, cuerpo: str = None, tipo: str = None):
    fields: dict = {"updated_at": datetime.now(timezone.utc)}
    if nombre is not None:
        fields["nombre"] = nombre
    if asunto is not None:
        fields["asunto"] = asunto
    if cuerpo is not None:
        fields["cuerpo"] = cuerpo
    if tipo is not None:
        fields["tipo"] = tipo
    plantillas_col.update_one({"_id": ObjectId(plantilla_id)}, {"$set": fields})


def delete_plantilla(plantilla_id: str):
    plantillas_col.update_one(
        {"_id": ObjectId(plantilla_id)},
        {"$set": {"activa": False}},
    )


def get_comunicacion_destinatarios(
    periodo: str | None = None,
    estados_pago: list[str] | None = None,
    empresa: str = "",
    ubicacion: str = "",
    titulo: str = "",
    centro_trabajo: str = "",
    keywords: str = "",
    excluir_estados_pago: list[str] | None = None,
    excluir_empresa: str = "",
    excluir_ubicacion: str = "",
    excluir_titulo: str = "",
    excluir_centro_trabajo: str = "",
    excluir_keywords: str = "",
    excluir_ids: list[str] | None = None,
    evento_id: str = "",
    evento_relacion: str = "",
) -> list:
    """Socios activos con email habilitado que coincidan con los filtros.

    evento_id + evento_relacion (asistio | inscrito | no_asistio | no_inscrito): según su relación con
    ese evento, por la plataforma (inscritos) o por Zoom (zoom_registrants unido por member_id)."""
    periodo = _periodo_o_actual(periodo)
    q: dict = {
        "estado": "activo",
        "emails": {"$elemMatch": {"estado": "habilitado"}},
        "excluir_comunicaciones": {"$ne": True},
    }
    if titulo:
        q["titulo"] = titulo
    if ubicacion:
        q["ubicacion"] = ubicacion
    if centro_trabajo:
        q["centro_trabajo"] = {"$regex": _re.escape(centro_trabajo.strip()), "$options": "i"}
    if keywords:
        kw = _re.escape(keywords.strip())
        q["$and"] = q.pop("$and", []) + [{"$or": [
            {"apellidos":     {"$regex": kw, "$options": "i"}},
            {"nombres":       {"$regex": kw, "$options": "i"}},
            {"centro_trabajo":{"$regex": kw, "$options": "i"}},
            {"notas":         {"$regex": kw, "$options": "i"}},
        ]}]
    if excluir_titulo:
        q["titulo"] = {**q.get("titulo", {}), "$ne": excluir_titulo} if isinstance(q.get("titulo"), dict) else {"$ne": excluir_titulo}
    if excluir_ubicacion:
        q["ubicacion"] = {**q.get("ubicacion", {}), "$ne": excluir_ubicacion} if isinstance(q.get("ubicacion"), dict) else {"$ne": excluir_ubicacion}
    if excluir_centro_trabajo:
        q["centro_trabajo"] = {
            **(q["centro_trabajo"] if isinstance(q.get("centro_trabajo"), dict) else {}),
            "$not": {"$regex": _re.escape(excluir_centro_trabajo.strip()), "$options": "i"},
        }
    if excluir_keywords:
        kw_ex = _re.escape(excluir_keywords.strip())
        q["$and"] = q.pop("$and", []) + [{"$nor": [
            {"apellidos":     {"$regex": kw_ex, "$options": "i"}},
            {"nombres":       {"$regex": kw_ex, "$options": "i"}},
            {"centro_trabajo":{"$regex": kw_ex, "$options": "i"}},
            {"notas":         {"$regex": kw_ex, "$options": "i"}},
        ]}]

    members = list(members_col.find(q, {
        "member_id": 1, "apellidos": 1, "nombres": 1,
        "titulo": 1, "ubicacion": 1, "emails": 1,
    }))

    # Filtros de pago solo aplican cuando hay un período definido
    if periodo and (estados_pago or empresa):
        pay_q: dict = {"periodo": periodo}
        if estados_pago:
            pay_q["estado"] = {"$in": estados_pago}
        if empresa:
            pay_q.update(_build_empresa_filter(empresa))
        valid_ids = {p["member_id"] for p in payments_col.find(pay_q, {"member_id": 1})}
        members = [m for m in members if m["member_id"] in valid_ids]

    if periodo and (excluir_estados_pago or excluir_empresa):
        ex_q: dict = {"periodo": periodo}
        if excluir_estados_pago:
            ex_q["estado"] = {"$in": excluir_estados_pago}
        if excluir_empresa:
            ex_q.update(_build_empresa_filter(excluir_empresa))
        excluded_ids = {p["member_id"] for p in payments_col.find(ex_q, {"member_id": 1})}
        members = [m for m in members if m["member_id"] not in excluded_ids]

    if excluir_ids:
        excluir_set = set(excluir_ids)
        members = [m for m in members if m["member_id"] not in excluir_set]

    if evento_id and evento_relacion in ("asistio", "inscrito", "no_asistio", "no_inscrito"):
        inscritos, asistieron = socios_de_evento(evento_id)
        regla = {"asistio": lambda mid: mid in asistieron,
                 "inscrito": lambda mid: mid in inscritos,
                 "no_asistio": lambda mid: mid in inscritos and mid not in asistieron,
                 "no_inscrito": lambda mid: mid not in inscritos}[evento_relacion]
        members = [m for m in members if regla(m["member_id"])]

    member_ids = [m["member_id"] for m in members]
    pay_map = {}
    if periodo and member_ids:
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
        emails_cc = [
            e["email"] for e in m.get("emails", [])
            if e.get("estado") == "habilitado" and e["email"] != email
        ]
        result.append({
            "member_id":   m["member_id"],
            "nombre":      f"{m.get('apellidos', '')} {m.get('nombres', '')}".strip(),
            "email":       email,
            "emails_cc":   emails_cc,
            "titulo":      m.get("titulo", ""),
            "ubicacion":   m.get("ubicacion", ""),
            "estado_pago": pay_map.get(m["member_id"], "sin registro") if periodo else "",
        })

    return sorted(result, key=lambda x: x["nombre"])


def save_comunicacion_log(asunto: str, plantilla: str, filtros: dict,
                           destinatarios: list, usuario: str = "",
                           fallidos_detalle: list | None = None,
                           campania: str = "", cuerpo: str = "") -> str:
    from datetime import datetime, timezone
    fd = fallidos_detalle or []
    member_ids_enviados = [
        d.get("member_id") for d in destinatarios
        if d.get("member_id")
    ]
    doc = {
        "fecha":               datetime.now(timezone.utc),
        "usuario":             usuario,
        "asunto":              asunto,
        "plantilla":           plantilla,
        "filtros":             filtros,
        "total_enviados":      len(destinatarios) - len(fd),
        "total_fallidos":      len(fd),
        "fallidos_detalle":    fd,
        "destinatarios":       destinatarios,
        "member_ids_enviados": member_ids_enviados,
    }
    if campania:
        doc["campania"] = campania
    if cuerpo:
        # Texto del mensaje (sin personalizar): el agente de correos lo usa para responder consultas sobre él.
        doc["cuerpo"] = cuerpo[:20000]
    return str(comunicaciones_col.insert_one(doc).inserted_id)


def get_campania_info(campania_nombre: str) -> dict:
    """Retorna lotes enviados y member_ids acumulados para una campaña."""
    if not campania_nombre.strip():
        return {"campania": "", "num_lotes": 0, "total_enviados": 0, "member_ids_enviados": [], "ultima_fecha": None}
    lotes = list(comunicaciones_col.find(
        {"campania": campania_nombre},
        {"member_ids_enviados": 1, "fecha": 1, "total_enviados": 1},
    ).sort("fecha", 1))
    seen: set = set()
    all_ids: list = []
    for lote in lotes:
        for mid in (lote.get("member_ids_enviados") or []):
            if mid and mid not in seen:
                seen.add(mid)
                all_ids.append(mid)
    return {
        "campania":            campania_nombre,
        "num_lotes":           len(lotes),
        "total_enviados":      len(all_ids),
        "member_ids_enviados": all_ids,
        "ultima_fecha":        lotes[-1]["fecha"].isoformat() if lotes else None,
    }


def get_campanias_recientes(limit: int = 10) -> list:
    """Retorna las campañas más recientes con conteo acumulado."""
    pipeline = [
        {"$match": {"campania": {"$exists": True, "$nin": ["", None]}}},
        {"$group": {
            "_id":            "$campania",
            "num_lotes":      {"$sum": 1},
            "total_enviados": {"$sum": "$total_enviados"},
            "ultima_fecha":   {"$max": "$fecha"},
        }},
        {"$sort": {"ultima_fecha": -1}},
        {"$limit": limit},
    ]
    return [
        {
            "nombre":         d["_id"],
            "num_lotes":      d["num_lotes"],
            "total_enviados": d["total_enviados"],
            "ultima_fecha":   d["ultima_fecha"].isoformat() if d.get("ultima_fecha") else None,
        }
        for d in comunicaciones_col.aggregate(pipeline)
    ]


def get_comunicaciones_history(limit: int = 20) -> list:
    docs = list(comunicaciones_col.find(
        {}, {"destinatarios": 0}
    ).sort("fecha", -1).limit(limit))
    return _clean(docs)


def get_member_titulos() -> list[str]:
    return sorted(members_col.distinct("titulo", {"estado": "activo", "titulo": {"$nin": [None, ""]}}))


def get_publico_titulos() -> list[str]:
    """Títulos distintos en la colección publico (incluye los que vienen de Zoom)."""
    raw = publico_col.distinct("titulo", {"titulo": {"$nin": [None, ""]}})
    return sorted(set(raw))


def get_member_ubicaciones() -> list[str]:
    return sorted(members_col.distinct("ubicacion", {"estado": "activo", "ubicacion": {"$nin": [None, ""]}}))


# ── WooCommerce ↔ MongoDB cross-reference ──────────────────────────────────────

def get_wc_payment(order_id: int) -> dict | None:
    """Busca el pago en MongoDB que corresponde a una orden WC (pagado_por = 'WC#<id>')."""
    tag = f"WC#{order_id}"
    cuota_numero = None
    pay = payments_col.find_one({"pagado_por": tag})
    if not pay:
        pay = payments_col.find_one({"cuotas.pagado_por": tag})
        if pay:
            for c in pay.get("cuotas", []):
                if c.get("pagado_por") == tag:
                    cuota_numero = c.get("numero")
                    break
    if not pay:
        return None
    member = members_col.find_one({"member_id": pay["member_id"]}, {"nombres": 1, "apellidos": 1})
    result = _clean(pay)
    result["nombre_completo"] = f"{member.get('apellidos','').strip()}, {member.get('nombres','').strip()}".strip(", ") if member else ""
    if cuota_numero is not None:
        result["cuota_numero"] = cuota_numero
    return result


def get_wc_payment_by_wp_user_id(wp_user_id: int, periodo: str = None) -> dict | None:
    """Busca el socio por wp_user_id y, si hay periodo, su pago correspondiente."""
    member = members_col.find_one({"wp_user_id": wp_user_id}, {"member_id": 1, "nombres": 1, "apellidos": 1})
    if not member:
        return None
    nombre = f"{member.get('apellidos','').strip()}, {member.get('nombres','').strip()}".strip(", ")
    if periodo:
        pay = payments_col.find_one({"member_id": member["member_id"], "periodo": periodo})
        if pay:
            result = _clean(pay)
            result["nombre_completo"] = nombre
            return result
    return {"member_id": member["member_id"], "nombre_completo": nombre, "solo_socio": True}


def get_member_by_email_exact(email: str) -> dict | None:
    """Busca un socio cuyo array emails[] contenga este email (case-insensitive)."""
    email = email.strip().lower()
    member = members_col.find_one(
        {"emails.email": {"$regex": f"^{email}$", "$options": "i"}},
        {"member_id": 1, "nombres": 1, "apellidos": 1, "estado": 1, "emails": 1},
    )
    return _clean(member) if member else None


def vincular_wp_usuario(wc_email: str, member_id: str, wp_user_id: int = None) -> dict:
    """
    Vincula un usuario de WordPress a un socio MongoDB:
    agrega el email WC al array emails[] y guarda el wp_user_id permanente.
    """
    wc_email = wc_email.strip().lower()
    member = members_col.find_one({"member_id": member_id}, {"emails": 1, "nombres": 1, "apellidos": 1})
    if not member:
        return {"ok": False, "error": f"Socio {member_id} no encontrado"}

    existing = [e.get("email", "").lower() for e in member.get("emails", [])]
    update: dict = {}
    if wc_email not in existing:
        update["$addToSet"] = {"emails": {"email": wc_email, "estado": "habilitado", "principal": False}}
    if wp_user_id:
        update.setdefault("$set", {})["wp_user_id"] = wp_user_id

    if update:
        members_col.update_one({"member_id": member_id}, update)

    action = "already_exists" if wc_email in existing and not wp_user_id else "linked"
    return {"ok": True, "action": action, "member_id": member_id}


def vincular_wc_order(order_id: int, member_id: str, wc_email: str, periodo: str, cuota_numero: int = None) -> dict:
    """
    Vincula una orden WC a un socio de MongoDB:
    1. Agrega el email de WC al socio (si no lo tiene ya).
    2. Si se indica periodo, marca el pago de ese período con pagado_por = 'WC#<order_id>'
       (y lo crea si no existe). Sin periodo solo vincula el email.
    Devuelve {"ok": True} o {"ok": False, "error": "..."}.
    """
    member = members_col.find_one({"member_id": member_id}, {"emails": 1})
    if not member:
        return {"ok": False, "error": f"Socio {member_id} no encontrado"}

    # 1. Agregar email WC si no existe
    wc_email_l = wc_email.strip().lower()
    existing = [e.get("email", "").lower() for e in member.get("emails", [])]
    if wc_email_l and wc_email_l not in existing:
        members_col.update_one(
            {"member_id": member_id},
            {"$addToSet": {"emails": {"email": wc_email_l, "estado": "habilitado", "principal": False}}},
        )

    # 2. Actualizar pago del período (opcional)
    if not periodo:
        return {"ok": True, "action": "linked_email_only"}

    tag = f"WC#{order_id}"
    pay = payments_col.find_one({"member_id": member_id, "periodo": periodo})
    if pay:
        if cuota_numero is not None:
            payments_col.update_one(
                {"_id": pay["_id"], "cuotas.numero": cuota_numero},
                {"$set": {"cuotas.$.pagado_por": tag}},
            )
            return {"ok": True, "payment_id": str(pay["_id"]), "action": "cuota_updated", "cuota_numero": cuota_numero}
        payments_col.update_one({"_id": pay["_id"]}, {"$set": {"pagado_por": tag}})
        return {"ok": True, "payment_id": str(pay["_id"]), "action": "updated"}
    else:
        # No existe registro de pago para ese período — lo creamos en estado "pagado"
        new_pay = {
            "member_id": member_id,
            "periodo": periodo,
            "estado": "pagado",
            "pagado_por": tag,
            "empresa_pagadora": None,
            "fecha_pago": None,
            "medio_pago": "WooCommerce",
            "num_comprobante": None,
            "tipo_comprobante": None,
            "link_constancia": None,
            "raw_original": "",
        }
        result = payments_col.insert_one(new_pay)
        return {"ok": True, "payment_id": str(result.inserted_id), "action": "created"}


def get_wc_payment_by_email(email: str, periodo: str | None) -> dict | None:
    """Busca socio por email. Si hay período, intenta traer también el pago; si no, devuelve solo el socio."""
    member = members_col.find_one(
        {"emails.email": {"$regex": f"^{email}$", "$options": "i"}},
        {"member_id": 1, "nombres": 1, "apellidos": 1},
    )
    if not member:
        return None
    nombre = f"{member.get('apellidos','').strip()}, {member.get('nombres','').strip()}".strip(", ")
    if periodo:
        pay = payments_col.find_one({"member_id": member["member_id"], "periodo": periodo})
        if pay:
            result = _clean(pay)
            result["nombre_completo"] = nombre
            return result
    return {"member_id": member["member_id"], "nombre_completo": nombre, "solo_socio": True}


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
    if estado:
        q["estado"] = estado
    docs = list(credito_col.find(q).sort("fecha_vencimiento", 1))
    if empresa:
        emp = empresa.strip()
        company = companies_col.find_one(
            {"$or": [
                {"nombre":      {"$regex": f"^{_re.escape(emp)}$", "$options": "i"}},
                {"razon_social": {"$regex": f"^{_re.escape(emp)}$", "$options": "i"}},
            ]},
            {"nombre": 1, "razon_social": 1},
        )
        variants = {emp.lower()}
        if company:
            if company.get("nombre"):
                variants.add(company["nombre"].lower())
            if company.get("razon_social") and company["razon_social"].strip():
                variants.add(company["razon_social"].lower().strip())
        def _matches(de: str) -> bool:
            de = (de or "").lower().strip()
            return bool(de) and any(de in v or v in de for v in variants)
        docs = [d for d in docs if _matches(d.get("empresa", ""))]
    for d in docs:
        d["estado"] = _sync_credito_estado(d)
    docs = _clean(docs)
    # Enriquecer socios: lista de IDs → lista de {member_id, nombre_completo}
    all_ids = {mid for d in docs for mid in (d.get("socios") or [])}
    if all_ids:
        members_map = {
            m["member_id"]: f"{m.get('apellidos','')} {m.get('nombres','')}".strip()
            for m in members_col.find(
                {"member_id": {"$in": list(all_ids)}},
                {"member_id": 1, "apellidos": 1, "nombres": 1},
            )
        }
        for d in docs:
            d["socios"] = [
                {"member_id": mid, "nombre": members_map.get(mid, mid)}
                for mid in (d.get("socios") or [])
            ]
    # Añadir campos calculados de cuotas
    for d in docs:
        cuotas = d.get("cuotas") or []
        d["cuotas_total"]   = len(cuotas)
        d["cuotas_pagadas"] = sum(1 for c in cuotas if c.get("estado") == "pagado")
        d["cuotas_completo"] = d["cuotas_total"] > 0 and d["cuotas_pagadas"] == d["cuotas_total"]
    return docs


def create_factura_credito(empresa: str, numero_factura: str, monto: float,
                            fecha_emision: str, fecha_vencimiento: str,
                            concepto: str = "", socios: list | None = None,
                            periodo: str = "",
                            comprobante_id: str = "") -> str:
    emp = empresa.strip()
    empresa_id = None
    if emp:
        existing = companies_col.find_one(
            {"$or": [
                {"nombre":      {"$regex": f"^{_re.escape(emp)}$", "$options": "i"}},
                {"razon_social": {"$regex": f"^{_re.escape(emp)}$", "$options": "i"}},
            ]},
            {"nombre": 1},
        )
        if existing:
            emp = existing["nombre"]
            empresa_id = str(existing["_id"])
        else:
            add_company(nombre=emp)
    # Pre-resolve payment_ids FK for each socio in this period
    socios_list = socios or []
    payment_ids: list[str] = []
    if socios_list and periodo:
        for mid in socios_list:
            p = payments_col.find_one({"member_id": mid, "periodo": periodo}, {"_id": 1})
            if p:
                payment_ids.append(str(p["_id"]))
    doc = {
        "empresa":           emp,
        "empresa_id":        empresa_id,
        "numero_factura":    numero_factura.strip(),
        "monto":             monto,
        "fecha_emision":     fecha_emision,
        "fecha_vencimiento": fecha_vencimiento,
        "concepto":          concepto.strip(),
        "socios":            socios_list,
        "periodo":           periodo,
        "estado":            "pendiente",
        "payment_ids":       payment_ids,
        "comprobante_id":    comprobante_id.strip() if comprobante_id else "",
        "created_at":        datetime.now(timezone.utc),
    }
    factura_id = str(credito_col.insert_one(doc).inserted_id)
    if socios_list and periodo:
        sync_credito_to_cobranzas(factura_id)
    return factura_id


def sync_credito_to_cobranzas(factura_id: str) -> None:
    """Sincroniza el estado de pagos en Cobranzas según el estado de cobro de la factura de crédito."""
    f = credito_col.find_one({"_id": ObjectId(factura_id)})
    if not f:
        return
    socios_raw = f.get("socios") or []
    periodo    = f.get("periodo") or ""
    if not socios_raw or not periodo:
        return

    # Normalizar: socios puede ser lista de strings o lista de {member_id, nombre}
    socios = [
        s if isinstance(s, str) else s.get("member_id", "")
        for s in socios_raw
    ]
    socios = [s for s in socios if s]

    cuotas = f.get("cuotas") or []
    estado = f.get("estado", "pendiente")
    empresa     = f.get("empresa") or None
    medio_pago  = f.get("medio_pago") or None
    link_const  = f.get("link_constancia") or None

    empresa_id = None
    if empresa:
        emp_doc = companies_col.find_one(
            {"nombre": {"$regex": f"^{_re.escape(empresa)}$", "$options": "i"}},
            {"_id": 1},
        )
        empresa_id = str(emp_doc["_id"]) if emp_doc else None

    if estado == "cobrado" or (cuotas and all(c.get("estado") == "pagado" for c in cuotas)):
        new_estado = "pagado"
        fecha = f.get("fecha_cobro") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    else:
        new_estado = "por_cobrar"
        fecha = None

    # Build fast-path map: member_id → payment ObjectId from FK list
    payment_ids_fk = f.get("payment_ids") or []
    fk_map: dict[str, ObjectId] = {}
    if payment_ids_fk:
        for pay in payments_col.find(
            {"_id": {"$in": [ObjectId(x) for x in payment_ids_fk if x]},
             "member_id": {"$in": socios}},
            {"_id": 1, "member_id": 1},
        ):
            fk_map[pay["member_id"]] = pay["_id"]

    for member_id in socios:
        if member_id in fk_map:
            p_id = fk_map[member_id]
            p = payments_col.find_one({"_id": p_id}, {"_id": 1})
        else:
            # Fallback: text-pair lookup for legacy docs without payment_ids
            p = payments_col.find_one({"member_id": member_id, "periodo": periodo})
        if p:
            upd: dict = {"estado": new_estado, "factura_credito_id": factura_id}
            if new_estado == "pagado":
                upd["fecha_pago"]       = fecha
                upd["empresa_pagadora"] = empresa
                upd["empresa_id"]       = empresa_id
                upd["medio_pago"]       = medio_pago
                upd["link_constancia"]  = link_const
            else:
                upd["fecha_pago"]       = None
                upd["empresa_pagadora"] = None
                upd["empresa_id"]       = None
                upd["medio_pago"]       = None
                upd["link_constancia"]  = None
            payments_col.update_one({"_id": p["_id"]}, {"$set": upd})


def update_factura_credito_estado(factura_id: str, estado: str,
                                   fecha_cobro: str = "",
                                   medio_pago: str = "",
                                   link_constancia: str = "") -> None:
    fields: dict = {"estado": estado}
    if estado == "cobrado":
        if fecha_cobro:
            fields["fecha_cobro"] = fecha_cobro
        fields["medio_pago"]      = medio_pago or None
        fields["link_constancia"] = link_constancia or None
    else:
        # Revertir: limpiar datos de cobro
        fields["fecha_cobro"]     = None
        fields["medio_pago"]      = None
        fields["link_constancia"] = None
    credito_col.update_one({"_id": ObjectId(factura_id)}, {"$set": fields})
    sync_credito_to_cobranzas(factura_id)


def delete_factura_credito(factura_id: str) -> None:
    revert = {"$set": {"estado": "debe", "fecha_pago": None,
                       "empresa_pagadora": None, "medio_pago": None,
                       "link_constancia": None},
              "$unset": {"factura_credito_id": ""}}
    # Pagos con enlace explícito (nuevo)
    payments_col.update_many({"factura_credito_id": factura_id}, revert)
    # Pagos sin enlace explícito, compat con registros anteriores
    f = credito_col.find_one({"_id": ObjectId(factura_id)})
    if f:
        socios_raw = f.get("socios") or []
        periodo    = f.get("periodo") or ""
        if socios_raw and periodo:
            socios = [s if isinstance(s, str) else s.get("member_id", "") for s in socios_raw]
            socios = [s for s in socios if s]
            payments_col.update_many(
                {"member_id": {"$in": socios}, "periodo": periodo,
                 "estado": {"$in": ["por_cobrar", "pagado"]},
                 "factura_credito_id": {"$exists": False}},
                revert,
            )
    credito_col.delete_one({"_id": ObjectId(factura_id)})


def add_comentario_credito(factura_id: str, texto: str) -> dict:
    fecha = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    comentario = {"texto": texto, "fecha": fecha}
    credito_col.update_one(
        {"_id": ObjectId(factura_id)},
        {"$push": {"comentarios": comentario}},
    )
    return comentario


def delete_comentario_credito(factura_id: str, idx: int) -> None:
    doc = credito_col.find_one({"_id": ObjectId(factura_id)}, {"comentarios": 1})
    if not doc:
        return
    comentarios = doc.get("comentarios", [])
    if 0 <= idx < len(comentarios):
        comentarios.pop(idx)
        credito_col.update_one(
            {"_id": ObjectId(factura_id)},
            {"$set": {"comentarios": comentarios}},
        )


def add_cuota_credito(factura_id: str, monto: float, fecha_venc: str) -> dict:
    doc = credito_col.find_one({"_id": ObjectId(factura_id)}, {"cuotas": 1})
    cuotas = (doc.get("cuotas") or []) if doc else []
    numero = max((c["numero"] for c in cuotas), default=0) + 1
    cuota = {
        "numero": numero,
        "monto": monto,
        "fecha_venc": fecha_venc or None,
        "fecha_pago": None,
        "estado": "pendiente",
    }
    credito_col.update_one({"_id": ObjectId(factura_id)}, {"$push": {"cuotas": cuota}})
    return cuota


def update_cuota_credito(factura_id: str, numero: int, estado: str, fecha_pago: str = "") -> None:
    update: dict = {"cuotas.$.estado": estado}
    if estado == "pagado" and fecha_pago:
        update["cuotas.$.fecha_pago"] = fecha_pago
    elif estado == "pendiente":
        update["cuotas.$.fecha_pago"] = None
    credito_col.update_one(
        {"_id": ObjectId(factura_id), "cuotas.numero": numero},
        {"$set": update},
    )
    sync_credito_to_cobranzas(factura_id)


def delete_cuota_credito(factura_id: str, numero: int) -> None:
    credito_col.update_one(
        {"_id": ObjectId(factura_id)},
        {"$pull": {"cuotas": {"numero": numero}}},
    )
    sync_credito_to_cobranzas(factura_id)


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
            {"titulo": {"$regex": _re.escape(search), "$options": "i"}},
            {"lugar":  {"$regex": _re.escape(search), "$options": "i"}},
        ]
    if estado:
        q["estado"] = estado
    total = eventos_col.count_documents(q)
    skip = (page - 1) * per_page
    docs = list(eventos_col.find(q).sort("fecha", 1).skip(skip).limit(per_page))
    for d in docs:
        d["_id"] = str(d["_id"])
        n_ins  = len(d.get("inscritos", []))
        n_zoom = len(d.get("zoom_registrants", []))
        total_ins = n_ins + n_zoom
        cupo = d.get("cupo_max")
        d["n_inscritos"]     = total_ins
        d["cupo_disponible"] = (cupo - total_ins) if cupo else None
        d["lleno"]           = bool(cupo and total_ins >= cupo)
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
    # Campos Zoom — default vacío si el evento no tiene webinar vinculado
    doc.setdefault("zoom_webinar_id", None)
    doc.setdefault("zoom_titulo", "")
    doc.setdefault("zoom_synced_at", None)
    doc.setdefault("zoom_registrants", [])
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
                  hora: str, lugar: str, cupo_max: int | None, estado: str,
                  modalidad: str = "", link_registro: str = "",
                  link_zoom: str = "", link_grabacion: str = "",
                  precio: str = "", fecha_limite_inscripcion: str = ""):
    eventos_col.update_one(
        {"_id": ObjectId(evento_id)},
        {"$set": {
            "titulo":         titulo,
            "descripcion":    descripcion or "",
            "fecha":          fecha,
            "hora":           hora or "",
            "lugar":          lugar or "",
            "cupo_max":       cupo_max,
            "estado":         estado,
            "modalidad":      modalidad or "",
            "link_registro":  link_registro.strip(),
            "link_zoom":      link_zoom.strip(),
            "link_grabacion": link_grabacion.strip(),
            "precio":         precio.strip(),
            "fecha_limite_inscripcion": fecha_limite_inscripcion.strip(),
        }},
    )


def update_evento_comunicaciones(evento_id: str, flyer_url: str = "",
                                  email_asunto: str = "", email_cuerpo: str = ""):
    eventos_col.update_one(
        {"_id": ObjectId(evento_id)},
        {"$set": {
            "flyer_url":    flyer_url.strip(),
            "email_asunto": email_asunto.strip(),
            "email_cuerpo": email_cuerpo,
        }},
    )


def update_evento_agente(evento_id: str, usar_en_agente: bool, agente_faqs: list, agente_info: str) -> None:
    """Lo que el agente de correos puede decir de este evento.

    `usar_en_agente`: mientras sea False el agente no conoce el evento (sirve para eventos a medio
    cargar); en True sus datos y preguntas cuentan como fuente autorizada, igual que una FAQ.
    """
    faqs = [{"pregunta": str(f.get("pregunta", "")).strip()[:500], "respuesta": str(f.get("respuesta", "")).strip()[:3000]}
            for f in agente_faqs or [] if str(f.get("pregunta", "")).strip() and str(f.get("respuesta", "")).strip()]
    eventos_col.update_one({"_id": ObjectId(evento_id)}, {"$set": {
        "usar_en_agente": bool(usar_en_agente),
        "agente_faqs": faqs[:40],
        "agente_info": (agente_info or "").strip()[:5000],
        "agente_actualizado_at": datetime.now(timezone.utc),
    }})


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


def socios_de_evento(evento_id: str) -> tuple[set[str], set[str]]:
    """(socios inscritos, socios que asistieron) a un evento, por la plataforma o por Zoom."""
    if not ObjectId.is_valid(evento_id or ""):
        return set(), set()
    ev = eventos_col.find_one({"_id": ObjectId(evento_id)},
                              {"inscritos.member_id": 1, "inscritos.asistio": 1,
                               "zoom_registrants.member_id": 1, "zoom_registrants.asistio": 1}) or {}
    filas = [*(ev.get("inscritos") or []), *(ev.get("zoom_registrants") or [])]
    inscritos = {f["member_id"] for f in filas if f.get("member_id")}
    asistieron = {f["member_id"] for f in filas if f.get("member_id") and f.get("asistio")}
    return inscritos, asistieron


def seguimiento_comercial_evento(evento_id: str) -> dict:
    """Números para el seguimiento después de un evento: socios y no socios que asistieron o no, y
    cuántos no socios asistentes ya tienen una postulación abierta."""
    if not ObjectId.is_valid(evento_id or ""):
        return {}
    ev = eventos_col.find_one({"_id": ObjectId(evento_id)}, {"zoom_registrants.email": 1, "zoom_registrants.asistio": 1,
                                                             "zoom_registrants.es_socio": 1}) or {}
    regs = ev.get("zoom_registrants") or []
    inscritos, asistieron = socios_de_evento(evento_id)
    no_socios = [r for r in regs if not r.get("es_socio") and r.get("email")]
    asist_ns = {r["email"].lower() for r in no_socios if r.get("asistio")}
    ids = [str(p["_id"]) for p in publico_col.find({"email": {"$in": list(asist_ns)}}, {"_id": 1})]
    con_postulacion = postulantes_col.count_documents({"prospecto_id": {"$in": ids},
                                                       "etapa": {"$in": ["consulta", "documentos", "evaluacion", "aprobado"]}}) if ids else 0
    return {
        "socios_asistieron": len(asistieron), "socios_no_asistieron": len(inscritos - asistieron),
        "no_socios_asistieron": len(asist_ns),
        "no_socios_no_asistieron": len({r["email"].lower() for r in no_socios if not r.get("asistio")} - asist_ns),
        "no_socios_postulando": con_postulacion,
    }


def eventos_de_socio(member_id: str) -> list[dict]:
    """Eventos a los que se inscribió el socio: por la plataforma (inscritos) o por Zoom (zoom_registrants,
    unido por email o DNI al sincronizar). Más reciente primero."""
    res = []
    for ev in eventos_col.find(
        {"$or": [{"inscritos.member_id": member_id}, {"zoom_registrants.member_id": member_id}]},
        {"titulo": 1, "fecha": 1, "modalidad": 1, "estado": 1,
         "inscritos.member_id": 1, "inscritos.asistio": 1,
         "zoom_registrants.member_id": 1, "zoom_registrants.asistio": 1, "zoom_registrants.duracion_min": 1},
    ):
        ins = next((i for i in ev.get("inscritos") or [] if i.get("member_id") == member_id), None)
        zoom = [z for z in ev.get("zoom_registrants") or [] if z.get("member_id") == member_id]
        asistio = any(z.get("asistio") for z in zoom) or bool(ins and ins.get("asistio"))
        sin_dato = not zoom and (not ins or ins.get("asistio") is None)
        res.append({
            "evento_id": str(ev["_id"]), "titulo": ev.get("titulo") or "", "fecha": ev.get("fecha") or "",
            "estado": ev.get("estado") or "", "via": "Zoom" if zoom else "Plataforma",
            "asistio": None if sin_dato else asistio,
            "duracion_min": sum(z.get("duracion_min") or 0 for z in zoom) or None,
        })
    res.sort(key=lambda e: e["fecha"], reverse=True)
    return res


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
    inscritos = evento.get("inscritos", [])
    member_ids = [i["member_id"] for i in inscritos]
    members_map = {}
    if member_ids:
        members_map = {m["member_id"]: m for m in
                       members_col.find({"member_id": {"$in": member_ids}},
                                        {"titulo": 1, "tipo_socio": 1, "centro_trabajo": 1, "member_id": 1})}
    by_titulo: dict = {}
    by_tipo:   dict = {}
    by_empresa: dict = {}
    for i in inscritos:
        m = members_map.get(i["member_id"], {})
        t = m.get("titulo") or "Sin datos"
        tp = m.get("tipo_socio") or "Sin datos"
        emp = m.get("centro_trabajo") or ""
        by_titulo[t]  = by_titulo.get(t, 0) + 1
        by_tipo[tp]   = by_tipo.get(tp, 0) + 1
        if emp:
            by_empresa[emp] = by_empresa.get(emp, 0) + 1
    n_ins = evento["n_inscritos"]
    n_asis = evento["n_asistentes"]
    return {
        "n_inscritos":    n_ins,
        "n_asistentes":   n_asis,
        "n_ausentes":     evento["n_ausentes"],
        "n_pendientes":   evento["n_pendientes"],
        "pct_asistencia": round(n_asis / n_ins * 100, 1) if n_ins else 0,
        "pct_ausencia":   round(evento["n_ausentes"] / n_ins * 100, 1) if n_ins else 0,
        "by_titulo":  sorted(by_titulo.items(),  key=lambda x: -x[1]),
        "by_tipo":    sorted(by_tipo.items(),    key=lambda x: -x[1]),
        "by_empresa": sorted(by_empresa.items(), key=lambda x: -x[1])[:10],
    }


def toggle_checklist_item(evento_id: str, item_id: str, done: bool):
    eventos_col.update_one(
        {"_id": ObjectId(evento_id)},
        {"$set": {f"checklist.{item_id}": done}},
    )


def add_expositor(evento_id: str, nombre: str, titulo: str = "",
                  empresa: str = "", linkedin: str = "", bio: str = "", ponente_id: str = "") -> int:
    doc = eventos_col.find_one({"_id": ObjectId(evento_id)}, {"expositores": 1})
    existentes = doc.get("expositores", []) if doc else []
    numero = (max((e["numero"] for e in existentes), default=0) + 1)
    entrada = {
        "numero":     numero,
        "nombre":     nombre.strip(),
        "titulo":     titulo.strip(),
        "empresa":    empresa.strip(),
        "linkedin":   linkedin.strip(),
        "bio":        bio.strip(),
        "confirmado": False,
        "ponente_id": ponente_id,
    }
    eventos_col.update_one({"_id": ObjectId(evento_id)}, {"$push": {"expositores": entrada}})
    return numero


def update_expositor(evento_id: str, numero: int, nombre: str, titulo: str = "",
                     empresa: str = "", linkedin: str = "", bio: str = "",
                     confirmado: bool = False):
    eventos_col.update_one(
        {"_id": ObjectId(evento_id), "expositores.numero": numero},
        {"$set": {
            "expositores.$.nombre":     nombre.strip(),
            "expositores.$.titulo":     titulo.strip(),
            "expositores.$.empresa":    empresa.strip(),
            "expositores.$.linkedin":   linkedin.strip(),
            "expositores.$.bio":        bio.strip(),
            "expositores.$.confirmado": confirmado,
        }},
    )


def delete_expositor(evento_id: str, numero: int):
    eventos_col.update_one(
        {"_id": ObjectId(evento_id)},
        {"$pull": {"expositores": {"numero": numero}}},
    )


def _parse_zoom_custom_fields(custom_questions: list) -> dict:
    """
    Mapea las preguntas personalizadas del formulario de Zoom a campos canónicos.
    Usa coincidencia por palabras clave en el título de la pregunta — el operador
    puede nombrar las preguntas libremente siempre que contengan estas palabras:
      título/profesion → titulo
      empresa/instituc/organiz/trabajo → empresa
      ciudad/departamento/ubicacion → ciudad
      celular/telefono/phone/movil → celular
      dni/documento/cedula/ruc → dni
    """
    _KEYWORDS = {
        "titulo":  ("título", "titulo", "profesion", "profesión", "especialidad"),
        "empresa": ("empresa", "instituc", "organiz", "trabajo", "empleador", "compañ"),
        "ciudad":  ("ciudad", "departamento", "ubicacion", "ubicación", "región", "region"),
        "celular": ("celular", "teléfono", "telefono", "phone", "móvil", "movil", "whatsapp"),
        "dni":     ("dni", "documento", "cédula", "cedula", "ruc", "identificacion"),
    }
    result: dict = {}
    for q in custom_questions:
        title = (q.get("title") or "").lower()
        value = (q.get("value") or "").strip()
        if not value:
            continue
        for campo, kws in _KEYWORDS.items():
            if campo not in result and any(kw in title for kw in kws):
                result[campo] = value
                break
    return result


def sync_zoom_data(
    evento_id: str,
    zoom_webinar_id: str,
    zoom_titulo: str,
    registrants: list[dict],
    participants: list[dict],
) -> dict:
    """
    Reemplaza completamente los datos de Zoom en el evento.
    registrants: lista de inscritos de Zoom (del endpoint /registrants).
    participants: lista de asistentes reales (del endpoint /report/participants).
    Los participantes de Zoom son público general, NO socios de IPIDET.
    Devuelve stats del resultado.
    """
    from datetime import datetime, timezone as _tz

    attended_emails = {
        p["user_email"].lower()
        for p in participants
        if p.get("user_email")
    }
    duration_by_email: dict[str, int] = {}
    for p in participants:
        em = (p.get("user_email") or "").lower()
        if em:
            duration_by_email[em] = duration_by_email.get(em, 0) + int(p.get("duration", 0)) // 60

    # Mapas de cruce con el padrón — email y DNI (una sola query)
    by_email: dict[str, str] = {}  # email_lower → member_id
    by_dni:   dict[str, str] = {}  # dni_limpio  → member_id
    for m in members_col.find({}, {"member_id": 1, "emails": 1, "dni": 1}):
        mid = m["member_id"]
        for e in m.get("emails", []):
            em = (e.get("email") or "").lower().strip()
            if em:
                by_email[em] = mid
        dni_raw = (m.get("dni") or "").strip()
        if dni_raw:
            by_dni[dni_raw] = mid

    zoom_list = []
    for r in registrants:
        email  = (r.get("email") or "").lower().strip()
        nombre = f"{r.get('first_name', '')} {r.get('last_name', '')}".strip() or email
        asistio = email in attended_emails if email else False

        # Leer campos extra del formulario de registro Zoom
        extra = _parse_zoom_custom_fields(r.get("custom_questions") or [])

        # Cruce: email primero, DNI como fallback
        member_id_val = by_email.get(email)
        match_via = "email" if member_id_val else None
        if not member_id_val and extra.get("dni"):
            member_id_val = by_dni.get(extra["dni"].strip())
            if member_id_val:
                match_via = "dni"

        entry: dict = {
            "email":         email,
            "nombre":        nombre,
            "registrado_at": r.get("create_time") or r.get("join_time", ""),
            "asistio":       asistio,
            "duracion_min":  duration_by_email.get(email) if asistio else None,
            "es_socio":      member_id_val is not None,
            "match_via":     match_via,
        }
        if member_id_val:
            entry["member_id"] = member_id_val
        # Campos extra del formulario Zoom (solo si vienen con valor)
        for k in ("titulo", "empresa", "ciudad", "celular", "dni"):
            if extra.get(k):
                entry[k] = extra[k]
        zoom_list.append(entry)

    # Ordenar: asistentes primero, luego por nombre
    zoom_list.sort(key=lambda x: (not x["asistio"], x["nombre"].lower()))

    now = datetime.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    eventos_col.update_one(
        {"_id": ObjectId(evento_id)},
        {"$set": {
            "zoom_webinar_id": zoom_webinar_id,
            "zoom_titulo":     zoom_titulo,
            "zoom_synced_at":  now,
            "zoom_registrants": zoom_list,
        }},
    )
    n_asistentes = sum(1 for z in zoom_list if z["asistio"])
    return {
        "total_registrados": len(zoom_list),
        "total_asistentes":  n_asistentes,
        "pct_asistencia":    round(n_asistentes / len(zoom_list) * 100, 1) if zoom_list else 0,
        "_zoom_list":        zoom_list,  # evita re-query al llamador
    }


# ── Pendientes y Atención ─────────────────────────────────────────────────────

pendientes_col = _db.pendientes


def get_pendientes(estado: str = "", prioridad: str = "", search: str = "") -> list:
    q: dict = {}
    if estado:
        q["estado"] = estado
    else:
        q["estado"] = {"$ne": "resuelto"}  # por defecto ocultar resueltos
    if prioridad:
        q["prioridad"] = prioridad
    if search:
        rx = {"$regex": _re.escape(search.strip()), "$options": "i"}
        q["$or"] = [{"titulo": rx}, {"descripcion": rx}, {"nombre_miembro": rx}]
    orden = {"alta": 0, "media": 1, "baja": 2}
    docs = list(pendientes_col.find(q).sort("created_at", -1))
    docs.sort(key=lambda d: (orden.get(d.get("prioridad", "media"), 1), d.get("created_at", datetime.min)))
    return _clean(docs)


def create_pendiente(titulo: str, descripcion: str = "", prioridad: str = "media",
                     member_id: str = "", nombre_miembro: str = "") -> str:
    doc = {
        "titulo": titulo.strip(),
        "descripcion": descripcion.strip(),
        "prioridad": prioridad,
        "estado": "pendiente",
        "member_id": member_id.strip() or None,
        "nombre_miembro": nombre_miembro.strip() or None,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "resuelto_at": None,
    }
    return str(pendientes_col.insert_one(doc).inserted_id)


def update_pendiente(pendiente_id: str, titulo: str, descripcion: str,
                     prioridad: str, estado: str,
                     member_id: str = "", nombre_miembro: str = "") -> None:
    fields: dict = {
        "titulo": titulo.strip(),
        "descripcion": descripcion.strip(),
        "prioridad": prioridad,
        "estado": estado,
        "member_id": member_id.strip() or None,
        "nombre_miembro": nombre_miembro.strip() or None,
        "updated_at": datetime.now(timezone.utc),
    }
    if estado == "resuelto":
        fields["resuelto_at"] = datetime.now(timezone.utc)
    else:
        fields["resuelto_at"] = None
    pendientes_col.update_one({"_id": ObjectId(pendiente_id)}, {"$set": fields})


def delete_pendiente(pendiente_id: str) -> None:
    pendientes_col.delete_one({"_id": ObjectId(pendiente_id)})


# ── Tareas IA ──────────────────────────────────────────────────────────────────

ia_col = _db.ia_tareas


def get_ia_tareas(solo_pendientes: bool = False) -> list:
    q: dict = {}
    if solo_pendientes:
        # Excluir completadas Y las que están en stand_by (no deben ejecutarse aún)
        q["estado"]   = {"$ne": "completado"}
        q["stand_by"] = {"$ne": True}
    docs = list(ia_col.find(q).sort([("stand_by", 1), ("created_at", 1)]))
    return _clean(docs)


def create_ia_tarea(texto: str) -> str:
    doc = {
        "texto":      texto.strip(),
        "estado":     "pendiente",
        "stand_by":   False,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    return str(ia_col.insert_one(doc).inserted_id)


def update_ia_tarea(tarea_id: str, texto: str | None = None,
                    estado: str | None = None, stand_by: bool | None = None) -> None:
    fields: dict = {"updated_at": datetime.now(timezone.utc)}
    if texto is not None:
        fields["texto"] = texto.strip()
    if estado is not None:
        fields["estado"] = estado
    if stand_by is not None:
        fields["stand_by"] = stand_by
    ia_col.update_one({"_id": ObjectId(tarea_id)}, {"$set": fields})


def toggle_ia_tarea_stand_by(tarea_id: str) -> None:
    doc = ia_col.find_one({"_id": ObjectId(tarea_id)})
    if doc:
        update_ia_tarea(tarea_id, stand_by=not bool(doc.get("stand_by")))


# ── Constancias de asociado ───────────────────────────────────────────────────

constancias_col = _db.constancias


def get_next_constancia_number(year: int) -> str:
    """Devuelve el siguiente número correlativo en formato NNN-YYYY."""
    suffix = f"-{year}"
    last = constancias_col.find_one(
        {"numero": {"$regex": f"{suffix}$"}},
        sort=[("numero", -1)],
    )
    if last:
        try:
            n = int(last["numero"].split("-")[0]) + 1
        except (ValueError, IndexError):
            n = 1
    else:
        n = 1
    return f"{n:03d}-{year}"


def save_constancia(
    member_id: str,
    fecha_emision: str,
    email_destino: str,
    tratamiento: str,
) -> str:
    doc = {
        "member_id":     member_id,
        "fecha_emision": fecha_emision,
        "email_destino": email_destino,
        "tratamiento":   tratamiento,
        "created_at":    datetime.now(timezone.utc),
    }
    return str(constancias_col.insert_one(doc).inserted_id)


def delete_ia_tarea(tarea_id: str) -> None:
    ia_col.delete_one({"_id": ObjectId(tarea_id)})


# ── Productos facturables ──────────────────────────────────────────────────────

TIPOS_PRODUCTO = ["cuota_anual", "cuota_provincia", "evento", "fraccionamiento", "otro"]


def _codigo_sunat_para_producto(tipo: str, periodo: str) -> str | None:
    """Deriva el código SUNAT canónico para un producto dado su tipo y período."""
    if tipo == "cuota_anual" and periodo:
        return f"CUOTA-{periodo}"
    if tipo == "cuota_provincia" and periodo:
        return f"CUOTA-F-{periodo}"
    if tipo == "fraccionamiento":
        return "FRACC-CUOTA"
    return None


def _sync_productos_codigo_sunat():
    """Backfill: asigna codigo_sunat a los productos que no lo tengan.
    Solo escribe el campo si está vacío o ausente — no pisa ediciones manuales.
    Genera el código dinámicamente desde tipo + periodo, sin años hardcodeados."""
    for prod in productos_col.find(
        {"$or": [{"codigo_sunat": {"$exists": False}}, {"codigo_sunat": ""}]}
    ):
        codigo = _codigo_sunat_para_producto(prod.get("tipo", ""), prod.get("periodo", ""))
        if codigo:
            productos_col.update_one({"_id": prod["_id"]}, {"$set": {"codigo_sunat": codigo}})


_sync_productos_codigo_sunat()


def get_productos(tipo: str = "", solo_activos: bool = False) -> list:
    q: dict = {}
    if tipo:
        q["tipo"] = tipo
    if solo_activos:
        q["activo"] = True
    docs = list(productos_col.find(q).sort([("activo", -1), ("tipo", 1), ("nombre", 1)]))
    return _clean(docs)


def create_producto(nombre: str, tipo: str, precio: float | None,
                    periodo: str, descripcion: str,
                    codigo_wc: str = "", codigo_sunat: str = "",
                    cuota_numero: int | None = None,
                    wc_product_id: int | None = None) -> str:
    doc = {
        "nombre":        nombre.strip(),
        "tipo":          tipo,
        "precio":        precio,
        "periodo":       periodo.strip(),
        "descripcion":   descripcion.strip(),
        "wc_product_id": wc_product_id,
        "codigo_wc":     codigo_wc.strip(),
        "codigo_sunat":  codigo_sunat.strip(),
        "cuota_numero":  cuota_numero,
        "activo":        True,
        "created_at":    datetime.now(timezone.utc),
    }
    producto_id = str(productos_col.insert_one(doc).inserted_id)
    catalogos.invalidar()
    return producto_id


def update_producto(producto_id: str, nombre: str, tipo: str, precio: float | None,
                    periodo: str, descripcion: str, activo: bool,
                    wc_product_id: int | None = None,
                    codigo_wc: str = "", codigo_sunat: str = "",
                    cuota_numero: int | None = None) -> None:
    productos_col.update_one(
        {"_id": ObjectId(producto_id)},
        {"$set": {
            "nombre":        nombre.strip(),
            "tipo":          tipo,
            "precio":        precio,
            "periodo":       periodo.strip(),
            "descripcion":   descripcion.strip(),
            "activo":        activo,
            "wc_product_id": wc_product_id,
            "codigo_wc":     codigo_wc.strip(),
            "codigo_sunat":  codigo_sunat.strip(),
            "cuota_numero":  cuota_numero,
        }},
    )
    catalogos.invalidar()


def delete_producto(producto_id: str) -> None:
    productos_col.delete_one({"_id": ObjectId(producto_id)})
    catalogos.invalidar()


# ── Comprobantes (boletas / facturas) ─────────────────────────────────────────

def get_comprobante_stats() -> dict:
    hoy = _date.today()
    este_mes = hoy.strftime("%Y-%m")
    q = {"estado": {"$ne": "anulado"}}
    docs = list(comprobantes_col.find(q, {"tipo": 1, "fecha_emision": 1, "estado": 1, "monto_total": 1,
                                          "efecto_pago": 1}))
    cs = get_credito_stats()
    emitidos = [d for d in docs if d.get("estado") != "pre_registrado"]
    notas = [d for d in emitidos if d.get("tipo") == "nota_credito"]
    return {
        "total":                len(emitidos) - len(notas),
        "boletas":              sum(1 for d in emitidos if d.get("tipo") == "boleta"),
        "facturas":             sum(1 for d in emitidos if d.get("tipo") == "factura"),
        "notas_credito":        len(notas),
        "notas_credito_monto":  round(sum(_sf(d.get("monto_total")) for d in notas), 2),
        "notas_sin_efecto":     sum(1 for d in notas if not d.get("efecto_pago")),
        "este_mes":             sum(1 for d in emitidos if (d.get("fecha_emision") or "").startswith(este_mes)),
        "pre_registrados":      sum(1 for d in docs if d.get("estado") == "pre_registrado"),
        "credito_pendiente":    cs["total_pendiente"],
        "credito_vencido":      cs["total_vencido"],
        "credito_cobrado":      cs["total_cobrado"],
        "credito_n_pendientes": cs["n_pendientes"],
        "credito_n_vencidos":   cs["n_vencidos"],
    }


def get_comprobantes_unified(
    search: str = "", tipo: str = "", empresa: str = "",
    fecha: str = "", sunat: str = "",
    sort_by: str = "fecha_emision", sort_dir: str = "desc",
    page: int = 1, per_page: int = 50,
) -> tuple:
    """Lista unificada de comprobantes (boletas/facturas) + facturas_credito."""
    include_comp = tipo in ("", "boleta", "factura", "recibo", "nota_credito")
    # credito docs always render as "Factura" in the tipo-doc column
    include_cred = tipo in ("", "credito", "factura")
    member_cache: dict = {}

    def _enrich(doc):
        info = []
        for mid in doc.get("socios") or []:
            if isinstance(mid, dict):
                mid = mid.get("member_id", "")
            if mid not in member_cache:
                m = members_col.find_one({"member_id": mid}, {"nombres": 1, "apellidos": 1})
                member_cache[mid] = (f"{m.get('apellidos','').strip()} {m.get('nombres','').strip()}".strip() if m else mid)
            info.append({"member_id": mid, "nombre": member_cache[mid]})
        doc["socios_info"] = info

    all_docs: list = []
    # Track self-contained crédito comprobantes for deduplication against facturas_credito.
    # credito_numeros: text fallback (numero string match)
    # credito_comp_ids: FK match (_id of comprobante stored as comprobante_id on factura_credito)
    credito_numeros: set = set()
    credito_comp_ids: set = set()

    # Filtro SUNAT implica excluir crédito (no aplica)
    if sunat:
        include_cred = False

    if include_comp:
        q: dict = {"estado": {"$ne": "anulado"}}
        if tipo and tipo not in ("credito",):
            q["tipo"] = tipo
        if empresa:
            q["empresa"] = {"$regex": _re.escape(empresa), "$options": "i"}
        if fecha:
            q["fecha_emision"] = fecha
        if sunat == "emitido":
            q["sunat_estado"] = "emitido"
        elif sunat == "pendiente":
            q["tipo"] = {"$in": ["boleta", "factura"]}
            q["sunat_estado"] = {"$not": {"$in": ["emitido", "anulado_sunat"]}}
        elif sunat == "error":
            q["sunat_estado"] = "error"
        if search:
            _srx = _re.escape(search)
            or_c: list = [
                {"numero":          {"$regex": _srx, "$options": "i"}},
                {"empresa":         {"$regex": _srx, "$options": "i"}},
                {"producto_nombre": {"$regex": _srx, "$options": "i"}},
                {"concepto":        {"$regex": _srx, "$options": "i"}},
            ]
            mtch = list(members_col.find(
                {"$or": [{"nombres": {"$regex": _srx, "$options": "i"}},
                         {"apellidos": {"$regex": _srx, "$options": "i"}}]},
                {"member_id": 1},
            ))
            if mtch:
                or_c.append({"socios": {"$in": [m["member_id"] for m in mtch]}})
            q["$or"] = or_c
        for d in comprobantes_col.find(q):
            d["_id"] = str(d["_id"])
            d["_tipo_doc"] = "comprobante"
            d["monto_total"] = d.get("monto_total") or 0
            lineas = d.get("items") or []
            if lineas:
                pnames = list({it.get("producto_nombre", "") for it in lineas if it.get("producto_nombre")})
                d["display_producto"] = pnames[0] if len(pnames) == 1 else "Varios"
            else:
                d["display_producto"] = d.get("producto_nombre") or "—"
            d["lineas"] = lineas
            d["socios_map"] = {}
            _enrich(d)
            # If this comprobante has a fecha_vencimiento it is self-contained crédito
            if d.get("fecha_vencimiento"):
                credito_numeros.add(d["numero"])
                credito_comp_ids.add(d["_id"])  # FK dedup
                # Derive estado_credito dynamically if not stored
                if not d.get("estado_credito"):
                    d["estado_credito"] = _sync_credito_estado({
                        "estado": d.get("estado_credito", ""),
                        "fecha_vencimiento": d["fecha_vencimiento"],
                    })
            all_docs.append(d)

    if include_cred:
        qc: dict = {}
        if empresa:
            qc["empresa"] = {"$regex": _re.escape(empresa), "$options": "i"}
        if fecha:
            qc["fecha_emision"] = fecha
        if search:
            _srx = _re.escape(search)
            qc["$or"] = [
                {"numero_factura": {"$regex": _srx, "$options": "i"}},
                {"empresa":        {"$regex": _srx, "$options": "i"}},
                {"concepto":       {"$regex": _srx, "$options": "i"}},
            ]
        for d in credito_col.find(qc):
            numero_fac = d.get("numero_factura") or ""
            fc_comp_id = d.get("comprobante_id") or ""
            # Skip facturas_credito already represented by a self-contained crédito comprobante
            # Prefer FK match (comprobante_id), fall back to numero text match
            if (fc_comp_id and fc_comp_id in credito_comp_ids) or (numero_fac and numero_fac in credito_numeros):
                continue
            d["_id"] = str(d["_id"])
            d["_tipo_doc"] = "credito"
            d["tipo"] = "credito"
            d["numero"] = numero_fac
            d["monto_total"] = _sf(d.get("monto"))
            d["estado_credito"] = _sync_credito_estado(d)
            d["display_producto"] = d.get("concepto") or "—"
            d["lineas"] = []
            d["fecha_carga"] = d.get("fecha_vencimiento") or ""
            _enrich(d)
            d["socios"] = [{"member_id": si["member_id"], "nombre": si["nombre"]} for si in d["socios_info"]]
            d["socios_map"] = {si["member_id"]: si["nombre"] for si in d["socios_info"]}
            all_docs.append(d)

    rev = sort_dir == "desc"
    if sort_by == "monto":
        all_docs.sort(key=lambda d: d.get("monto_total") or 0, reverse=rev)
    elif sort_by == "empresa":
        all_docs.sort(key=lambda d: (d.get("empresa") or "").lower(), reverse=rev)
    elif sort_by == "numero":
        all_docs.sort(key=lambda d: d.get("numero") or "", reverse=rev)
    else:
        all_docs.sort(key=lambda d: d.get("fecha_emision") or "", reverse=rev)

    total = len(all_docs)
    start = (page - 1) * per_page
    return all_docs[start:start + per_page], total


def get_comprobantes(search: str = "", tipo: str = "", empresa: str = "",
                     fecha: str = "",
                     sort_by: str = "fecha_carga", sort_dir: str = "desc",
                     page: int = 1, per_page: int = 50) -> tuple:
    q: dict = {"estado": {"$ne": "anulado"}}
    if tipo:
        q["tipo"] = tipo
    if empresa:
        q["empresa"] = {"$regex": _re.escape(empresa), "$options": "i"}
    if fecha:
        q["fecha_emision"] = fecha
    if search:
        _srx = _re.escape(search)
        or_conds: list = [
            {"numero":          {"$regex": _srx, "$options": "i"}},
            {"empresa":         {"$regex": _srx, "$options": "i"}},
            {"producto_nombre": {"$regex": _srx, "$options": "i"}},
            {"concepto":        {"$regex": _srx, "$options": "i"}},
        ]
        matching = list(members_col.find(
            {"$or": [
                {"nombres":   {"$regex": _srx, "$options": "i"}},
                {"apellidos": {"$regex": _srx, "$options": "i"}},
            ]}, {"member_id": 1}
        ))
        if matching:
            or_conds.append({"socios": {"$in": [m["member_id"] for m in matching]}})
        q["$or"] = or_conds
    _sort_map = {
        "numero":       "numero",
        "tipo":         "tipo",
        "fecha_emision":"fecha_emision",
        "fecha_carga":  "fecha_carga",
        "empresa":      "empresa",
        "monto":        "monto_total",
    }
    _sort_field = _sort_map.get(sort_by, "fecha_carga")
    _sort_mongo  = -1 if sort_dir == "desc" else 1
    total = comprobantes_col.count_documents(q)
    docs = list(
        comprobantes_col.find(q)
        .sort(_sort_field, _sort_mongo)
        .skip((page - 1) * per_page)
        .limit(per_page)
    )
    member_cache: dict = {}
    for doc in docs:
        doc["_id"] = str(doc["_id"])
        socios_info = []
        for mid in doc.get("socios", []):
            if mid not in member_cache:
                m = members_col.find_one({"member_id": mid}, {"nombres": 1, "apellidos": 1})
                member_cache[mid] = f"{m.get('nombres','') if m else ''} {m.get('apellidos','') if m else ''}".strip()
            socios_info.append({"member_id": mid, "nombre": member_cache[mid] or mid})
        doc["socios_info"] = socios_info
    return docs, total


def create_comprobante(numero: str, tipo: str, fecha_emision: str, monto_total: float,
                        producto_nombre: str, concepto: str, empresa: str,
                        socios: list, items: list = None, ruc: str = "",
                        estado: str = "emitido",
                        destinatario_id: str = "",
                        destinatario_nombre: str = "",
                        destinatario_dni: str = "",
                        medio_pago: str = "",
                        xml_raw: str = "",
                        fecha_vencimiento: str = "",
                        estado_credito: str = "",
                        fecha_pago: str = "") -> str:
    # Derive socios from items if provided
    if items:
        seen = set()
        socios = [it["member_id"] for it in items
                  if it.get("member_id") and it["member_id"] not in seen and not seen.add(it["member_id"])]
    emp = empresa.strip()
    ruc_emp = ruc.strip()
    empresa_id = None
    if emp:
        comp = companies_col.find_one(
            {"$or": [
                {"nombre":      {"$regex": f"^{_re.escape(emp)}$", "$options": "i"}},
                {"razon_social": {"$regex": f"^{_re.escape(emp)}$", "$options": "i"}},
            ]},
            {"ruc": 1},
        )
        if comp:
            ruc_emp = ruc_emp or comp.get("ruc", "")
            empresa_id = str(comp["_id"])
    doc = {
        "numero":               numero.strip(),
        "tipo":                 tipo,
        "fecha_emision":        fecha_emision,
        "fecha_carga":          datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "monto_total":          monto_total,
        "producto_nombre":      producto_nombre.strip(),
        "concepto":             concepto.strip(),
        "empresa":              emp,
        "empresa_id":           empresa_id,
        "ruc_empresa":          ruc_emp,
        "socios":               socios,
        "items":                items or [],
        "estado":               estado,
        "medio_pago":           medio_pago.strip() if medio_pago else "",
        "fecha_pago":           fecha_pago.strip() if fecha_pago else "",
        "xml_raw":              xml_raw or "",
        "fecha_vencimiento":    fecha_vencimiento.strip() if fecha_vencimiento else "",
        "estado_credito":       estado_credito or ("pendiente" if fecha_vencimiento else ""),
        "destinatario_id":      destinatario_id.strip() if destinatario_id else "",
        "destinatario_nombre":  destinatario_nombre.strip() if destinatario_nombre else "",
        "destinatario_dni":     destinatario_dni.strip() if destinatario_dni else "",
        "created_at":           datetime.now(timezone.utc),
    }
    result = comprobantes_col.insert_one(doc)
    return str(result.inserted_id)


def _nombre_clave(texto: str) -> frozenset:
    import unicodedata
    t = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().upper()
    return frozenset(p for p in _re.split(r"[^A-Z0-9]+", t) if len(p) > 1)


def buscar_borrador_para_xml(data: dict) -> dict | None:
    """Borrador (pre_registrado) del mismo tipo que corresponde a un XML importado.

    Factura: por RUC del cliente (en el borrador o en su empresa). Boleta: por DNI del borrador, por
    el socio que tiene ese DNI en el padrón, por los socios del XML o, al final, por el nombre del
    cliente. Si hay varios, gana el de igual monto y luego el más reciente."""
    tipo, doc, monto = data.get("tipo"), (data.get("ruc_empresa") or "").strip(), data.get("monto_total") or 0
    base = {"estado": "pre_registrado", "tipo": tipo}
    condiciones: list[dict] = []
    if len(doc) == 11 and doc.isdigit():
        condiciones.append({"ruc_empresa": doc})
        ids = [str(c["_id"]) for c in companies_col.find({"ruc": doc}, {"_id": 1})]
        if ids:
            condiciones.append({"empresa_id": {"$in": ids}})
    elif len(doc) == 8 and doc.isdigit():
        condiciones.append({"destinatario_dni": doc})
        socios = [m["member_id"] for m in members_col.find({"dni": doc}, {"member_id": 1})]
        socios += [it["member_id"] for it in data.get("items") or [] if it.get("member_id")]
        if socios:
            condiciones += [{"destinatario_id": {"$in": socios}}, {"socios": {"$in": socios}}]
    candidatos = list(comprobantes_col.find({**base, "$or": condiciones}).sort("created_at", -1)) if condiciones else []
    if not candidatos and len(doc) == 8 and data.get("razon_social"):
        clave = _nombre_clave(data["razon_social"])
        candidatos = [c for c in comprobantes_col.find(base).sort("created_at", -1)
                      if clave and _nombre_clave(c.get("destinatario_nombre") or "") == clave]
    if not candidatos:
        return None
    mismo_monto = [c for c in candidatos if abs(float(c.get("monto_total") or 0) - float(monto)) < 0.01]
    return (mismo_monto or candidatos)[0]


def update_comprobante(comprobante_id: str, fields: dict) -> None:
    allowed = {"numero", "tipo", "fecha_emision", "monto_total",
               "producto_nombre", "concepto", "empresa", "empresa_id", "ruc_empresa",
               "socios", "items", "estado", "medio_pago", "fecha_pago", "xml_raw", "fecha_vencimiento",
               "estado_credito", "fecha_cobro",
               "sunat_estado", "sunat_xml", "sunat_hash", "sunat_cdr_code",
               "sunat_cdr_desc", "sunat_accepted", "sunat_error", "sunat_emitido_at"}
    update = {k: v for k, v in fields.items() if k in allowed}
    if "empresa" in update:
        emp = (update["empresa"] or "").strip()
        if emp:
            comp = companies_col.find_one(
                {"$or": [
                    {"nombre":      {"$regex": f"^{_re.escape(emp)}$", "$options": "i"}},
                    {"razon_social": {"$regex": f"^{_re.escape(emp)}$", "$options": "i"}},
                ]},
                {"_id": 1, "ruc": 1},
            )
            if comp:
                if not update.get("ruc_empresa"):
                    update["ruc_empresa"] = comp.get("ruc", "")
                update["empresa_id"] = str(comp["_id"])
            else:
                update["empresa_id"] = None
        else:
            update["empresa_id"] = None
    if update:
        comprobantes_col.update_one({"_id": ObjectId(comprobante_id)}, {"$set": update})


_SIN_COMPROBANTE = {
    "num_comprobante": None, "comprobante_id": None, "tipo_comprobante": None,
    "fecha_emision_comprobante": None, "comprobante_emitido": False,
}


def _desvincular_pagos(comprobante_id: str, items: list, modo: str) -> list[dict]:
    """Quita el comprobante de los pagos de esas líneas (solo por FK, R8). Devuelve lo que tocó.

    modo «anular»     → lo que hacía siempre anular un comprobante: el pago vuelve a «debe».
    modo «devolucion» → igual, y además se borra la fecha y el medio de pago (no hubo ingreso).
    modo «reemplazo»  → el pago conserva su estado: solo queda sin comprobante.
    Un pago parcial solo se desvincula: si se devolvió dinero, el parcial se corrige en Cobranzas.
    """
    devolver = modo in ("anular", "devolucion")
    tocados: list[dict] = []
    vistos: set = set()
    for item in items:
        pid, tipo = item.get("payment_id"), item.get("tipo_pago")
        if not pid:
            continue
        try:
            oid = ObjectId(pid)
        except Exception:
            continue
        clave = (pid, tipo, item.get("cuota_numero"))
        if clave in vistos:
            continue
        vistos.add(clave)
        r = None
        if tipo == "principal":
            cambios = dict(_SIN_COMPROBANTE)
            if devolver:
                cambios["estado"] = "debe"
            if modo == "devolucion":
                cambios.update({"fecha_pago": None, "medio_pago": None, "banco_origen": None, "pagado_por": None})
            r = payments_col.update_one({"_id": oid, "comprobante_id": comprobante_id}, {"$set": cambios})
        elif tipo == "cuota" and item.get("cuota_numero") is not None:
            cambios = {"cuotas.$.num_comprobante": None, "cuotas.$.comprobante_id": None,
                       "cuotas.$.fecha_emision_comprobante": None}
            if devolver:
                cambios.update({"cuotas.$.estado": "pendiente", "cuotas.$.fecha_pago": None})
            if modo == "devolucion":
                cambios["cuotas.$.medio_pago"] = None
            r = payments_col.update_one(
                {"_id": oid, "cuotas": {"$elemMatch": {"numero": item["cuota_numero"],
                                                       "comprobante_id": comprobante_id}}},
                {"$set": cambios},
            )
            _sync_estado_from_cuotas(pid)
        elif tipo == "parcial":
            r = payments_col.update_one(
                {"_id": oid, "pagos_parciales.comprobante_id": comprobante_id},
                {"$set": {"pagos_parciales.$.num_comprobante": None, "pagos_parciales.$.comprobante_id": None}},
            )
            _sync_estado_from_parciales(pid)
        if r is not None and r.modified_count:
            tocados.append({"payment_id": pid, "tipo_pago": tipo, "cuota_numero": item.get("cuota_numero"),
                            "member_id": item.get("member_id"), "accion": modo})
    return tocados


def delete_comprobante(comprobante_id: str) -> None:
    comp = comprobantes_col.find_one({"_id": ObjectId(comprobante_id)}, {"items": 1, "tipo": 1})
    if comp and comp.get("tipo") == "nota_credito":
        anular_nota_credito(comprobante_id)
        return
    comprobantes_col.update_one({"_id": ObjectId(comprobante_id)}, {"$set": {"estado": "anulado"}})
    if not comp:
        return
    _desvincular_pagos(comprobante_id, comp.get("items") or [], "anular")
    # Pagos que apuntan al comprobante sin estar en sus líneas
    payments_col.update_many(
        {"comprobante_id": comprobante_id},
        {"$set": {**_SIN_COMPROBANTE, "estado": "debe"}},
    )


# ── Notas de crédito ──────────────────────────────────────────────────────────
# Una nota de crédito es un comprobante con tipo «nota_credito» y FK `doc_ref_id` al comprobante
# que modifica. Sus líneas son solo descriptivas (sin payment_id): lo que cambia en los pagos lo
# decide el equipo con `efecto_pago` y se aplica sobre las líneas del original (`lineas_ref`).

def _saldo_comprobante(original: dict) -> float:
    """Lo que aún se puede acreditar: monto del comprobante menos sus notas de crédito vigentes."""
    acreditado = sum(_sf(n.get("monto")) for n in (original.get("notas_credito") or []))
    return round(_sf(original.get("monto_total")) - acreditado, 2)


def _recalcular_notas_de(original_id: str) -> None:
    """Rehace en el original la lista de notas vigentes, lo acreditado y si quedó anulado."""
    notas = list(comprobantes_col.find(
        {"tipo": "nota_credito", "doc_ref_id": original_id, "estado": {"$ne": "anulado"}},
        {"numero": 1, "monto_total": 1, "fecha_emision": 1, "motivo_codigo": 1},
    ).sort("fecha_emision", 1))
    original = comprobantes_col.find_one({"_id": ObjectId(original_id)}, {"monto_total": 1})
    if not original:
        return
    acreditado = round(sum(_sf(n.get("monto_total")) for n in notas), 2)
    comprobantes_col.update_one({"_id": ObjectId(original_id)}, {"$set": {
        "notas_credito": [{"id": str(n["_id"]), "numero": n.get("numero", ""), "monto": _sf(n.get("monto_total")),
                           "fecha": n.get("fecha_emision", ""), "motivo_codigo": n.get("motivo_codigo", "")}
                          for n in notas],
        "monto_acreditado": acreditado,
        "anulado_por_nc": bool(notas) and acreditado >= _sf(original.get("monto_total")) - 0.009,
    }})


def info_para_nota_credito(original_id: str) -> dict | None:
    """Lo que necesita el formulario: datos del original, sus líneas con el estado de cada pago y el saldo."""
    try:
        original = comprobantes_col.find_one({"_id": ObjectId(original_id)})
    except Exception:
        return None
    if not original:
        return None
    lineas = []
    for idx, it in enumerate(original.get("items") or []):
        pago = None
        if it.get("payment_id"):
            try:
                pago = payments_col.find_one({"_id": ObjectId(it["payment_id"])},
                                             {"estado": 1, "periodo": 1, "comprobante_id": 1})
            except Exception:
                pago = None
        m = members_col.find_one({"member_id": it.get("member_id")}, {"nombres": 1, "apellidos": 1}) \
            if it.get("member_id") else None
        lineas.append({
            "idx": idx,
            "member_id": it.get("member_id") or "",
            "nombre": f"{(m or {}).get('apellidos', '')} {(m or {}).get('nombres', '')}".strip(),
            "producto_nombre": it.get("producto_nombre") or "",
            "monto": _sf(it.get("monto")),
            "tipo_pago": it.get("tipo_pago") or "",
            "cuota_numero": it.get("cuota_numero"),
            "periodo": (pago or {}).get("periodo", ""),
            "estado_pago": (pago or {}).get("estado", ""),
            "vinculado": bool(pago),
        })
    return {
        "id": str(original["_id"]),
        "numero": original.get("numero", ""),
        "tipo": original.get("tipo", ""),
        "estado": original.get("estado", ""),
        "fecha_emision": original.get("fecha_emision", ""),
        "monto_total": _sf(original.get("monto_total")),
        "saldo": _saldo_comprobante(original),
        "empresa": original.get("empresa", ""),
        "destinatario": original.get("destinatario_nombre", ""),
        "sunat_emitido": original.get("sunat_estado") == "emitido",
        "notas_credito": original.get("notas_credito") or [],
        "lineas": lineas,
    }


def _lineas_elegidas(original: dict, lineas_idx: list | None) -> list:
    items = original.get("items") or []
    if lineas_idx is None:
        return list(items)
    elegidas = []
    for i in lineas_idx:
        try:
            elegidas.append(items[int(i)])
        except (ValueError, IndexError, TypeError):
            raise ValueError("Una de las líneas elegidas no existe en el comprobante")
    return elegidas


def _aplicar_efecto(nc_id: str, original: dict, efecto: str, lineas: list,
                    lineas_idx: list | None) -> list[dict]:
    if efecto == "ninguno":
        return []
    tocados = _desvincular_pagos(str(original["_id"]), lineas, efecto)
    # Las líneas afectadas quedan marcadas con la nota: ninguna sincronización (al arrancar el
    # servidor, «vincular pagos» o al editar el comprobante) vuelve a marcar esos pagos como pagados.
    items = list(original.get("items") or [])
    for i in (range(len(items)) if lineas_idx is None else lineas_idx):
        if 0 <= int(i) < len(items):
            items[int(i)] = {**items[int(i)], "nc_id": nc_id}
    if items:
        comprobantes_col.update_one({"_id": original["_id"]}, {"$set": {"items": items}})
    # Comprobante sin líneas vinculadas (datos antiguos): pagos que lo apuntan directamente.
    if not any(it.get("payment_id") for it in lineas) and not (original.get("items") or []):
        for p in payments_col.find({"comprobante_id": str(original["_id"])}, {"member_id": 1}):
            tocados += _desvincular_pagos(str(original["_id"]), [{
                "payment_id": str(p["_id"]), "tipo_pago": "principal", "member_id": p.get("member_id"),
            }], efecto)
    return tocados


def registrar_nota_credito(original_id: str, numero: str, fecha_emision: str, motivo_codigo: str,
                           motivo: str, monto: float, lineas_idx: list | None, efecto: str,
                           origen: str = "manual", items_xml: list | None = None,
                           xml_raw: str = "", sunat: dict | None = None,
                           validar_saldo: bool = True) -> tuple[str, list[dict]]:
    """Registra una nota de crédito sobre un comprobante y aplica su efecto en los pagos.

    `lineas_idx` = posiciones de las líneas del original que afecta (None = todas).
    `efecto` = clave de catalogos.EFECTOS_NOTA_CREDITO, o "" si queda por definir (importación XML).
    Devuelve (id de la nota, pagos que cambiaron).
    """
    from config import catalogos
    numero = (numero or "").strip().upper()
    if not numero:
        raise ValueError("Falta el número de la nota de crédito")
    if motivo_codigo not in catalogos.MOTIVOS_NOTA_CREDITO:
        raise ValueError("Elige el motivo de la nota de crédito")
    if efecto and efecto not in catalogos.EFECTOS_NOTA_CREDITO:
        raise ValueError("Efecto en la cobranza no válido")
    monto = round(_sf(monto), 2)
    if monto <= 0:
        raise ValueError("El monto debe ser mayor a cero")
    original = comprobantes_col.find_one({"_id": ObjectId(original_id)})
    if not original or original.get("tipo") not in ("boleta", "factura"):
        raise ValueError("El comprobante que modifica no existe o no es boleta ni factura")
    if original.get("estado") != "emitido":
        raise ValueError("Solo se puede hacer una nota de crédito de un comprobante emitido")
    if comprobantes_col.find_one({"tipo": "nota_credito", "numero": numero, "estado": {"$ne": "anulado"}}):
        raise ValueError(f"Ya existe la nota de crédito {numero}")
    saldo = _saldo_comprobante(original)
    if validar_saldo and monto > saldo + 0.009:
        raise ValueError(f"El monto supera lo que queda por acreditar del comprobante (S/ {saldo:.2f})")
    lineas = _lineas_elegidas(original, lineas_idx)
    if efecto in ("devolucion", "reemplazo") and lineas_idx is not None and not lineas:
        raise ValueError("Elige qué líneas del comprobante afecta la nota")

    if items_xml:
        items = [{k: it.get(k) for k in ("member_id", "producto_nombre", "monto", "codigo_sunat")}
                 for it in items_xml]
    else:
        items = [{"member_id": it.get("member_id"), "producto_nombre": it.get("producto_nombre"),
                  "monto": _sf(it.get("monto")), "codigo_sunat": it.get("codigo_sunat")} for it in lineas]
        # Descuento parcial con una sola línea: la línea lleva el monto de la nota.
        if len(items) == 1 or not items:
            items = [{**(items[0] if items else {}), "monto": monto}]
    socios = list(dict.fromkeys(it["member_id"] for it in items if it.get("member_id"))) or original.get("socios") or []
    ahora = datetime.now(timezone.utc)
    doc = {
        "numero":              numero,
        "tipo":                "nota_credito",
        "fecha_emision":       fecha_emision or _date.today().isoformat(),
        "fecha_carga":         ahora.strftime("%Y-%m-%d"),
        "monto_total":         monto,
        "producto_nombre":     catalogos.MOTIVOS_NOTA_CREDITO[motivo_codigo],
        "concepto":            (motivo or catalogos.MOTIVOS_NOTA_CREDITO[motivo_codigo]).strip(),
        "motivo_codigo":       motivo_codigo,
        "motivo":              (motivo or catalogos.MOTIVOS_NOTA_CREDITO[motivo_codigo]).strip(),
        "doc_ref_id":          str(original["_id"]),
        "doc_ref_numero":      original.get("numero", ""),
        "doc_ref_tipo":        original.get("tipo", ""),
        "lineas_ref":          [int(i) for i in lineas_idx] if lineas_idx is not None else None,
        "empresa":             original.get("empresa", ""),
        "empresa_id":          original.get("empresa_id"),
        "ruc_empresa":         original.get("ruc_empresa", ""),
        "destinatario_id":     original.get("destinatario_id", ""),
        "destinatario_nombre": original.get("destinatario_nombre", ""),
        "destinatario_dni":    original.get("destinatario_dni", ""),
        "socios":              socios,
        "items":               items,
        "estado":              "emitido",
        "origen":              origen,
        "efecto_pago":         efecto,
        "pagos_afectados":     [],
        "xml_raw":             xml_raw or "",
        "created_at":          ahora,
    }
    if sunat:
        doc.update(sunat)
    nc_id = str(comprobantes_col.insert_one(doc).inserted_id)
    tocados = _aplicar_efecto(nc_id, original, efecto, lineas, lineas_idx) if efecto else []
    if efecto:
        comprobantes_col.update_one({"_id": ObjectId(nc_id)},
                                    {"$set": {"pagos_afectados": tocados, "efecto_aplicado_at": ahora}})
    _recalcular_notas_de(str(original["_id"]))
    return nc_id, tocados


def aplicar_efecto_nota_credito(nc_id: str, efecto: str, lineas_idx: list | None) -> list[dict]:
    """Define el efecto de una nota que quedó pendiente (importada por XML). Se aplica una sola vez."""
    from config import catalogos
    if efecto not in catalogos.EFECTOS_NOTA_CREDITO:
        raise ValueError("Elige qué pasa con la cobranza")
    nc = comprobantes_col.find_one({"_id": ObjectId(nc_id), "tipo": "nota_credito"})
    if not nc:
        raise ValueError("Nota de crédito no encontrada")
    if nc.get("efecto_pago"):
        raise ValueError("El efecto de esta nota ya se aplicó")
    if not nc.get("doc_ref_id"):
        raise ValueError(f"El comprobante {nc.get('doc_ref_numero') or ''} no está en la plataforma. "
                         "Impórtalo primero; la nota se vincula sola.")
    original = comprobantes_col.find_one({"_id": ObjectId(nc["doc_ref_id"])})
    if not original:
        raise ValueError("El comprobante que modifica ya no existe")
    lineas = _lineas_elegidas(original, lineas_idx)
    if efecto in ("devolucion", "reemplazo") and lineas_idx is not None and not lineas:
        raise ValueError("Elige qué líneas del comprobante afecta la nota")
    tocados = _aplicar_efecto(nc_id, original, efecto, lineas, lineas_idx)
    comprobantes_col.update_one({"_id": ObjectId(nc_id)}, {"$set": {
        "efecto_pago": efecto, "pagos_afectados": tocados, "efecto_aplicado_at": datetime.now(timezone.utc),
        "lineas_ref": [int(i) for i in lineas_idx] if lineas_idx is not None else None,
    }})
    return tocados


def anular_nota_credito(nc_id: str) -> None:
    """Quita el registro de la nota. No restaura los pagos: eso se corrige en Cobranzas."""
    nc = comprobantes_col.find_one({"_id": ObjectId(nc_id), "tipo": "nota_credito"}, {"doc_ref_id": 1})
    if not nc:
        return
    comprobantes_col.update_one({"_id": nc["_id"]}, {"$set": {"estado": "anulado"}})
    if nc.get("doc_ref_id"):
        _recalcular_notas_de(nc["doc_ref_id"])


def vincular_notas_pendientes(comprobante_id: str, numero: str) -> int:
    """Al cargar un comprobante, se le unen las notas de crédito importadas antes que él."""
    r = comprobantes_col.update_many(
        {"tipo": "nota_credito", "doc_ref_numero": numero, "doc_ref_id": None, "estado": {"$ne": "anulado"}},
        {"$set": {"doc_ref_id": comprobante_id}},
    )
    if r.modified_count:
        _recalcular_notas_de(comprobante_id)
    return r.modified_count


def sync_comp_credito_to_payments(comprobante_id: str, nuevo_estado: str, fecha_cobro: str = "") -> None:
    """Sincroniza el estado de cobranza de un comprobante crédito hacia los payments vinculados."""
    comp = comprobantes_col.find_one({"_id": ObjectId(comprobante_id)})
    if not comp:
        return
    empresa = comp.get("empresa") or None
    for item in (comp.get("items") or []):
        pid = item.get("payment_id")
        if not pid or item.get("tipo_pago") != "principal":
            continue
        try:
            update_payment(pid, estado=nuevo_estado,
                           fecha_pago=fecha_cobro or None,
                           empresa=empresa,
                           comprobante_id=comprobante_id)
        except Exception:
            pass


def get_comprobante_by_id(comprobante_id: str) -> dict | None:
    doc = comprobantes_col.find_one({"_id": ObjectId(comprobante_id)})
    if doc:
        doc["_id"] = str(doc["_id"])
    return doc


def get_ultimo_correlativo_info(serie: str, notas_credito: bool = False) -> dict | None:
    """Returns {numero, ambiente} for the highest emitted correlativo in the serie, or None.
    Counts ALL emitted docs regardless of ambiente to avoid number reuse.
    Las notas de crédito llevan su propia numeración: `notas_credito=True` cuenta solo esas."""
    import re as _re
    pattern = f"^{_re.escape(serie)}-\\d+$"
    docs = list(comprobantes_col.find(
        {"sunat_estado": "emitido", "numero": {"$regex": pattern},
         "tipo": "nota_credito" if notas_credito else {"$ne": "nota_credito"}},
        {"numero": 1, "sunat_ambiente": 1},
    ))
    best_num = -1
    best: dict | None = None
    for doc in docs:
        try:
            n = int(doc["numero"].split("-", 1)[1])
            if n > best_num:
                best_num = n
                best = {
                    "numero":   n,
                    "ambiente": doc.get("sunat_ambiente") or "desconocido",
                }
        except (ValueError, IndexError):
            pass
    return best


def get_ultimo_correlativo_emitido(serie: str) -> int | None:
    """Returns the highest correlativo (as int) emitted to SUNAT for a given serie.
    serie = 'B001', 'F001', etc.  Returns None if no emitted docs for that serie."""
    info = get_ultimo_correlativo_info(serie)
    return info["numero"] if info else None


def get_ultimo_correlativo_emitido_nc(serie: str) -> int | None:
    """Último correlativo de nota de crédito emitido por la plataforma en esa serie (BC01, FC01…)."""
    info = get_ultimo_correlativo_info(serie, notas_credito=True)
    return info["numero"] if info else None


def update_sunat_estado(
    comprobante_id: str,
    estado: str,
    xml: str = "",
    hash_: str = "",
    cdr_code: str = "",
    cdr_desc: str = "",
    accepted: bool = False,
    error: str = "",
    ambiente: str = "beta",
) -> None:
    """Persiste el resultado de la emisión SUNAT en el comprobante."""
    update: dict = {
        "sunat_estado":     estado,
        "sunat_xml":        xml,
        "sunat_hash":       hash_,
        "sunat_cdr_code":   cdr_code,
        "sunat_cdr_desc":   cdr_desc,
        "sunat_accepted":   accepted,
        "sunat_error":      error,
        "sunat_emitido_at": datetime.now(timezone.utc) if estado == "emitido" else None,
        "sunat_ambiente":   ambiente,
    }
    comprobantes_col.update_one({"_id": ObjectId(comprobante_id)}, {"$set": update})


def get_comprobante_email_info(comp_id: str) -> dict | None:
    """Devuelve la info necesaria para el modal de envío de email."""
    import re as _re2
    try:
        comp = comprobantes_col.find_one({"_id": ObjectId(comp_id)})
    except Exception:
        return None
    if not comp:
        return None

    socios_info = []
    for mid in (comp.get("socios") or []):
        m = members_col.find_one(
            {"member_id": mid},
            {"nombres": 1, "apellidos": 1, "celular": 1, "dni": 1, "emails": 1},
        )
        if not m:
            continue
        email_principal = next(
            (e["email"] for e in m.get("emails", [])
             if e.get("principal") and e.get("estado") == "habilitado"),
            next((e["email"] for e in m.get("emails", [])
                  if e.get("estado") == "habilitado"), None),
        )
        socios_info.append({
            "member_id": mid,
            "nombre": f"{m.get('apellidos', '')} {m.get('nombres', '')}".strip(),
            "email": email_principal or "",
            "celular": m.get("celular") or "",
            "dni": m.get("dni") or "",
        })

    empresa = comp.get("empresa") or ""
    empresa_email = ""
    empresa_contacto = ""
    if empresa:
        emp = companies_col.find_one(
            {"$or": [
                {"nombre":      {"$regex": f"^{_re2.escape(empresa)}$", "$options": "i"}},
                {"razon_social": {"$regex": f"^{_re2.escape(empresa)}$", "$options": "i"}},
            ]},
            {"contacto_email": 1, "contacto_nombre": 1},
        )
        if emp:
            empresa_email    = emp.get("contacto_email") or ""
            empresa_contacto = emp.get("contacto_nombre") or ""

    if empresa and empresa_email:
        para_sugerido = empresa_email
        para_label = f"{empresa_contacto} ({empresa})" if empresa_contacto else empresa
    elif socios_info and socios_info[0]["email"]:
        para_sugerido = socios_info[0]["email"]
        para_label = socios_info[0]["nombre"]
    else:
        para_sugerido = ""
        para_label = ""

    return {
        "_id":              str(comp["_id"]),
        "numero":           comp.get("numero") or "",
        "tipo":             comp.get("tipo") or "boleta",
        "fecha_emision":    comp.get("fecha_emision") or "",
        "empresa":          empresa,
        "empresa_email":    empresa_email,
        "empresa_contacto": empresa_contacto,
        "has_xml":          bool(comp.get("xml_raw") or comp.get("sunat_xml")),
        "monto_total":      comp.get("monto_total") or 0,
        "items":            comp.get("items") or [],
        "concepto":         comp.get("concepto") or comp.get("producto_nombre") or "",
        "socios_info":      socios_info,
        "para_sugerido":    para_sugerido,
        "para_label":       para_label,
    }


def get_factura_credito_email_info(factura_id: str) -> dict | None:
    """Devuelve la info necesaria para el modal de envío de email de una factura crédito."""
    try:
        fac = credito_col.find_one({"_id": ObjectId(factura_id)})
    except Exception:
        return None
    if not fac:
        return None

    socios_info = []
    for mid in (fac.get("socios") or []):
        m = members_col.find_one(
            {"member_id": mid},
            {"nombres": 1, "apellidos": 1, "celular": 1, "dni": 1, "emails": 1},
        )
        if not m:
            continue
        email_principal = next(
            (e["email"] for e in m.get("emails", [])
             if e.get("principal") and e.get("estado") == "habilitado"),
            next((e["email"] for e in m.get("emails", [])
                  if e.get("estado") == "habilitado"), None),
        )
        socios_info.append({
            "member_id": mid,
            "nombre": f"{m.get('apellidos', '')} {m.get('nombres', '')}".strip(),
            "email": email_principal or "",
            "celular": m.get("celular") or "",
            "dni": m.get("dni") or "",
        })

    empresa = fac.get("empresa") or ""
    empresa_email = ""
    empresa_contacto = ""
    if empresa:
        emp = companies_col.find_one(
            {"$or": [
                {"nombre":      {"$regex": f"^{_re.escape(empresa)}$", "$options": "i"}},
                {"razon_social": {"$regex": f"^{_re.escape(empresa)}$", "$options": "i"}},
            ]},
            {"contacto_email": 1, "contacto_nombre": 1},
        )
        if emp:
            empresa_email    = emp.get("contacto_email") or ""
            empresa_contacto = emp.get("contacto_nombre") or ""

    if empresa and empresa_email:
        para_sugerido = empresa_email
        para_label = f"{empresa_contacto} ({empresa})" if empresa_contacto else empresa
    elif socios_info and socios_info[0]["email"]:
        para_sugerido = socios_info[0]["email"]
        para_label = socios_info[0]["nombre"]
    else:
        para_sugerido = ""
        para_label = ""

    return {
        "_id":              str(fac["_id"]),
        "numero":           fac.get("numero_factura") or "",
        "tipo":             "factura",
        "fecha_emision":    fac.get("fecha_emision") or "",
        "empresa":          empresa,
        "empresa_email":    empresa_email,
        "empresa_contacto": empresa_contacto,
        "has_xml":          False,
        "monto_total":      _sf(fac.get("monto")),
        "items":            [{"producto_nombre": fac.get("concepto") or f"Membresía {fac.get('periodo', '')}".strip(),
                              "monto": _sf(fac.get("monto"))}],
        "concepto":         fac.get("concepto") or "",
        "socios_info":      socios_info,
        "para_sugerido":    para_sugerido,
        "para_label":       para_label,
    }


def backfill_comprobantes_payment_ids() -> dict:
    """
    Startup migration: for each non-anulado comprobante whose items have member_id
    but no payment_id (missing OR null), resolves payment_id via producto_id or
    producto_nombre → productos → periodo → payments, persists the resolved items,
    and syncs payments. Idempotent — only touches items still missing payment_id.
    """
    fixed_comps = 0
    fixed_items = 0
    # $in:[None,""] matches both null values and absent fields (MongoDB treats missing as null)
    cursor = comprobantes_col.find(
        {"estado": {"$ne": "anulado"},
         "items": {"$elemMatch": {"member_id": {"$exists": True, "$nin": [None, ""]},
                                  "payment_id": {"$in": [None, ""]}}}}
    )
    for comp in cursor:
        items = list(comp.get("items") or [])
        changed = False
        for item in items:
            if item.get("payment_id") or not item.get("member_id"):
                continue
            prod = None
            prod_id = item.get("producto_id")
            if prod_id:
                try:
                    prod = productos_col.find_one({"_id": ObjectId(prod_id)})
                except Exception:
                    pass
            if not prod and item.get("producto_nombre"):
                prod = productos_col.find_one(
                    {"nombre": {"$regex": f"^{_re.escape(item['producto_nombre'].strip())}$",
                                "$options": "i"}}
                )
            if not prod:
                continue
            if prod.get("tipo") in ("cuota_anual", "cuota_provincia") and prod.get("periodo"):
                pmt = payments_col.find_one(
                    {"member_id": item["member_id"], "periodo": prod["periodo"]}
                )
                if pmt:
                    item["payment_id"]  = str(pmt["_id"])
                    item["tipo_pago"]   = "principal"
                    item["producto_id"] = str(prod["_id"])
                    changed = True
                    fixed_items += 1
        if changed:
            comp_id = str(comp["_id"])
            comprobantes_col.update_one(
                {"_id": comp["_id"]}, {"$set": {"items": items}}
            )
            sync_comprobante_to_payments(
                items=items,
                numero=comp.get("numero", ""),
                tipo=comp.get("tipo", "boleta"),
                fecha_emision=comp.get("fecha_emision", ""),
                empresa=comp.get("empresa", ""),
                comprobante_id=comp_id,
                fecha_pago=comp.get("fecha_pago", ""),
            )
            fixed_comps += 1
    return {"fixed_comprobantes": fixed_comps, "fixed_items": fixed_items}


def backfill_resync_comprobantes_with_payment_id() -> dict:
    """
    Startup migration: re-syncs all non-anulado comprobantes whose items already have
    payment_id set (string). Applies current sync logic to existing data: empresa
    propagation/clearing, medio_pago propagation, fraccionamiento→pagado when full
    annual boleta is linked. Safe to run on every startup — idempotent.
    """
    synced = 0
    cursor = comprobantes_col.find(
        {"estado": {"$ne": "anulado"},
         "items": {"$elemMatch": {"payment_id": {"$exists": True, "$nin": [None, ""]}}}}
    )
    for comp in cursor:
        items = list(comp.get("items") or [])
        if not items:
            continue
        comp_id    = str(comp["_id"])
        medio_pago = comp.get("medio_pago") or ""
        sync_comprobante_to_payments(
            items          = items,
            numero         = comp.get("numero", ""),
            tipo           = comp.get("tipo", "boleta"),
            fecha_emision  = comp.get("fecha_emision", ""),
            empresa        = comp.get("empresa", ""),
            comprobante_id = comp_id,
            medio_pago     = medio_pago,
            fecha_pago     = comp.get("fecha_pago", ""),
        )
        synced += 1
    return {"resynced_comprobantes": synced}


def backfill_payments_fecha_pago() -> int:
    """
    Startup migration: for payments that are pagado/exonerado/no_aplica, have a
    comprobante_id linked, but no fecha_pago — fill fecha_pago from the comprobante's
    fecha_emision. Idempotent — skips payments that already have fecha_pago.
    """
    filled = 0
    cursor = payments_col.find({
        "estado":        {"$in": list(_ESTADOS_NO_MODIFICAR)},
        "comprobante_id": {"$exists": True, "$nin": [None, ""]},
        "fecha_pago":    {"$in": [None, ""]},
    })
    for pay in cursor:
        comp_id = pay.get("comprobante_id")
        if not comp_id:
            continue
        try:
            comp = comprobantes_col.find_one({"_id": ObjectId(comp_id)}, {"fecha_emision": 1})
        except Exception:
            continue
        if not comp or not comp.get("fecha_emision"):
            continue
        payments_col.update_one(
            {"_id": pay["_id"]},
            {"$set": {"fecha_pago": comp["fecha_emision"]}},
        )
        filled += 1
    return filled


def get_pre_comprobantes(tipo: str = None) -> list:
    """Comprobantes pre-registrados (borradores sin número de SUNAT)."""
    q: dict = {"estado": "pre_registrado"}
    if tipo:
        q["tipo"] = tipo
    docs = list(comprobantes_col.find(q).sort("created_at", -1))
    for doc in docs:
        doc["_id"] = str(doc["_id"])
    return docs


def complete_pre_comprobante(comp_id: str, numero: str, tipo: str,
                              fecha_emision: str, monto_total: float = None,
                              ruc: str = None) -> dict:
    """Completa un pre-comprobante con los datos reales del XML/SUNAT."""
    update: dict = {
        "numero":       numero,
        "tipo":         tipo,
        "fecha_emision": fecha_emision,
        "estado":       "emitido",
    }
    if monto_total is not None:
        update["monto_total"] = monto_total
    if ruc is not None:
        update["ruc_empresa"] = ruc
    comprobantes_col.update_one({"_id": ObjectId(comp_id)}, {"$set": update})
    doc = comprobantes_col.find_one({"_id": ObjectId(comp_id)})
    if doc:
        doc["_id"] = str(doc["_id"])
    return doc or {}


def get_comprobantes_por_empresa(empresa: str) -> list:
    """Comprobantes emitidos a una empresa (búsqueda bidireccional por nombre y RUC)."""
    emp = empresa.strip()
    if not emp:
        return []
    company = companies_col.find_one(
        {"$or": [
            {"nombre":      {"$regex": f"^{_re.escape(emp)}$", "$options": "i"}},
            {"razon_social": {"$regex": f"^{_re.escape(emp)}$", "$options": "i"}},
        ]},
        {"nombre": 1, "razon_social": 1, "ruc": 1},
    )
    variants = {emp.lower()}
    ruc = ""
    if company:
        for field in ("nombre", "razon_social"):
            if company.get(field) and company[field].strip():
                variants.add(company[field].lower().strip())
        ruc = company.get("ruc", "") or ""

    q: dict = {"estado": {"$ne": "anulado"}}
    # Build a scoped query to avoid full-collection scan
    name_patterns = [{"empresa": {"$regex": v, "$options": "i"}} for v in variants]
    if ruc:
        name_patterns.append({"ruc_empresa": ruc})
    q["$or"] = name_patterns
    docs = list(comprobantes_col.find(q).sort("fecha_emision", -1))

    def _matches(doc_emp: str, doc_ruc: str) -> bool:
        if ruc and doc_ruc and ruc == doc_ruc:
            return True
        de = (doc_emp or "").lower().strip()
        return bool(de) and any(de in v or v in de for v in variants)

    docs = [d for d in docs if _matches(d.get("empresa", ""), d.get("ruc_empresa", ""))]

    # Enrich socios
    all_ids = {mid for d in docs for mid in (d.get("socios") or [])}
    members_map: dict = {}
    if all_ids:
        members_map = {
            m["member_id"]: f"{m.get('apellidos', '')} {m.get('nombres', '')}".strip()
            for m in members_col.find(
                {"member_id": {"$in": list(all_ids)}},
                {"member_id": 1, "apellidos": 1, "nombres": 1},
            )
        }
    result = []
    for d in _clean(docs):
        d["socios_info"] = [
            {"member_id": mid, "nombre": members_map.get(mid, mid)}
            for mid in (d.get("socios") or [])
        ]
        result.append(d)
    return result


def get_productos_list() -> list:
    return list(productos_col.find({"activo": True}, {"nombre": 1, "tipo": 1, "precio": 1, "periodo": 1, "codigo_sunat": 1}).sort("nombre", 1))


# ── Ingresos (flujo de caja) ───────────────────────────────────────────────────

def get_ingresos(fecha_desde: str = "", fecha_hasta: str = "",
                 periodo: str = "", empresa: str = "", medio: str = "",
                 tipo_fecha: str = "pago",
                 page: int = 1, per_page: int = 100) -> tuple:
    """
    Devuelve (page_docs, total, stats).
    Muestra registros de cobranza con fecha_pago registrada, ordenados por fecha.
    tipo_fecha='pago'    → todos los que tienen fecha_pago
    tipo_fecha='emision' → solo los que además tienen num_comprobante emitido
    """
    result = []
    member_cache: dict = {}

    def _nombre(mid: str) -> str:
        if mid not in member_cache:
            m = members_col.find_one({"member_id": mid}, {"nombres": 1, "apellidos": 1})
            member_cache[mid] = (
                f"{m.get('nombres','') if m else ''} {m.get('apellidos','') if m else ''}".strip() or "?"
            )
        return member_cache[mid]

    def _ok_fecha(fecha: str) -> bool:
        if not fecha:
            return False
        if fecha_desde and fecha < fecha_desde:
            return False
        if fecha_hasta and fecha > fecha_hasta:
            return False
        return True

    pmt_q: dict = {}
    if periodo:
        pmt_q["periodo"] = periodo
    if empresa:
        pmt_q.update(_build_empresa_filter(empresa))
    for pmt in payments_col.find(pmt_q):
        mid      = pmt.get("member_id", "")
        per      = pmt.get("periodo", "")
        emp      = pmt.get("empresa_pagadora") or ""
        pid      = str(pmt["_id"])
        cuotas   = pmt.get("cuotas", [])
        parciales= pmt.get("pagos_parciales", [])
        medio_p  = pmt.get("medio_pago", "") or ""

        # 1. Pago completo (sin cuotas ni parciales)
        if not cuotas and not parciales:
            fecha   = pmt.get("fecha_pago") or ""
            num_cmp = pmt.get("num_comprobante", "") or ""
            if tipo_fecha == "emision" and not num_cmp:
                pass  # modo emisión: solo los que tienen comprobante emitido
            elif _ok_fecha(fecha) and (not medio or medio_p == medio):
                result.append({
                    "_id": pid,
                    "tipo_entrada": "pago",
                    "fecha": fecha,
                    "member_id": mid,
                    "nombre": _nombre(mid),
                    "periodo": per,
                    "concepto": "Pago completo",
                    "monto": pmt.get("monto"),
                    "empresa": emp,
                    "medio_pago": medio_p,
                    "num_comprobante": num_cmp,
                    "tipo_comprobante": pmt.get("tipo_comprobante", ""),
                    "link_constancia": pmt.get("link_constancia", ""),
                })

        # 2. Cuotas de fraccionamiento pagadas
        for c in cuotas:
            if c.get("estado") != "pagado":
                continue
            fecha   = c.get("fecha_pago") or ""
            medio_c = c.get("medio_pago") or medio_p
            num_cmp = c.get("num_comprobante", "") or ""
            if tipo_fecha == "emision" and not num_cmp:
                continue
            if _ok_fecha(fecha) and (not medio or medio_c == medio):
                result.append({
                    "_id": f"{pid}_c{c.get('numero', 0)}",
                    "tipo_entrada": "cuota",
                    "fecha": fecha,
                    "member_id": mid,
                    "nombre": _nombre(mid),
                    "periodo": per,
                    "concepto": f"Fraccionamiento — cuota {c.get('numero', '')}",
                    "monto": c.get("monto"),
                    "empresa": emp,
                    "medio_pago": medio_c,
                    "num_comprobante": num_cmp,
                    "tipo_comprobante": c.get("tipo_comprobante", ""),
                    "link_constancia": c.get("link_constancia", ""),
                })

        # 3. Pagos parciales
        for pp in parciales:
            fecha    = pp.get("fecha_pago") or ""
            medio_pp = pp.get("medio_pago") or medio_p
            num_cmp  = pp.get("num_comprobante", "") or ""
            if tipo_fecha == "emision" and not num_cmp:
                continue
            if _ok_fecha(fecha) and (not medio or medio_pp == medio):
                result.append({
                    "_id": f"{pid}_p{pp.get('numero', 0)}",
                    "tipo_entrada": "parcial",
                    "fecha": fecha,
                    "member_id": mid,
                    "nombre": _nombre(mid),
                    "periodo": per,
                    "concepto": f"Pago parcial {pp.get('numero', '')}",
                    "monto": pp.get("monto"),
                    "empresa": emp,
                    "medio_pago": medio_pp,
                    "num_comprobante": num_cmp,
                    "tipo_comprobante": pp.get("tipo_comprobante", ""),
                    "link_constancia": pp.get("link_constancia", ""),
                })

    result.sort(key=lambda x: x.get("fecha", ""), reverse=True)
    total = len(result)
    con_monto  = [r for r in result if r.get("monto")]
    sin_monto  = total - len(con_monto)
    stats = {
        "monto_total":  sum(r["monto"] for r in con_monto),
        "con_monto":    len(con_monto),
        "sin_monto":    sin_monto,
    }
    start = (page - 1) * per_page
    return result[start:start + per_page], total, stats


# ── Factura crédito: edición de campos ────────────────────────────────────────

def update_factura_credito_fields(factura_id: str, fields: dict) -> None:
    allowed = {"empresa", "numero_factura", "monto", "fecha_emision",
               "fecha_vencimiento", "concepto", "periodo", "socios",
               "payment_ids", "comprobante_id"}
    update: dict = {}
    for k, v in fields.items():
        if k not in allowed or v is None:
            continue
        if k == "numero_factura":
            update["numero"] = str(v).strip()   # el campo en BD se llama "numero"
        elif k == "monto":
            try:
                update["monto"] = float(v)
            except (ValueError, TypeError):
                pass
        elif k == "socios":
            if isinstance(v, list):
                update["socios"] = [str(s) for s in v if s]
        elif k == "empresa":
            emp = str(v).strip()
            empresa_id = None
            if emp:
                existing = companies_col.find_one(
                    {"$or": [
                        {"nombre":       {"$regex": f"^{_re.escape(emp)}$", "$options": "i"}},
                        {"razon_social": {"$regex": f"^{_re.escape(emp)}$", "$options": "i"}},
                    ]},
                    {"nombre": 1},
                )
                if existing:
                    emp = existing["nombre"]
                    empresa_id = str(existing["_id"])
                else:
                    add_company(nombre=emp)
            update["empresa"] = emp
            update["empresa_id"] = empresa_id
        else:
            update[k] = str(v).strip()
    if update:
        credito_col.update_one({"_id": ObjectId(factura_id)}, {"$set": update})
        sync_credito_to_cobranzas(factura_id)


def get_pendientes_stats() -> dict:
    docs = list(pendientes_col.find({}))
    return {
        "total":     sum(1 for d in docs if d.get("estado") != "resuelto"),
        "alta":      sum(1 for d in docs if d.get("prioridad") == "alta" and d.get("estado") != "resuelto"),
        "en_proceso": sum(1 for d in docs if d.get("estado") == "en_proceso"),
        "resueltos": sum(1 for d in docs if d.get("estado") == "resuelto"),
    }


# ── Envíos programados ─────────────────────────────────────────────────────────

programados_col = _db.comunicaciones_programadas


def create_envio_programado(asunto: str, cuerpo: str, filtros: dict,
                             destinatarios: list, fecha_envio: str,
                             disclaimer: bool = True,
                             imagenes_inline: list | None = None) -> str:
    """
    fecha_envio: ISO 8601 string en UTC, ej. "2026-09-18T14:00:00"
    destinatarios: lista de dicts con {member_id, nombre, email, ...}
    imagenes_inline: [{id, filename, data_b64, mime}] — imágenes embebidas como CID
    """
    doc = {
        "asunto":          asunto.strip(),
        "cuerpo":          cuerpo.strip(),
        "filtros":         filtros,
        "destinatarios":   destinatarios,
        "fecha_envio":     fecha_envio,
        "disclaimer":      disclaimer,
        "imagenes_inline": imagenes_inline or [],
        "estado":          "pendiente",
        "resultado":       None,
        "created_at":      datetime.now(timezone.utc),
    }
    return str(programados_col.insert_one(doc).inserted_id)


def get_envios_programados_pendientes() -> list:
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    docs = list(programados_col.find({
        "estado":     "pendiente",
        "fecha_envio": {"$lte": now_iso},
    }).sort("fecha_envio", 1))
    return _clean(docs)


def update_envio_programado_estado(envio_id: str, estado: str,
                                    resultado: dict | None = None) -> None:
    update: dict = {"estado": estado}
    if resultado is not None:
        update["resultado"] = resultado
    programados_col.update_one({"_id": ObjectId(envio_id)}, {"$set": update})


def cancel_envio_programado(envio_id: str) -> None:
    programados_col.update_one(
        {"_id": ObjectId(envio_id), "estado": "pendiente"},
        {"$set": {"estado": "cancelado"}},
    )


def get_envios_programados(limit: int = 30) -> list:
    docs = list(programados_col.find({}).sort("fecha_envio", -1).limit(limit))
    for d in docs:
        d["_id"] = str(d["_id"])
        d.setdefault("destinatarios_count", len(d.get("destinatarios", [])))
        d.pop("destinatarios", None)  # don't serialize the full list
    return docs


# ── Público (Prospectos) ──────────────────────────────────────────────────────

def get_publico_stats() -> dict:
    total       = publico_col.count_documents({})
    nuevos      = publico_col.count_documents({"estado": "nuevo"})
    interesados = publico_col.count_documents({"estado": "interesado"})
    postulantes = publico_col.count_documents({"estado": "postulante"})
    convertidos = publico_col.count_documents({"estado": "convertido"})
    desde_zoom  = publico_col.count_documents({"zoom_eventos": {"$exists": True, "$ne": []}})
    no_socios   = publico_col.count_documents({"es_socio": {"$ne": True}, "estado": {"$ne": "inactivo"}})
    return {
        "total": total, "nuevos": nuevos, "interesados": interesados,
        "postulantes": postulantes, "convertidos": convertidos,
        "desde_zoom": desde_zoom, "no_socios": no_socios,
    }


def get_all_tags() -> list:
    """Todos los tags distintos en publico con frecuencia de uso, ordenados desc."""
    pipeline = [
        {"$unwind": "$tags"},
        {"$match": {"tags": {"$nin": [None, ""]}}},
        {"$group": {"_id": "$tags", "count": {"$sum": 1}}},
        {"$sort": {"count": -1, "_id": 1}},
    ]
    return [{"tag": d["_id"], "count": d["count"]} for d in publico_col.aggregate(pipeline)]


# ── Segmentación comercial de Público ──────────────────────────────────────────
# Interés según lo que la persona hizo con IPIDET (eventos y su etapa). Es una regla del sistema,
# se calcula al consultar: no hay que mantener campos aparte.
#   caliente: postulante, o asistió a 2 o más eventos, o asistió a uno en los últimos 90 días
#   tibio:    se inscribió a algún evento o está «interesado»
#   frío:     el resto
INTERES_PUBLICO = {"caliente": ("Caliente", "red"), "tibio": ("Tibio", "amber"), "frio": ("Frío", "blue")}
_DIAS_RECIENTE = 90


def _desde_dias(dias: int) -> str:
    return (datetime.now(timezone.utc) - _timedelta(days=dias)).strftime("%Y-%m-%d")


def _q_asistencias_min(n: int) -> dict:
    return {"$expr": {"$gte": [{"$size": {"$filter": {"input": {"$ifNull": ["$zoom_eventos", []]}, "as": "z",
                                                      "cond": {"$eq": ["$$z.asistio", True]}}}}, n]}}


def _q_interes(nivel: str) -> dict:
    caliente = [{"estado": "postulante"}, _q_asistencias_min(2),
                {"zoom_eventos": {"$elemMatch": {"asistio": True, "evento_fecha": {"$gte": _desde_dias(_DIAS_RECIENTE)}}}}]
    tibio = [{"zoom_eventos.0": {"$exists": True}}, {"estado": "interesado"}]
    if nivel == "caliente":
        return {"$or": caliente}
    if nivel == "tibio":
        return {"$and": [{"$nor": caliente}, {"$or": tibio}]}
    if nivel == "frio":
        return {"$nor": caliente + tibio}
    return {}


def interes_prospecto(d: dict) -> dict:
    """Lo mismo que _q_interes, para una ficha ya leída: {nivel, etiqueta, color, inscritos, asistidos, ultimo}."""
    ev = d.get("zoom_eventos") or []
    asistidos = [z for z in ev if z.get("asistio")]
    ultimo = max((z.get("evento_fecha") or "" for z in ev), default="")
    reciente = any((z.get("evento_fecha") or "") >= _desde_dias(_DIAS_RECIENTE) for z in asistidos)
    if d.get("estado") == "postulante" or len(asistidos) >= 2 or reciente:
        nivel = "caliente"
    elif ev or d.get("estado") == "interesado":
        nivel = "tibio"
    else:
        nivel = "frio"
    etiqueta, color = INTERES_PUBLICO[nivel]
    return {"nivel": nivel, "etiqueta": etiqueta, "color": color, "inscritos": len(ev),
            "asistidos": len(asistidos), "ultimo": ultimo}


def _build_prospectos_query(search: str = "", estado: str = "", ciudad: str = "",
                             titulo: str = "", empresa: str = "", fuente: str = "",
                             tags: list = None, es_socio: str = "",
                             zoom_evento_ids: list = None, zoom_asistio: str = "",
                             asistencias_min: int = 0, actividad: str = "", interes: str = "") -> dict:
    """Helper interno — construye la query de prospectos con todos los filtros.

    asistencias_min: asistió al menos a N eventos. actividad: "30"/"90"/"180" = tuvo un evento en esos
    días; "sin_180" = ninguno en 180 días. interes: caliente / tibio / frio (INTERES_PUBLICO)."""
    query: dict = {}
    extra: list[dict] = []
    if asistencias_min and int(asistencias_min) > 0:
        extra.append(_q_asistencias_min(int(asistencias_min)))
    if actividad in ("30", "90", "180"):
        extra.append({"zoom_eventos.evento_fecha": {"$gte": _desde_dias(int(actividad))}})
    elif actividad == "sin_180":
        extra.append({"zoom_eventos.evento_fecha": {"$not": {"$gte": _desde_dias(180)}}})
    if interes in INTERES_PUBLICO:
        extra.append(_q_interes(interes))
    if extra:
        query["$and"] = extra
    if estado:
        query["estado"] = estado
    if ciudad:
        query["ciudad"] = {"$regex": _re.escape(ciudad), "$options": "i"}
    if titulo:
        query["titulo"] = titulo
    if empresa:
        query["empresa"] = {"$regex": _re.escape(empresa), "$options": "i"}
    if fuente:
        query["fuente"] = fuente
    if tags:
        query["tags"] = {"$in": tags}
    if es_socio == "false":
        query["es_socio"] = {"$ne": True}
    elif es_socio == "true":
        query["es_socio"] = True
    ids = zoom_evento_ids or []
    # "inscrito" y "" son equivalentes: cualquier relación con el evento (inscrito con o sin asistencia)
    _asistio = zoom_asistio if zoom_asistio not in ("", "inscrito") else None
    if ids and zoom_asistio == "excluir":
        # Excluir a quien aparezca en alguno de estos eventos
        query["zoom_eventos.evento_id"] = {"$nin": ids}
    elif ids and _asistio == "true":
        query["zoom_eventos"] = {"$elemMatch": {"evento_id": {"$in": ids}, "asistio": True}}
    elif ids and _asistio == "false":
        # Inscrito pero NO asistió a ninguno de los eventos seleccionados
        query["zoom_eventos"] = {"$elemMatch": {"evento_id": {"$in": ids}, "asistio": {"$ne": True}}}
    elif ids:
        # Inscrito (con o sin asistencia) — usa $elemMatch para consistencia
        query["zoom_eventos"] = {"$elemMatch": {"evento_id": {"$in": ids}}}
    elif zoom_asistio == "excluir":
        query["zoom_eventos"] = {"$not": {"$elemMatch": {}}}
    elif zoom_asistio == "true":
        query["zoom_eventos"] = {"$elemMatch": {"asistio": True}}
    elif zoom_asistio == "false":
        query["zoom_eventos"] = {"$elemMatch": {"asistio": {"$ne": True}}}
    elif zoom_asistio == "tiene":
        query["zoom_eventos.0"] = {"$exists": True}
    if search:
        rx = {"$regex": _re.escape(search), "$options": "i"}
        query["$or"] = [{"nombre": rx}, {"apellido": rx}, {"email": rx},
                        {"empresa": rx}, {"celular": rx}]
    return query


def get_zoom_eventos_disponibles() -> list:
    """Lista de eventos Zoom únicos que tienen participantes en publico, ordenados por fecha desc."""
    pipeline = [
        {"$unwind": "$zoom_eventos"},
        {"$group": {
            "_id":    "$zoom_eventos.evento_id",
            "titulo": {"$first": "$zoom_eventos.evento_titulo"},
            "fecha":  {"$first": "$zoom_eventos.evento_fecha"},
            "count":  {"$sum": 1},
        }},
        {"$sort": {"fecha": -1}},
    ]
    return list(publico_col.aggregate(pipeline))


def get_prospectos(search: str = "", estado: str = "", ciudad: str = "",
                   titulo: str = "", empresa: str = "", fuente: str = "",
                   tags: list = None, es_socio: str = "", zoom_evento_ids: list = None,
                   zoom_asistio: str = "", page: int = 1, **segmento) -> tuple:
    query = _build_prospectos_query(search, estado, ciudad, titulo, empresa, fuente,
                                    tags, es_socio, zoom_evento_ids, zoom_asistio, **segmento)
    per_page = 50
    skip = (page - 1) * per_page
    total = publico_col.count_documents(query)
    docs  = list(publico_col.find(query).sort("created_at", -1).skip(skip).limit(per_page))
    for d in docs:
        d["_id"] = str(d["_id"])
    return docs, total


def get_prospectos_all(search: str = "", estado: str = "", ciudad: str = "",
                        titulo: str = "", empresa: str = "", fuente: str = "",
                        tags: list = None, es_socio: str = "", zoom_evento_ids: list = None,
                        zoom_asistio: str = "", **segmento) -> list:
    """Sin paginación — para envíos masivos y preview de campaña."""
    query = _build_prospectos_query(search, estado, ciudad, titulo, empresa, fuente,
                                    tags, es_socio, zoom_evento_ids, zoom_asistio, **segmento)
    docs = list(publico_col.find(query).sort("created_at", -1))
    for d in docs:
        d["_id"] = str(d["_id"])
    return docs


def upsert_prospectos_from_zoom(
    evento_id: str,
    evento_titulo: str,
    evento_fecha: str,
    zoom_webinar_id: str,
    zoom_registrants: list[dict],
) -> dict:
    """
    Upsert de participantes de Zoom en la colección publico.
    zoom_registrants ya viene enriquecido por sync_zoom_data:
    es_socio, member_id, match_via, titulo, empresa, ciudad, celular, dni.
    Deduplicación por email. Re-sync del mismo evento actualiza la entrada.
    """
    now = datetime.now(timezone.utc)
    now_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    insertados = actualizados = sin_email = 0

    for r in zoom_registrants:
        email = (r.get("email") or "").lower().strip()
        if not email or "@" not in email:
            sin_email += 1
            continue

        nombre       = r.get("nombre") or ""
        asistio      = r.get("asistio", False)
        duracion_min = r.get("duracion_min")
        es_socio     = r.get("es_socio", False)
        member_id_val = r.get("member_id")

        # Campos extra del formulario Zoom (solo los que vienen con valor)
        extra_fields = {k: r[k] for k in ("titulo", "empresa", "ciudad", "celular", "dni")
                        if r.get(k)}

        zoom_entry = {
            "evento_id":       evento_id,
            "evento_titulo":   evento_titulo,
            "evento_fecha":    evento_fecha,
            "zoom_webinar_id": zoom_webinar_id,
            "asistio":         asistio,
            "duracion_min":    duracion_min,
            "synced_at":       now_str,
        }

        existing = publico_col.find_one(
            {"email": email},
            {"_id": 1, "zoom_eventos": 1, "titulo": 1, "empresa": 1, "ciudad": 1, "celular": 1},
        )

        if existing:
            existing_zoom = existing.get("zoom_eventos") or []
            has_this_event = any(ze.get("evento_id") == evento_id for ze in existing_zoom)
            set_fields: dict = {"es_socio": es_socio, "updated_at": now}
            if member_id_val:
                set_fields["member_id"] = member_id_val
            # Solo completar campos vacíos — no pisar datos que ya existían
            for k, v in extra_fields.items():
                if not existing.get(k):
                    set_fields[k] = v
            if has_this_event:
                publico_col.update_one(
                    {"_id": existing["_id"], "zoom_eventos.evento_id": evento_id},
                    {"$set": {
                        **set_fields,
                        **{f"zoom_eventos.$.{k}": v for k, v in zoom_entry.items()},
                    }},
                )
            else:
                publico_col.update_one(
                    {"_id": existing["_id"]},
                    {"$set": set_fields, "$push": {"zoom_eventos": zoom_entry}},
                )
            actualizados += 1
        else:
            doc = {
                "nombre":         nombre,
                "apellido":       "",
                "email":          email,
                "celular":        extra_fields.get("celular", ""),
                "titulo":         extra_fields.get("titulo", ""),
                "empresa":        extra_fields.get("empresa", ""),
                "ciudad":         extra_fields.get("ciudad", ""),
                "notas":          "",
                "fuente":         "zoom",
                "fuente_detalle": evento_titulo,
                "tags":           [],
                "estado":         "nuevo",
                "es_socio":       es_socio,
                "member_id":      member_id_val,
                "zoom_eventos":   [zoom_entry],
                "comunicaciones": [],
                "created_at":     now,
                "updated_at":     now,
            }
            if extra_fields.get("dni"):
                doc["dni"] = extra_fields["dni"]
            try:
                publico_col.insert_one(doc)
                insertados += 1
            except Exception:
                actualizados += 1

    return {"insertados": insertados, "actualizados": actualizados, "sin_email": sin_email}


def get_prospecto(prospecto_id: str) -> dict | None:
    try:
        d = publico_col.find_one({"_id": ObjectId(prospecto_id)})
    except Exception:
        return None
    if d:
        d["_id"] = str(d["_id"])
    return d


def create_prospecto(nombre: str, apellido: str, email: str,
                     celular: str = "", titulo: str = "", empresa: str = "",
                     ciudad: str = "", notas: str = "", fuente: str = "manual",
                     fuente_detalle: str = "", tags: list | None = None) -> str:
    email = email.strip().lower()
    existing = publico_col.find_one({"email": email}, {"_id": 1})
    if existing:
        return str(existing["_id"])
    doc = {
        "nombre":         nombre.strip(),
        "apellido":       apellido.strip(),
        "email":          email,
        "celular":        celular.strip(),
        "titulo":         titulo.strip(),
        "empresa":        empresa.strip(),
        "ciudad":         ciudad.strip(),
        "notas":          notas.strip(),
        "fuente":         fuente,
        "fuente_detalle": fuente_detalle.strip(),
        "tags":           tags or [],
        "estado":         "nuevo",
        "postulacion":    None,
        "member_id":      None,
        "comunicaciones": [],
        "created_at":     datetime.now(timezone.utc),
        "updated_at":     datetime.now(timezone.utc),
    }
    result = publico_col.insert_one(doc)
    return str(result.inserted_id)


def guardar_prospecto_desde_correo(email: str, datos: dict, estado: str, tags: list,
                                   fuente_detalle: str = "", prospecto_id: str = "") -> tuple[str, bool]:
    """Agrega a Público a quien escribió a IPIDET, o actualiza su ficha. Devuelve (id, creado).

    Con `prospecto_id` (ficha ya vinculada al correo) el estado y las etiquetas quedan como los
    dejó el equipo. Si la ficha se encuentra por email, solo se completan los campos vacíos y las
    etiquetas se suman: no se pisa lo que alguien ya cargó a mano.
    """
    from config import catalogos
    email = (email or "").strip().lower()
    tags = list(dict.fromkeys(t.strip() for t in tags if t and t.strip()))
    datos = {k: (datos.get(k) or "").strip() for k in ("nombre", "apellido", "celular", "titulo",
                                                      "empresa", "ciudad", "notas")}
    # «convertido» solo lo pone la conversión a socio; desde la cola no se llega ni se sale de ahí.
    estado = estado if estado in catalogos.ESTADOS_PUBLICO and estado != "convertido" else ""
    ahora = datetime.now(timezone.utc)

    existente = None
    if prospecto_id:
        try:
            existente = publico_col.find_one({"_id": ObjectId(prospecto_id)})
        except Exception:
            existente = None
    if not existente and email:
        existente = publico_col.find_one({"email": email})

    if existente:
        cambios: dict = {"updated_at": ahora}
        for k, v in datos.items():
            if v and not (existente.get(k) or "").strip():
                cambios[k] = v
        if estado and existente.get("estado") != "convertido":
            cambios["estado"] = estado
        cambios["tags"] = tags if prospecto_id else list(dict.fromkeys((existente.get("tags") or []) + tags))
        publico_col.update_one({"_id": existente["_id"]}, {"$set": cambios})
        return str(existente["_id"]), False

    if not email:
        raise ValueError("El correo no tiene la dirección del remitente")
    nuevo_id = create_prospecto(
        nombre=datos["nombre"] or email.split("@")[0], apellido=datos["apellido"], email=email,
        celular=datos["celular"], titulo=datos["titulo"], empresa=datos["empresa"], ciudad=datos["ciudad"],
        notas=datos["notas"], fuente="correo", fuente_detalle=fuente_detalle[:200], tags=tags,
    )
    if estado and estado != "nuevo":
        publico_col.update_one({"_id": ObjectId(nuevo_id)}, {"$set": {"estado": estado}})
    return nuevo_id, True


def update_prospecto(prospecto_id: str, fields: dict) -> None:
    allowed = {"nombre", "apellido", "email", "celular", "dni", "titulo", "empresa",
               "ciudad", "notas", "fuente", "fuente_detalle", "tags", "estado",
               "postulacion", "member_id"}
    update = {k: v for k, v in fields.items() if k in allowed}
    update["updated_at"] = datetime.now(timezone.utc)
    publico_col.update_one({"_id": ObjectId(prospecto_id)}, {"$set": update})


def delete_prospecto(prospecto_id: str) -> None:
    publico_col.update_one(
        {"_id": ObjectId(prospecto_id)},
        {"$set": {"estado": "inactivo", "updated_at": datetime.now(timezone.utc)}},
    )


def log_comunicacion_prospecto(prospecto_id: str, asunto: str, plantilla_nombre: str = "") -> None:
    publico_col.update_one(
        {"_id": ObjectId(prospecto_id)},
        {"$push": {"comunicaciones": {
            "fecha":    datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
            "asunto":   asunto,
            "plantilla": plantilla_nombre,
        }}},
    )


CAMPOS_IMPORT_PUBLICO = ("nombre", "apellido", "email", "celular", "titulo", "empresa",
                         "ciudad", "estado", "notas", "fuente_detalle", "tags")
_EMAIL_RX = _re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def socios_por_email(emails: list[str]) -> dict[str, str]:
    """{email: member_id} de los emails que pertenecen a un socio (cualquiera de sus emails)."""
    lista = list({(e or "").strip().lower() for e in emails if e})
    res: dict[str, str] = {}
    for i in range(0, len(lista), 500):
        parte = set(lista[i:i + 500])
        for m in members_col.find({"emails.email": {"$in": list(parte)}}, {"member_id": 1, "emails.email": 1}):
            for e in m.get("emails") or []:
                correo = (e.get("email") or "").strip().lower()
                if correo in parte:
                    res[correo] = m["member_id"]
    return res


def import_prospectos_bulk(rows: list) -> dict:
    """Carga masiva de Público, por email. Cada fila: dict con CAMPOS_IMPORT_PUBLICO y `_fila`.

    Un email nuevo se inserta (fuente «excel»). Si ya existe, solo se completan los campos que
    la ficha tiene vacíos y las etiquetas se suman: una celda en blanco nunca borra un dato, y lo
    que el equipo cargó a mano no se pisa. El estado se aplica si es válido (nunca «convertido»).
    Un email de socio entra marcado como socio (es_socio + member_id, igual que Zoom), sin estado de
    postulante: se informa en `socios` [(fila, member_id)].
    Devuelve {insertados, actualizados, sin_cambios, errores, detalle_errores: [(fila, motivo)], socios}.
    """
    from config import catalogos
    insertados = actualizados = sin_cambios = 0
    detalle: list[tuple[int, str]] = []
    socios: list[tuple[int, str]] = []
    de_socio = socios_por_email([str(r.get("email") or "") for r in rows])
    vistos: set[str] = set()
    for row in rows:
        fila = row.get("_fila", 0)
        email = str(row.get("email") or "").strip().lower()
        if not email:
            detalle.append((fila, "sin email"))
            continue
        if not _EMAIL_RX.match(email):
            detalle.append((fila, f"email no válido ({email[:40]})"))
            continue
        if email in vistos:
            detalle.append((fila, f"email repetido en el archivo ({email})"))
            continue
        vistos.add(email)
        socio = de_socio.get(email)
        if socio:
            socios.append((fila, socio))
        try:
            tags = list(dict.fromkeys(t.strip() for t in str(row.get("tags") or "").split(",") if t.strip()))
            datos = {k: str(row.get(k) or "").strip() for k in
                     ("nombre", "apellido", "celular", "titulo", "empresa", "ciudad", "notas", "fuente_detalle")}
            estado = str(row.get("estado") or "").strip().lower()
            if estado and (estado not in catalogos.ESTADOS_PUBLICO or estado == "convertido"):
                estado = ""
            if socio and estado == "postulante":  # un socio no postula
                estado = ""
            ahora = datetime.now(timezone.utc)
            existente = publico_col.find_one({"email": email})
            if existente:
                cambios: dict = {k: v for k, v in datos.items()
                                 if v and not str(existente.get(k) or "").strip()}
                tags_previos = existente.get("tags") or []
                if any(t not in tags_previos for t in tags):
                    cambios["tags"] = list(dict.fromkeys(tags_previos + tags))
                if estado and estado != existente.get("estado") and existente.get("estado") != "convertido":
                    cambios["estado"] = estado
                if socio and (not existente.get("es_socio") or existente.get("member_id") != socio):
                    cambios.update(es_socio=True, member_id=socio)
                if cambios:
                    cambios["updated_at"] = ahora
                    publico_col.update_one({"_id": existente["_id"]}, {"$set": cambios})
                    actualizados += 1
                else:
                    sin_cambios += 1
                persona_id = str(existente["_id"])
            else:
                persona_id = str(publico_col.insert_one({
                    **datos,
                    "email":          email,
                    "tags":           tags,
                    "fuente":         "excel",
                    "estado":         estado or "nuevo",
                    "es_socio":       bool(socio),
                    "member_id":      socio,
                    "comunicaciones": [],
                    "created_at":     ahora,
                    "updated_at":     ahora,
                }).inserted_id)
                insertados += 1
            if estado == "postulante":
                try:
                    crear_postulacion(persona_id, origen="excel", usuario="importación")
                except ValueError as exc:   # ya es socio: queda en Público, sin postulación
                    detalle.append((fila, f"no se abrió su postulación: {exc}"))
        except Exception as exc:
            detalle.append((fila, f"no se pudo guardar ({type(exc).__name__})"))
    return {"insertados": insertados, "actualizados": actualizados, "sin_cambios": sin_cambios,
            "errores": len(detalle), "detalle_errores": detalle, "socios": socios}


# ── Postulantes (proceso de admisión) ─────────────────────────────────────────
# Una postulación por proceso, unida por `prospecto_id` a la persona en Público, que es la única
# ficha con sus datos. Nunca toca `members`: el socio solo se crea con alta_postulante_como_socio.

def _hist(accion: str, usuario: str, detalle: str = "") -> dict:
    return {"fecha": datetime.now(timezone.utc), "accion": accion, "usuario": usuario or "equipo",
            "detalle": (detalle or "")[:500]}


def _socio_con_email(email: str) -> dict | None:
    email = (email or "").strip()
    if not email:
        return None
    return members_col.find_one({"emails.email": {"$regex": f"^{_re.escape(email)}$", "$options": "i"}},
                                {"member_id": 1, "nombres": 1, "apellidos": 1})


def postulacion_abierta(prospecto_id: str) -> dict | None:
    return postulantes_col.find_one({"prospecto_id": prospecto_id,
                                     "etapa": {"$in": list(catalogos.ETAPAS_POSTULANTE_ABIERTAS)}})


def postulaciones_por_prospecto(prospecto_ids: list[str]) -> dict[str, dict]:
    """La postulación más reciente de cada persona (abierta o no), para mostrarla en Público y la cola."""
    if not prospecto_ids:
        return {}
    res: dict[str, dict] = {}
    for p in postulantes_col.find({"prospecto_id": {"$in": list(set(prospecto_ids))}},
                                  {"prospecto_id": 1, "etapa": 1, "created_at": 1}).sort("created_at", 1):
        res[p["prospecto_id"]] = {"id": str(p["_id"]), "etapa": p.get("etapa")}
    return res


def _sync_estado_publico(prospecto_id: str, etapa: str) -> None:
    """Público refleja la postulación: abierta → «postulante»; cerrada → «interesado»."""
    p = publico_col.find_one({"_id": ObjectId(prospecto_id)}, {"estado": 1})
    if not p or p.get("estado") == "convertido":
        return
    if etapa in catalogos.ETAPAS_POSTULANTE_ABIERTAS:
        nuevo = "postulante"
    elif etapa == "socio":
        nuevo = "convertido"
    else:
        nuevo = "interesado" if p.get("estado") == "postulante" else p.get("estado")
    if nuevo != p.get("estado"):
        publico_col.update_one({"_id": p["_id"]}, {"$set": {"estado": nuevo, "updated_at": datetime.now(timezone.utc)}})


def crear_postulacion(prospecto_id: str, origen: str = "manual", usuario: str = "",
                      tipo_socio: str = "", nota: str = "", correo_id: str = "",
                      detalle: str = "") -> tuple[str, bool]:
    """Abre la postulación de una persona de Público. Si ya tiene una abierta, la devuelve.
    Devuelve (id, creada). Rechaza a quien ya es socio: los postulantes no se mezclan con el padrón."""
    try:
        persona = publico_col.find_one({"_id": ObjectId(prospecto_id)})
    except Exception:
        persona = None
    if not persona:
        raise ValueError("La persona no está en Público")
    if persona.get("member_id") or persona.get("estado") == "convertido":
        raise ValueError("Esta persona ya es socio de IPIDET")
    socio = _socio_con_email(persona.get("email", ""))
    if socio:
        raise ValueError(f"Su correo ya pertenece al socio {socio.get('member_id')}: no se registra como postulante")
    abierta = postulacion_abierta(prospecto_id)
    if abierta:
        if correo_id and correo_id not in (abierta.get("correo_ids") or []):
            postulantes_col.update_one({"_id": abierta["_id"]}, {
                "$push": {"historial": _hist("nuevo correo", usuario, detalle)},
                "$addToSet": {"correo_ids": correo_id},
                "$set": {"updated_at": datetime.now(timezone.utc)},
            })
        return str(abierta["_id"]), False
    ahora = datetime.now(timezone.utc)
    tipos_validos = {t.get("valor") for t in catalogos.get_catalogo("tipos_socio")}
    doc = {
        "prospecto_id":      prospecto_id,
        "etapa":             "consulta",
        "etapa_desde":       ahora,
        "tipo_socio":        tipo_socio if tipo_socio in tipos_validos else "",
        "origen":            origen if origen in catalogos.ORIGENES_POSTULANTE else "manual",
        "responsable":       "",
        "proxima_accion":    "",
        "fecha_seguimiento": "",
        "requisitos":        {},
        "notas":             [{"texto": nota.strip(), "usuario": usuario or "equipo", "fecha": ahora}] if nota.strip() else [],
        "correo_ids":        [correo_id] if correo_id else [],
        "historial":         [_hist("postulación abierta", usuario,
                                    detalle or catalogos.ORIGENES_POSTULANTE.get(origen, origen))],
        "member_id":         None,
        "motivo_cierre":     "",
        "created_at":        ahora,
        "updated_at":        ahora,
    }
    pid = str(postulantes_col.insert_one(doc).inserted_id)
    _sync_estado_publico(prospecto_id, "consulta")
    return pid, True


def _persona_publico(p: dict | None) -> dict:
    p = p or {}
    return {k: p.get(k) or "" for k in ("nombre", "apellido", "email", "celular", "dni", "titulo",
                                       "empresa", "ciudad", "fuente", "fuente_detalle")} | \
           {"tags": p.get("tags") or [], "nombre_completo": f"{p.get('apellido', '')} {p.get('nombre', '')}".strip()}


def _enriquecer_postulante(d: dict, persona: dict | None, requisitos: list, hoy: str) -> dict:
    d["_id"] = str(d["_id"])
    d["persona"] = _persona_publico(persona)
    hechos = d.get("requisitos") or {}
    d["req_total"] = len(requisitos)
    d["req_hechos"] = sum(1 for r in requisitos if (hechos.get(r["id"]) or {}).get("hecho"))
    fs = d.get("fecha_seguimiento") or ""
    abierta = d.get("etapa") in catalogos.ETAPAS_POSTULANTE_ABIERTAS
    d["abierta"] = abierta
    d["seguimiento_estado"] = ("" if not abierta or not fs else
                               "vencido" if fs < hoy else "hoy" if fs == hoy else "pendiente")
    desde = d.get("etapa_desde") or d.get("created_at")
    if isinstance(desde, datetime):
        desde = desde if desde.tzinfo else desde.replace(tzinfo=timezone.utc)
        d["dias_en_etapa"] = (datetime.now(timezone.utc) - desde).days
    else:
        d["dias_en_etapa"] = None
    return d


def get_postulantes(search: str = "", etapa: str = "abiertas", tipo_socio: str = "",
                    responsable: str = "", seguimiento: str = "", origen: str = "",
                    page: int = 1, per_page: int = 50) -> tuple[list, int, dict]:
    """Lista paginada. etapa: «abiertas» (por defecto), «todas» o una clave de ETAPAS_POSTULANTE.
    Devuelve (docs, total, conteo por etapa)."""
    hoy = _date.today().isoformat()
    q: dict = {}
    if etapa == "abiertas" or not etapa:
        q["etapa"] = {"$in": list(catalogos.ETAPAS_POSTULANTE_ABIERTAS)}
    elif etapa in catalogos.ETAPAS_POSTULANTE:
        q["etapa"] = etapa
    if tipo_socio:
        q["tipo_socio"] = tipo_socio
    if origen:
        q["origen"] = origen
    if responsable == "sin":
        q["responsable"] = {"$in": [None, ""]}
    elif responsable:
        q["responsable"] = responsable
    if seguimiento == "vencido":
        q["fecha_seguimiento"] = {"$nin": [None, ""], "$lt": hoy}
    elif seguimiento == "hoy":
        q["fecha_seguimiento"] = hoy
    elif seguimiento == "semana":
        q["fecha_seguimiento"] = {"$gte": hoy, "$lte": (_date.today() + _timedelta(days=7)).isoformat()}
    elif seguimiento == "sin":
        q["fecha_seguimiento"] = {"$in": [None, ""]}
    if search:
        rx = {"$regex": _re.escape(search.strip()), "$options": "i"}
        ids = [str(p["_id"]) for p in publico_col.find(
            {"$or": [{"nombre": rx}, {"apellido": rx}, {"email": rx}, {"empresa": rx}, {"celular": rx}, {"dni": rx}]},
            {"_id": 1}).limit(2000)]
        q["prospecto_id"] = {"$in": ids}

    conteo = {k: 0 for k in catalogos.ETAPAS_POSTULANTE}
    q_sin_etapa = {k: v for k, v in q.items() if k != "etapa"}
    for row in postulantes_col.aggregate([{"$match": q_sin_etapa}, {"$group": {"_id": "$etapa", "n": {"$sum": 1}}}]):
        conteo[row["_id"]] = row["n"]
    conteo["abiertas"] = sum(conteo.get(k, 0) for k in catalogos.ETAPAS_POSTULANTE_ABIERTAS)
    conteo["todas"] = sum(v for k, v in conteo.items() if k in catalogos.ETAPAS_POSTULANTE)

    total = postulantes_col.count_documents(q)
    # Primero lo que hay que atender: seguimiento más próximo (los sin fecha al final), luego lo más nuevo.
    docs = list(postulantes_col.aggregate([
        {"$match": q},
        {"$addFields": {"_sin_fecha": {"$cond": [{"$in": [{"$ifNull": ["$fecha_seguimiento", ""]}, ["", None]]}, 1, 0]}}},
        {"$sort": {"_sin_fecha": 1, "fecha_seguimiento": 1, "created_at": -1}},
        {"$skip": max(page - 1, 0) * per_page}, {"$limit": per_page},
    ]))
    personas = {str(p["_id"]): p for p in publico_col.find(
        {"_id": {"$in": [ObjectId(d["prospecto_id"]) for d in docs if ObjectId.is_valid(d.get("prospecto_id", ""))]}})}
    requisitos = catalogos.get_catalogo("requisitos_admision")
    return [_enriquecer_postulante(d, personas.get(d.get("prospecto_id")), requisitos, hoy) for d in docs], total, conteo


def get_postulante(postulante_id: str) -> dict | None:
    try:
        d = postulantes_col.find_one({"_id": ObjectId(postulante_id)})
    except Exception:
        return None
    if not d:
        return None
    persona = publico_col.find_one({"_id": ObjectId(d["prospecto_id"])}) if ObjectId.is_valid(d.get("prospecto_id", "")) else None
    d = _enriquecer_postulante(d, persona, catalogos.get_catalogo("requisitos_admision"), _date.today().isoformat())
    d["requisitos"] = _clean(d.get("requisitos") or {})   # va a la página como JSON
    d["persona"]["zoom_eventos"] = (persona or {}).get("zoom_eventos") or []
    d["otras"] = [_clean(o) for o in postulantes_col.find(
        {"prospecto_id": d["prospecto_id"], "_id": {"$ne": ObjectId(d["_id"])}},
        {"etapa": 1, "created_at": 1, "motivo_cierre": 1}).sort("created_at", -1)]
    return d


def get_responsables_postulantes() -> list[str]:
    return sorted(r for r in postulantes_col.distinct("responsable") if r)


def actualizar_postulante(postulante_id: str, campos: dict, usuario: str) -> None:
    """Cambia etapa, tipo de socio, responsable y seguimiento. Deja todo en el historial.
    Cerrar (desistió / no admitido) pide motivo. «socio» no se elige: es el alta."""
    d = postulantes_col.find_one({"_id": ObjectId(postulante_id)})
    if not d:
        raise ValueError("Postulación no encontrada")
    ahora = datetime.now(timezone.utc)
    cambios: dict = {}
    eventos: list = []
    etapa = campos.get("etapa")
    if etapa and etapa != d.get("etapa"):
        if etapa not in catalogos.ETAPAS_POSTULANTE or etapa == "socio":
            raise ValueError("Etapa no válida")
        if d.get("etapa") == "socio":
            raise ValueError("Ya es socio: la postulación está cerrada")
        motivo = (campos.get("motivo_cierre") or "").strip()
        if etapa in catalogos.ETAPAS_POSTULANTE_CIERRE and not motivo:
            raise ValueError("Escribe el motivo del cierre")
        if etapa not in catalogos.ETAPAS_POSTULANTE_CIERRE:
            # Reabrir: no puede haber dos postulaciones abiertas de la misma persona.
            otra = postulacion_abierta(d["prospecto_id"])
            if otra and otra["_id"] != d["_id"]:
                raise ValueError("Esta persona ya tiene otra postulación abierta")
        cambios.update({"etapa": etapa, "etapa_desde": ahora,
                        "motivo_cierre": motivo if etapa in catalogos.ETAPAS_POSTULANTE_CIERRE else ""})
        etiqueta = catalogos.ETAPAS_POSTULANTE[etapa][0]
        eventos.append(_hist("etapa", usuario, f"{etiqueta}{': ' + motivo if motivo else ''}"))
    if "tipo_socio" in campos:
        tipos = {t.get("valor"): t.get("label") or t.get("valor") for t in catalogos.get_catalogo("tipos_socio")}
        if (campos["tipo_socio"] in tipos or campos["tipo_socio"] == "") and campos["tipo_socio"] != d.get("tipo_socio"):
            cambios["tipo_socio"] = campos["tipo_socio"]
            eventos.append(_hist("tipo de socio", usuario, tipos.get(campos["tipo_socio"]) or "por definir"))
    for k, etiqueta in (("responsable", "responsable"), ("proxima_accion", "próxima acción"),
                        ("fecha_seguimiento", "seguimiento")):
        if k in campos:
            v = (campos[k] or "").strip()[:300]
            if k == "fecha_seguimiento" and v and not _re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
                raise ValueError("Fecha de seguimiento no válida")
            if v != (d.get(k) or ""):
                cambios[k] = v
                visible = "/".join(reversed(v.split("-"))) if k == "fecha_seguimiento" and v else v
                eventos.append(_hist(etiqueta, usuario, visible or "—"))
    if not cambios:
        return
    cambios["updated_at"] = ahora
    postulantes_col.update_one({"_id": d["_id"]}, {"$set": cambios, "$push": {"historial": {"$each": eventos}}})
    if "etapa" in cambios:
        _sync_estado_publico(d["prospecto_id"], cambios["etapa"])


def marcar_requisito_postulante(postulante_id: str, req_id: str, hecho: bool, usuario: str) -> None:
    req = next((r for r in catalogos.get_catalogo("requisitos_admision") if r.get("id") == req_id), None)
    if not req:
        raise ValueError("Requisito desconocido")
    postulantes_col.update_one({"_id": ObjectId(postulante_id)}, {
        "$set": {f"requisitos.{req_id}": {"hecho": bool(hecho), "fecha": datetime.now(timezone.utc),
                                          "usuario": usuario or "equipo"},
                 "updated_at": datetime.now(timezone.utc)},
        "$push": {"historial": _hist("requisito", usuario, f"{'✓' if hecho else '✗'} {req.get('label', req_id)}")},
    })


def agregar_nota_postulante(postulante_id: str, texto: str, usuario: str) -> None:
    texto = (texto or "").strip()
    if not texto:
        raise ValueError("La nota está vacía")
    postulantes_col.update_one({"_id": ObjectId(postulante_id)}, {
        "$push": {"notas": {"texto": texto[:3000], "usuario": usuario or "equipo", "fecha": datetime.now(timezone.utc)}},
        "$set": {"updated_at": datetime.now(timezone.utc)},
    })


def alta_postulante_como_socio(postulante_id: str, periodo_actual: str | None, usuario: str) -> str:
    """Único paso que crea al socio: solo desde «Aprobado». Devuelve el member_id."""
    d = postulantes_col.find_one({"_id": ObjectId(postulante_id)})
    if not d:
        raise ValueError("Postulación no encontrada")
    if d.get("etapa") != "aprobado":
        raise ValueError("Primero marca la postulación como «Aprobado»")
    persona = publico_col.find_one({"_id": ObjectId(d["prospecto_id"])}, {"email": 1})
    socio = _socio_con_email((persona or {}).get("email", ""))
    if socio:
        raise ValueError(f"Su correo ya pertenece al socio {socio.get('member_id')}")
    mid = convert_prospecto_to_member(d["prospecto_id"], periodo_actual, cerrar_postulacion=False)
    if d.get("tipo_socio"):
        update_member_tipo_socio(mid, d["tipo_socio"])
    postulantes_col.update_one({"_id": d["_id"]}, {
        "$set": {"etapa": "socio", "etapa_desde": datetime.now(timezone.utc), "member_id": mid,
                 "updated_at": datetime.now(timezone.utc)},
        "$push": {"historial": _hist("alta como socio", usuario, mid)},
    })
    return mid


def get_postulantes_stats() -> dict:
    hoy = _date.today().isoformat()
    abiertas = {"etapa": {"$in": list(catalogos.ETAPAS_POSTULANTE_ABIERTAS)}}
    inicio_mes = _date.today().replace(day=1)
    return {
        "abiertas":    postulantes_col.count_documents(abiertas),
        "vencidos":    postulantes_col.count_documents({**abiertas, "fecha_seguimiento": {"$nin": [None, ""], "$lt": hoy}}),
        "sin_responsable": postulantes_col.count_documents({**abiertas, "responsable": {"$in": [None, ""]}}),
        "nuevos_mes":  postulantes_col.count_documents({"created_at": {"$gte": datetime(inicio_mes.year, inicio_mes.month, 1, tzinfo=timezone.utc)}}),
        "socios_mes":  postulantes_col.count_documents({"etapa": "socio", "etapa_desde": {"$gte": datetime(inicio_mes.year, inicio_mes.month, 1, tzinfo=timezone.utc)}}),
    }


def convert_prospecto_to_member(prospecto_id: str, periodo_actual: str | None = None,
                                cerrar_postulacion: bool = True) -> str:
    """Crea un socio desde el prospecto y lo marca como convertido. Devuelve el member_id.
    Si tiene una postulación abierta, queda cerrada como «Ya es socio»."""
    p = publico_col.find_one({"_id": ObjectId(prospecto_id)})
    if not p:
        raise ValueError("Prospecto no encontrado")
    if p.get("member_id"):
        return p["member_id"]
    socio = _socio_con_email(p.get("email", ""))
    if socio:
        raise ValueError(f"Su correo ya pertenece al socio {socio.get('member_id')}")
    mid = create_member(
        apellidos      = p.get("apellido") or "",
        nombres        = p.get("nombre") or "",
        titulo         = p.get("titulo") or "",
        email          = p.get("email") or "",
        celular        = p.get("celular") or "",
        centro_trabajo = p.get("empresa") or "",
        ubicacion      = p.get("ciudad") or "",
        notas          = p.get("notas") or "",
        periodo_actual = periodo_actual,
    )
    update_prospecto(prospecto_id, {"estado": "convertido", "member_id": mid})
    if cerrar_postulacion:
        abierta = postulacion_abierta(prospecto_id)
        if abierta:
            if abierta.get("tipo_socio"):
                update_member_tipo_socio(mid, abierta["tipo_socio"])
            postulantes_col.update_one({"_id": abierta["_id"]}, {
                "$set": {"etapa": "socio", "etapa_desde": datetime.now(timezone.utc), "member_id": mid,
                         "updated_at": datetime.now(timezone.utc)},
                "$push": {"historial": _hist("alta como socio", "equipo", f"{mid} · convertido desde Público")},
            })
    return mid


# ── Redes Sociales ────────────────────────────────────────────────────────────

PLATAFORMAS_CONFIG = {
    "linkedin":  {"label": "LinkedIn",  "color": "#0A66C2"},
    "youtube":   {"label": "YouTube",   "color": "#FF0000"},
    "tiktok":    {"label": "TikTok",    "color": "#010101"},
    "instagram": {"label": "Instagram", "color": "#C13584"},
}

TIPOS_CONTENIDO_REDES = [
    "video", "imagen", "carrusel", "reel", "short", "artículo", "historia", "otro",
]

ESTADOS_CRONOGRAMA = ["borrador", "programado", "publicado", "archivado", "cancelado"]


def get_redes_cuentas(solo_activas: bool = False) -> list:
    q = {"activo": True} if solo_activas else {}
    docs = list(redes_cuentas_col.find(q).sort("plataforma", 1))
    return _clean(docs)


def create_red_cuenta(plataforma: str, nombre: str, url: str, handle: str = "") -> str:
    doc = {
        "plataforma": plataforma,
        "nombre":     nombre,
        "handle":     handle,
        "url":        url,
        "activo":     True,
        "created_at": datetime.now(timezone.utc),
    }
    return str(redes_cuentas_col.insert_one(doc).inserted_id)


def update_red_cuenta(cuenta_id: str, fields: dict) -> None:
    redes_cuentas_col.update_one({"_id": ObjectId(cuenta_id)}, {"$set": fields})


def delete_red_cuenta(cuenta_id: str) -> None:
    redes_cuentas_col.delete_one({"_id": ObjectId(cuenta_id)})


def save_red_metrica(cuenta_id: str, plataforma: str, seguidores: int,
                     siguiendo: int = None, posts_total: int = None,
                     views_total: int = None, likes_total: int = None) -> None:
    hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    doc = {
        "cuenta_id":   cuenta_id,
        "plataforma":  plataforma,
        "fecha":       hoy,
        "seguidores":  seguidores,
        "siguiendo":   siguiendo,
        "posts_total": posts_total,
        "views_total": views_total,
        "likes_total": likes_total,
        "created_at":  datetime.now(timezone.utc),
    }
    redes_metricas_col.replace_one({"cuenta_id": cuenta_id, "fecha": hoy}, doc, upsert=True)


def get_redes_metricas_latest() -> list:
    """Último snapshot + anterior por cada cuenta (para las tarjetas del dashboard)."""
    cuentas = get_redes_cuentas()
    result = []
    for c in cuentas:
        cid = c["_id"]
        actual = redes_metricas_col.find_one({"cuenta_id": cid}, sort=[("fecha", -1)])
        anterior = None
        if actual:
            anterior = redes_metricas_col.find_one(
                {"cuenta_id": cid, "fecha": {"$lt": actual["fecha"]}},
                sort=[("fecha", -1)],
            )
        result.append({
            **c,
            "metrica_actual":   _clean(actual)   if actual   else None,
            "metrica_anterior": _clean(anterior)  if anterior else None,
        })
    return result


def get_redes_metricas_historico() -> dict:
    """Serie temporal agrupada por cuenta — últimos 16 snapshots, para Chart.js."""
    cuentas = get_redes_cuentas(solo_activas=True)
    out: dict = {}
    for c in cuentas:
        cid = c["_id"]
        docs = list(redes_metricas_col.find({"cuenta_id": cid}).sort("fecha", -1).limit(16))
        docs.reverse()
        out[cid] = {
            "plataforma": c["plataforma"],
            "nombre":     c["nombre"],
            "fechas":     [d["fecha"] for d in docs],
            "seguidores": [d.get("seguidores") or 0 for d in docs],
        }
    return out


def get_redes_posts(plataforma: str = "", tipo: str = "", search: str = "",
                    page: int = 1, per_page: int = 50):
    q: dict = {}
    if plataforma:
        q["plataforma"] = plataforma
    if tipo:
        q["tipo"] = tipo
    if search:
        q["$or"] = [
            {"titulo":     {"$regex": search, "$options": "i"}},
            {"descripcion":{"$regex": search, "$options": "i"}},
        ]
    total = redes_posts_col.count_documents(q)
    skip = (page - 1) * per_page
    docs = list(redes_posts_col.find(q).sort("fecha_publicacion", -1).skip(skip).limit(per_page))
    return _clean(docs), total


def create_red_post(cuenta_id: str, plataforma: str, url: str, tipo: str,
                    titulo: str, fecha_publicacion: str, descripcion: str = "",
                    metrics: dict = None) -> str:
    m = metrics or {}
    now = datetime.now(timezone.utc)
    doc = {
        "cuenta_id":        cuenta_id,
        "plataforma":       plataforma,
        "url":              url,
        "tipo":             tipo,
        "titulo":           titulo,
        "descripcion":      descripcion,
        "fecha_publicacion":fecha_publicacion,
        "views":            m.get("views"),
        "likes":            m.get("likes"),
        "comentarios":      m.get("comentarios"),
        "compartidos":      m.get("compartidos"),
        "guardados":        m.get("guardados"),
        "alcance":          m.get("alcance"),
        "impresiones":      m.get("impresiones"),
        "snapshots":        [],
        "created_at":       now,
        "updated_at":       now,
    }
    return str(redes_posts_col.insert_one(doc).inserted_id)


def update_red_post(post_id: str, fields: dict) -> None:
    fields["updated_at"] = datetime.now(timezone.utc)
    redes_posts_col.update_one({"_id": ObjectId(post_id)}, {"$set": fields})


def delete_red_post(post_id: str) -> None:
    redes_posts_col.delete_one({"_id": ObjectId(post_id)})


def get_cronograma(plataforma: str = "", estado: str = "", search: str = "",
                   fecha_desde: str = "", fecha_hasta: str = "") -> list:
    q: dict = {}
    if plataforma:
        q["plataforma"] = plataforma
    if estado:
        q["estado"] = estado
    if search:
        q["$or"] = [
            {"titulo":     {"$regex": search, "$options": "i"}},
            {"descripcion":{"$regex": search, "$options": "i"}},
        ]
    if fecha_desde or fecha_hasta:
        fq: dict = {}
        if fecha_desde:
            fq["$gte"] = fecha_desde
        if fecha_hasta:
            fq["$lte"] = fecha_hasta
        q["fecha_planificada"] = fq
    docs = list(redes_cronograma_col.find(q).sort("fecha_planificada", 1))
    return _clean(docs)


def create_cronograma_item(plataforma: str, titulo: str, descripcion: str,
                            tipo_contenido: str, fecha_planificada: str,
                            hora_planificada: str = "", estado: str = "borrador",
                            responsable: str = "", notas: str = "",
                            cuenta_id: str = "") -> str:
    now = datetime.now(timezone.utc)
    doc = {
        "plataforma":        plataforma,
        "cuenta_id":         cuenta_id or None,
        "titulo":            titulo,
        "descripcion":       descripcion,
        "tipo_contenido":    tipo_contenido,
        "fecha_planificada": fecha_planificada,
        "hora_planificada":  hora_planificada or None,
        "estado":            estado,
        "responsable":       responsable,
        "notas":             notas,
        "post_id":           None,
        "created_at":        now,
        "updated_at":        now,
    }
    return str(redes_cronograma_col.insert_one(doc).inserted_id)


def update_cronograma_item(item_id: str, fields: dict) -> None:
    fields["updated_at"] = datetime.now(timezone.utc)
    redes_cronograma_col.update_one({"_id": ObjectId(item_id)}, {"$set": fields})


def delete_cronograma_item(item_id: str) -> None:
    redes_cronograma_col.delete_one({"_id": ObjectId(item_id)})


# ── Índices MongoDB ───────────────────────────────────────────────────────────

def _ensure_indexes() -> None:
    """Crea índices en startup. Idempotente — seguro llamar múltiples veces."""
    from pymongo import ASCENDING, DESCENDING
    # members
    members_col.create_index([("member_id", ASCENDING)], unique=True, background=True)
    members_col.create_index([("emails.email", ASCENDING)], background=True)
    members_col.create_index([("apellidos", ASCENDING)], background=True)
    members_col.create_index([("estado", ASCENDING)], background=True)
    # payments
    payments_col.create_index([("member_id", ASCENDING), ("periodo", ASCENDING)],
                               unique=True, background=True)
    payments_col.create_index([("estado", ASCENDING)], background=True)
    payments_col.create_index([("empresa_id", ASCENDING)], background=True)
    payments_col.create_index([("comprobante_id", ASCENDING)], background=True)
    # comprobantes
    comprobantes_col.create_index([("numero", ASCENDING)], background=True)
    comprobantes_col.create_index([("fecha_emision", DESCENDING)], background=True)
    comprobantes_col.create_index([("empresa_id", ASCENDING)], background=True)
    # publico
    publico_col.create_index([("email", ASCENDING)], unique=True, background=True)
    publico_col.create_index([("estado", ASCENDING)], background=True)
    # redes sociales
    redes_metricas_col.create_index([("cuenta_id", ASCENDING), ("fecha", ASCENDING)],
                                     unique=True, background=True)
    redes_posts_col.create_index([("plataforma", ASCENDING)], background=True)
    redes_posts_col.create_index([("fecha_publicacion", DESCENDING)], background=True)
    redes_cronograma_col.create_index([("fecha_planificada", ASCENDING)], background=True)
    redes_cronograma_col.create_index([("estado", ASCENDING)], background=True)
    # faqs
    faqs_col.create_index([("active", ASCENDING)], background=True)
    # eventos
    eventos_col.create_index([("fecha", DESCENDING)], background=True)
    # comunicaciones
    comunicaciones_col.create_index([("fecha_envio", DESCENDING)], background=True)


try:
    _ensure_indexes()
except Exception as _idx_err:
    print(f"[db] Warning: no se pudieron crear índices: {_idx_err}")


# ── Alianzas ──────────────────────────────────────────────────────────────────

alianzas_col = _db.alianzas
alianzas_corridas_col = _db.alianzas_corridas


def _sin_acentos(texto: str) -> str:
    return "".join(c for c in _unicodedata.normalize("NFD", texto or "") if _unicodedata.category(c) != "Mn")


def _clave_alianza(nombre: str) -> str:
    return _re.sub(r"[^a-z0-9]+", " ", _sin_acentos(nombre).lower()).strip()


def _utc(valor):
    """Atlas devuelve fechas sin zona; todas se guardan en UTC."""
    return valor.replace(tzinfo=timezone.utc) if isinstance(valor, datetime) and valor.tzinfo is None else valor


_CORRIDAS = {"alianzas": "alianzas_corridas", "ponentes": "ponentes_corridas"}


def registrar_corrida(tipo: str, extra: dict | None = None) -> str:
    """Historial de importaciones hechas desde Claude Code (investigacion.py)."""
    doc = {"estado": "en_curso", "origen": "claude_code", "inicio": datetime.now(timezone.utc), **(extra or {})}
    return str(_db[_CORRIDAS[tipo]].insert_one(doc).inserted_id)


def cerrar_corrida(tipo: str, corrida_id: str, resultado: dict) -> None:
    _db[_CORRIDAS[tipo]].update_one({"_id": ObjectId(corrida_id)}, {"$set": {
        "estado": "completada", "fin": datetime.now(timezone.utc),
        "resultado": {k: v for k, v in resultado.items() if k not in ("aceptadas", "aceptados")}}})
    catalogos.invalidar()


def get_corridas_alianzas(limite: int = 8) -> list:
    docs = list(alianzas_corridas_col.find().sort("inicio", -1).limit(limite))
    for d in docs:
        d["_id"] = str(d["_id"])
    return docs


def get_alianzas_nombres() -> list:
    return list(alianzas_col.find({}, {"_id": 0, "nombre": 1, "estado": 1}).sort("nombre", 1))


def _buscar_alianza_existente(clave: str, siglas: str, dominio: str) -> dict | None:
    condiciones: list = [{"clave": clave}]
    if len(siglas) >= 3:
        condiciones.append({"siglas": {"$regex": f"^{_re.escape(siglas)}$", "$options": "i"}})
    if dominio:
        condiciones.append({"dominio": dominio})
    return alianzas_col.find_one({"$or": condiciones})


def guardar_propuesta_alianza(doc: dict, corrida_id: str) -> str:
    """Devuelve "nueva", "actualizada" o "ignorada". Nunca pisa el estado ni las notas del equipo."""
    ahora = datetime.now(timezone.utc)
    clave = _clave_alianza(doc["nombre"])
    novedad = doc.pop("novedad", "")
    previa = _buscar_alianza_existente(clave, doc.get("siglas", ""), doc.get("dominio", ""))
    if not previa:
        try:
            alianzas_col.insert_one({**doc, "clave": clave, "origen": "busqueda", "estado": "nueva",
                                     "puntaje": doc["afinidad"] + doc["impacto"] + doc["viabilidad"],
                                     "responsable": "", "notas": "", "fecha_seguimiento": "", "novedades": [],
                                     "corrida_id": corrida_id, "created_at": ahora, "updated_at": ahora})
        except DuplicateKeyError:
            return "ignorada"
        return "nueva"
    if previa.get("estado") == "descartada" or not novedad:
        return "ignorada"
    fuentes = {f["url"]: f for f in previa.get("fuentes", []) + doc.get("fuentes", [])}
    cambios = {"fuentes": list(fuentes.values())[:15], "updated_at": ahora}
    if previa.get("estado") == "nueva":
        for campo in ("que_hace", "por_que_importante", "por_que_estrategica", "como_colaborar", "primer_paso",
                      "riesgos", "afinidad", "impacto", "viabilidad"):
            cambios[campo] = doc[campo]
        cambios["puntaje"] = doc["afinidad"] + doc["impacto"] + doc["viabilidad"]
    alianzas_col.update_one({"_id": previa["_id"]}, {
        "$set": cambios,
        "$push": {"novedades": {"$each": [{"fecha": ahora, "texto": novedad, "corrida_id": corrida_id}], "$position": 0}},
    })
    return "actualizada"


def get_alianzas(search: str = "", tipo: str = "", ambito: str = "", estado: str = "", orden: str = "puntaje") -> list:
    query: dict = {}
    if search.strip():
        rx = {"$regex": _re.escape(search.strip()), "$options": "i"}
        query["$or"] = [{"nombre": rx}, {"siglas": rx}, {"pais": rx}, {"que_hace": rx},
                        {"por_que_importante": rx}, {"por_que_estrategica": rx}]
    if tipo:
        query["tipo"] = tipo
    if ambito:
        query["ambito"] = ambito
    query["estado"] = estado if estado else {"$ne": "descartada"}
    campo = "created_at" if orden == "recientes" else "puntaje"
    docs = list(alianzas_col.find(query).sort([(campo, -1), ("nombre", 1)]))
    for d in docs:
        d["_id"] = str(d["_id"])
    _agregar_socios_vinculados(docs)
    return docs


# ── Coincidencia de instituciones con el centro de trabajo de los socios ──────
# El centro de trabajo se escribe a mano («PRICEWATERHOUSECOOPERS S.Civil…», «PWC»), así que
# se compara el núcleo del nombre, sin forma legal, y se suman las variantes de /empresas
# (nombre comercial y razón social). Para que «PwC» y «PricewaterhouseCoopers» coincidan,
# la empresa debe tener su razón social cargada en /empresas.

_FORMAS_LEGALES = {"de", "del", "la", "las", "los", "el", "y", "e", "and", "en", "sa", "sac", "saa", "srl", "sas",
                   "ltda", "eirl", "scrl", "cscrl", "escrl", "inc", "llc", "civil", "sociedad", "comercial",
                   "responsabilidad", "limitada", "anonima", "cerrada", "abierta", "asesores", "consultores",
                   "empresariales", "tributarios", "legales", "peru"}
_GENERICOS_INSTITUCION = {"universidad", "estudio", "estudios", "grupo", "banco", "instituto", "ministerio",
                          "colegio", "asociacion", "firma", "abogados", "abogado", "consultora", "consultoria",
                          "auditores", "auditoria", "contadores", "contador", "empresa", "asesoria", "practica",
                          "privada", "publica", "independiente", "particular", "ninguno", "ninguna", "otro", "otros"}


def _nucleos(texto: str) -> list:
    """Núcleo de cada parte del nombre: «Tribunal Fiscal - MEF» da {tribunal, fiscal} y {mef}."""
    salida = []
    for parte in _re.split(r"[()/,;|]| - ", texto or ""):
        n = frozenset(t for t in _clave_alianza(parte).split() if len(t) > 1 and t not in _FORMAS_LEGALES)
        if n and not n <= _GENERICOS_INSTITUCION and n not in salida:
            salida.append(n)
    return salida


def _coinciden(a: frozenset, b: frozenset) -> bool:
    menor, mayor = (a, b) if len(a) <= len(b) else (b, a)
    return menor <= mayor and len(menor) * 2 >= len(mayor)


def _variantes_empresas() -> list:
    salida = []
    for c in companies_col.find({}, {"nombre": 1, "razon_social": 1}):
        variantes = set(_nucleos(c.get("nombre") or "")) | set(_nucleos(c.get("razon_social") or ""))
        if variantes:
            salida.append(variantes)
    return salida


def _socios_con_centro() -> list:
    socios = []
    for m in members_col.find({"estado": {"$ne": "retirar"}, "centro_trabajo": {"$nin": [None, ""]}},
                              {"member_id": 1, "apellidos": 1, "nombres": 1, "centro_trabajo": 1, "linkedin": 1}):
        socios.append({"member_id": m["member_id"], "linkedin": m.get("linkedin") or "",
                       "nombre": f"{m.get('nombres', '')} {m.get('apellidos', '')}".strip(),
                       "nucleos": _nucleos(m["centro_trabajo"])})
    return socios


def _socios_de_institucion(textos: list, socios: list, empresas: list) -> list:
    buscados = set()
    for texto_ in textos:
        for n in _nucleos(texto_):
            buscados.add(n)
            for variantes in empresas:
                if any(_coinciden(n, v) for v in variantes):
                    buscados |= variantes
    return [s for s in socios if any(_coinciden(a, b) for a in s["nucleos"] for b in buscados)]


def _agregar_socios_vinculados(alianzas: list) -> None:
    """Socios cuyo centro de trabajo coincide: son el contacto natural para acercarse a la institución."""
    if not alianzas:
        return
    socios, empresas = _socios_con_centro(), _variantes_empresas()
    for a in alianzas:
        a["socios_vinculados"] = [
            {"member_id": s["member_id"], "nombre": s["nombre"], "linkedin": s["linkedin"]}
            for s in _socios_de_institucion([a.get("nombre", ""), a.get("siglas", "")], socios, empresas)
        ]


def contar_alianzas_por_estado() -> dict:
    return {d["_id"]: d["n"] for d in alianzas_col.aggregate([{"$group": {"_id": "$estado", "n": {"$sum": 1}}}])}


def crear_alianza_manual(nombre: str, tipo: str, ambito: str, pais: str, web: str,
                         por_que_importante: str, por_que_estrategica: str) -> str:
    """Devuelve "" si se creó, o un mensaje si la institución ya está registrada."""
    nombre = nombre.strip()
    clave = _clave_alianza(nombre)
    if not clave:
        return "El nombre es obligatorio."
    web = web.strip()
    if web and not _re.match(r"^https?://", web, _re.I):
        web = "https://" + web
    host = (_urlsplit(web).hostname or "").lower().removeprefix("www.") if web else ""
    if _buscar_alianza_existente(clave, "", host):
        return f"{nombre} ya está registrada."
    ahora = datetime.now(timezone.utc)
    alianzas_col.insert_one({
        "nombre": nombre, "clave": clave, "siglas": "", "tipo": tipo, "ambito": ambito, "pais": pais.strip(),
        "web": web if host else "", "dominio": host, "que_hace": "", "por_que_importante": por_que_importante.strip(),
        "por_que_estrategica": por_que_estrategica.strip(), "como_colaborar": [], "primer_paso": "", "riesgos": "",
        "fuentes": [], "origen": "manual", "estado": "en_evaluacion", "puntaje": 0, "responsable": "", "notas": "",
        "fecha_seguimiento": "", "novedades": [], "created_at": ahora, "updated_at": ahora,
    })
    catalogos.invalidar()
    return ""


def actualizar_seguimiento_alianza(alianza_id: str, estado: str, responsable: str,
                                   fecha_seguimiento: str, notas: str) -> None:
    if estado not in catalogos.ESTADOS_ALIANZA:
        raise ValueError("Estado no válido")
    alianzas_col.update_one({"_id": ObjectId(alianza_id)}, {"$set": {
        "estado": estado, "responsable": responsable.strip(), "fecha_seguimiento": fecha_seguimiento.strip(),
        "notas": notas.strip(), "updated_at": datetime.now(timezone.utc)}})
    catalogos.invalidar()


# ── Ponentes ──────────────────────────────────────────────────────────────────

ponentes_col = _db.ponentes
ponentes_corridas_col = _db.ponentes_corridas
_ESTADOS_PONENTE_CERRADOS = ("descartado", "no_disponible")
_CAMPOS_ANALISIS_PONENTE = ("cargo", "institucion", "pais", "ambito", "temas", "por_que_relevante", "trayectoria",
                            "charlas_posibles", "canal_contacto", "linkedin", "perfil_url",
                            "es_funcionario_publico", "consideraciones", "relevancia", "prestigio", "viabilidad")


def get_corridas_ponentes(limite: int = 8) -> list:
    docs = list(ponentes_corridas_col.find().sort("inicio", -1).limit(limite))
    for d in docs:
        d["_id"] = str(d["_id"])
    return docs


def _hoy_lima() -> str:
    return (datetime.now(timezone.utc) - _timedelta(hours=5)).strftime("%Y-%m-%d")


def get_eventos_proximos_lista(limite: int = 12) -> list:
    docs = eventos_col.find({"fecha": {"$gte": _hoy_lima()}, "estado": {"$ne": "cancelado"}},
                            {"titulo": 1, "fecha": 1}).sort("fecha", 1).limit(limite)
    return [{"_id": str(d["_id"]), "titulo": d.get("titulo", ""), "fecha": d.get("fecha", "")} for d in docs]


def _asistencia_eventos(match: dict) -> list:
    registrantes = {"$ifNull": ["$zoom_registrants", []]}
    return list(eventos_col.aggregate([
        {"$match": match},
        {"$project": {"titulo": 1, "fecha": 1, "expositores.ponente_id": 1,
                      "inscritos": {"$size": registrantes},
                      "asistentes": {"$size": {"$filter": {"input": registrantes,
                                                            "cond": {"$eq": ["$$this.asistio", True]}}}}}},
    ]))


def get_contexto_ponentes() -> dict:
    """Lo que Claude necesita saber para buscar. No incluye datos de socios."""
    asistencia = [e for e in _asistencia_eventos({"zoom_registrants.0": {"$exists": True}}) if e["inscritos"]]
    asistencia.sort(key=lambda e: e["asistentes"] / e["inscritos"], reverse=True)
    buscados = list(ponentes_col.find({"origen": "busqueda"}, {"nombre": 1, "estado": 1}))
    return {
        "proximos": get_eventos_proximos_lista(8),
        "asistencia": asistencia[:10],
        "instituciones": [a["nombre"] for a in alianzas_col.find({"estado": {"$ne": "descartada"}}, {"nombre": 1})],
        "registrados": [p["nombre"] for p in buscados if p.get("estado") not in _ESTADOS_PONENTE_CERRADOS],
        "descartados": [p["nombre"] for p in buscados if p.get("estado") in _ESTADOS_PONENTE_CERRADOS],
    }


def guardar_propuesta_ponente(doc: dict, corrida_id: str, evento: dict | None = None) -> str:
    """Devuelve "nuevo", "actualizado" o "ignorado". Nunca pisa etapa, responsable ni notas."""
    ahora = datetime.now(timezone.utc)
    clave = _clave_alianza(doc["nombre"])
    novedad = doc.pop("novedad", "")
    relacion = doc.pop("relacion_evento", "")
    sugerencia = ({"evento_id": evento["_id"], "evento_titulo": evento.get("titulo", ""), "motivo": relacion,
                   "fecha": ahora} if evento else None)
    puntaje = doc["relevancia"] + doc["prestigio"] + doc["viabilidad"]
    previa = ponentes_col.find_one({"clave": clave})
    if not previa:
        try:
            ponentes_col.insert_one({**doc, "clave": clave, "origen": "busqueda", "estado": "nuevo",
                                     "puntaje": puntaje, "responsable": "", "notas": "", "fecha_seguimiento": "",
                                     "novedades": [], "sugerencias": [sugerencia] if sugerencia else [],
                                     "corrida_id": corrida_id, "created_at": ahora, "updated_at": ahora})
        except DuplicateKeyError:
            return "ignorado"
        return "nuevo"
    if previa.get("estado") in _ESTADOS_PONENTE_CERRADOS:
        return "ignorado"

    cambios: dict = {}
    if previa.get("estado") == "nuevo":
        cambios = {c: doc[c] for c in _CAMPOS_ANALISIS_PONENTE}
        cambios["puntaje"] = puntaje
    else:
        # Una ficha que vino de un evento o se cargó a mano: solo se completa lo que falta.
        cambios = {c: doc[c] for c in _CAMPOS_ANALISIS_PONENTE if doc.get(c) and not previa.get(c)}
        if cambios and not previa.get("puntaje"):
            cambios["puntaje"] = puntaje
    push: dict = {}
    if sugerencia and not any(s.get("evento_id") == sugerencia["evento_id"] for s in previa.get("sugerencias", [])):
        push["sugerencias"] = sugerencia
    if novedad:
        push["novedades"] = {"$each": [{"fecha": ahora, "texto": novedad, "corrida_id": corrida_id}], "$position": 0}
    if not cambios and not push:
        return "ignorado"
    fuentes = {f["url"]: f for f in previa.get("fuentes", []) + doc.get("fuentes", [])}
    ops: dict = {"$set": {**cambios, "fuentes": list(fuentes.values())[:15], "updated_at": ahora}}
    if push:
        ops["$push"] = push
    ponentes_col.update_one({"_id": previa["_id"]}, ops)
    return "actualizado"


def _tokens(texto: str) -> set:
    return {t for t in _clave_alianza(texto).split() if len(t) > 2}


def _enriquecer_ponentes(ponentes: list) -> None:
    """Eventos en los que expuso, socios con el mismo nombre y socios de su misma institución."""
    if not ponentes:
        return
    ids = [p["_id"] for p in ponentes]
    expuso: dict = {}
    for e in _asistencia_eventos({"expositores.ponente_id": {"$in": ids}}):
        for x in e.get("expositores", []):
            if x.get("ponente_id") in ids:
                expuso.setdefault(x["ponente_id"], []).append(
                    {"evento_id": str(e["_id"]), "titulo": e.get("titulo", ""), "fecha": e.get("fecha", ""),
                     "inscritos": e["inscritos"], "asistentes": e["asistentes"]})
    socios = [{"member_id": m["member_id"], "nombre": f"{m.get('nombres', '')} {m.get('apellidos', '')}".strip(),
               "tokens": _tokens(f"{m.get('nombres', '')} {m.get('apellidos', '')}")}
              for m in members_col.find({"estado": {"$ne": "retirar"}}, {"member_id": 1, "apellidos": 1, "nombres": 1})]
    con_centro, empresas = _socios_con_centro(), _variantes_empresas()
    for p in ponentes:
        p["eventos"] = sorted(expuso.get(p["_id"], []), key=lambda e: e["fecha"], reverse=True)
        nombre = _tokens(p.get("nombre", ""))
        p["posible_socio"] = next(({"member_id": s["member_id"], "nombre": s["nombre"]} for s in socios
                                   if len(nombre) >= 2 and (nombre <= s["tokens"] or len(nombre & s["tokens"]) >= 3)), None)
        mismo = p["posible_socio"]["member_id"] if p["posible_socio"] else ""
        p["colegas"] = [{"member_id": s["member_id"], "nombre": s["nombre"]}
                        for s in _socios_de_institucion([p.get("institucion", "")], con_centro, empresas)
                        if s["member_id"] != mismo][:6]


def get_ponentes(search: str = "", estado: str = "", ambito: str = "", evento: str = "", origen: str = "",
                 orden: str = "puntaje") -> list:
    query: dict = {}
    if search.strip():
        rx = {"$regex": _re.escape(search.strip()), "$options": "i"}
        query["$or"] = [{"nombre": rx}, {"cargo": rx}, {"institucion": rx}, {"pais": rx}, {"temas": rx},
                        {"por_que_relevante": rx}, {"trayectoria": rx}]
    if ambito:
        query["ambito"] = ambito
    if evento:
        query["sugerencias.evento_id"] = evento
    if origen:
        query["origen"] = origen
    query["estado"] = estado if estado else {"$ne": "descartado"}
    campo = "created_at" if orden == "recientes" else "puntaje"
    docs = list(ponentes_col.find(query).sort([(campo, -1), ("nombre", 1)]))
    for d in docs:
        d["_id"] = str(d["_id"])
    _enriquecer_ponentes(docs)
    return docs


def contar_ponentes_por_estado() -> dict:
    return {d["_id"]: d["n"] for d in ponentes_col.aggregate([{"$group": {"_id": "$estado", "n": {"$sum": 1}}}])}


def contar_sugeridos_evento(evento_id: str) -> int:
    return ponentes_col.count_documents({"sugerencias.evento_id": evento_id, "estado": {"$ne": "descartado"}})


def registrar_ponente_desde_evento(nombre: str, cargo: str = "", institucion: str = "", linkedin: str = "",
                                   bio: str = "") -> str:
    """El expositor de un evento entra al directorio; si ya estaba, se reutiliza su ficha."""
    clave = _clave_alianza(nombre)
    if not clave:
        return ""
    previa = ponentes_col.find_one({"clave": clave}, {"_id": 1})
    if previa:
        return str(previa["_id"])
    try:
        linkedin = normalizar_linkedin(linkedin)
    except ValueError:
        linkedin = ""
    ahora = datetime.now(timezone.utc)
    try:
        return str(ponentes_col.insert_one({
            "nombre": nombre.strip(), "clave": clave, "cargo": cargo.strip(), "institucion": institucion.strip(),
            "pais": "", "ambito": "nacional", "temas": [], "por_que_relevante": "", "trayectoria": bio.strip(),
            "charlas_posibles": [], "canal_contacto": "", "linkedin": linkedin, "perfil_url": "",
            "es_funcionario_publico": False, "consideraciones": "", "relevancia": 0, "prestigio": 0, "viabilidad": 0,
            "puntaje": 0, "fuentes": [], "origen": "evento", "estado": "invitado", "responsable": "", "notas": "",
            "fecha_seguimiento": "", "novedades": [], "sugerencias": [], "created_at": ahora, "updated_at": ahora,
        }).inserted_id)
    except DuplicateKeyError:
        return str(ponentes_col.find_one({"clave": clave}, {"_id": 1})["_id"])


def crear_ponente_manual(nombre: str, cargo: str, institucion: str, pais: str, ambito: str, linkedin: str,
                         temas: str, por_que_relevante: str) -> str:
    """Devuelve "" si se creó, o el mensaje de error."""
    clave = _clave_alianza(nombre)
    if not clave:
        return "El nombre es obligatorio."
    if ponentes_col.find_one({"clave": clave}, {"_id": 1}):
        return f"{nombre.strip()} ya está en el directorio."
    try:
        linkedin = normalizar_linkedin(linkedin)
    except ValueError as exc:
        return str(exc)
    ahora = datetime.now(timezone.utc)
    ponentes_col.insert_one({
        "nombre": nombre.strip(), "clave": clave, "cargo": cargo.strip(), "institucion": institucion.strip(),
        "pais": pais.strip(), "ambito": ambito if ambito in catalogos.AMBITOS_ALIANZA else "nacional",
        "temas": [t.strip() for t in temas.split(",") if t.strip()][:8], "por_que_relevante": por_que_relevante.strip(),
        "trayectoria": "", "charlas_posibles": [], "canal_contacto": "", "linkedin": linkedin, "perfil_url": "",
        "es_funcionario_publico": False, "consideraciones": "", "relevancia": 0, "prestigio": 0, "viabilidad": 0,
        "puntaje": 0, "fuentes": [], "origen": "manual", "estado": "en_evaluacion", "responsable": "", "notas": "",
        "fecha_seguimiento": "", "novedades": [], "sugerencias": [], "created_at": ahora, "updated_at": ahora,
    })
    catalogos.invalidar()
    return ""


def actualizar_seguimiento_ponente(ponente_id: str, estado: str, responsable: str,
                                   fecha_seguimiento: str, notas: str) -> None:
    if estado not in catalogos.ESTADOS_PONENTE:
        raise ValueError("Estado no válido")
    ponentes_col.update_one({"_id": ObjectId(ponente_id)}, {"$set": {
        "estado": estado, "responsable": responsable.strip(), "fecha_seguimiento": fecha_seguimiento.strip(),
        "notas": notas.strip(), "updated_at": datetime.now(timezone.utc)}})
    catalogos.invalidar()


def agregar_ponente_a_evento(ponente_id: str, evento_id: str) -> str:
    """Lo suma como expositor por confirmar. Devuelve "" o el motivo por el que no se agregó."""
    p = ponentes_col.find_one({"_id": ObjectId(ponente_id)})
    evento = eventos_col.find_one({"_id": ObjectId(evento_id)}, {"expositores": 1, "titulo": 1})
    if not p or not evento:
        return "No se encontró el ponente o el evento."
    if any(x.get("ponente_id") == ponente_id for x in evento.get("expositores", [])):
        return f"{p['nombre']} ya está en «{evento.get('titulo', '')}»."
    add_expositor(evento_id, p["nombre"], p.get("cargo", ""), p.get("institucion", ""), p.get("linkedin", ""),
                  "", ponente_id=ponente_id)
    return ""

def confirmar_ponente_de_expositor(evento_id: str, numero: int) -> None:
    """Confirmar al expositor en el evento confirma también su ficha, salvo que ya haya expuesto."""
    evento = eventos_col.find_one({"_id": ObjectId(evento_id)}, {"expositores": 1}) or {}
    ponente_id = next((x.get("ponente_id") for x in evento.get("expositores", []) if x.get("numero") == numero), "")
    if ponente_id:
        ponentes_col.update_one({"_id": ObjectId(ponente_id), "estado": {"$in": ["nuevo", "en_evaluacion", "invitado"]}},
                                {"$set": {"estado": "confirmado", "updated_at": datetime.now(timezone.utc)}})
        catalogos.invalidar()
