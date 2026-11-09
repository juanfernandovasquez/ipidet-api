"""
Datos del agente de correos: la cola (colección `correos`), su estado y el contexto
de cada remitente.

Un documento por correo recibido. El ciclo de vida está en catalogos.ESTADOS_CORREO;
los cambios de estado pasan siempre por las funciones de este módulo, que registran
quién hizo qué en `historial`.
"""
import re
import unicodedata
from datetime import datetime, timedelta, timezone

from bson import ObjectId
from bson.codec_options import CodecOptions
from bson.errors import InvalidId
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from config import catalogos
from config.mongo_client import db

# Fechas con zona (UTC) al leer: se comparan con las de Gmail y se muestran en hora de Lima.
_UTC = CodecOptions(tz_aware=True, tzinfo=timezone.utc)
correos_col = db.get_collection("correos", codec_options=_UTC)
_config = db.get_collection("config", codec_options=_UTC)
_blocked = db.blocked_senders

_INDICES_LISTOS = False


def asegurar_indices() -> None:
    global _INDICES_LISTOS
    if _INDICES_LISTOS:
        return
    correos_col.create_index("clave", unique=True)
    correos_col.create_index([("estado", ASCENDING), ("fecha", DESCENDING)])
    correos_col.create_index([("de_email", ASCENDING), ("fecha", DESCENDING)])
    correos_col.create_index("gm_thrid")
    correos_col.create_index("telegram_msg_id", sparse=True)
    _INDICES_LISTOS = True


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _oid(correo_id: str) -> ObjectId | None:
    try:
        return ObjectId(correo_id)
    except (InvalidId, TypeError):
        return None


def _evento_historial(accion: str, usuario: str = "agente", detalle: str = "") -> dict:
    return {"fecha": _ahora(), "accion": accion, "usuario": usuario, "detalle": detalle[:500]}


# ── Estado del agente (último UID leído, candado del ciclo) ───────────────────

def get_estado() -> dict:
    return _config.find_one({"_id": "agente_correo_estado"}) or {}


def set_estado(**campos) -> None:
    _config.update_one({"_id": "agente_correo_estado"}, {"$set": campos}, upsert=True)


def reservar_ciclo(minutos: int = 20) -> bool:
    """Candado atómico: solo un proceso (web o local) revisa el correo a la vez.

    Caduca solo, por si un reinicio deja el ciclo a medias.
    """
    ahora = _ahora()
    try:
        _config.update_one(
            {"_id": "agente_correo_estado",
             "$or": [{"en_curso_hasta": {"$exists": False}}, {"en_curso_hasta": None},
                     {"en_curso_hasta": {"$lt": ahora}}]},
            {"$set": {"en_curso_hasta": ahora + timedelta(minutes=minutos), "inicio_ciclo": ahora}},
            upsert=True,
        )
        return True
    except DuplicateKeyError:
        # El documento existe y el candado está tomado: el upsert intentó crear otro con el mismo _id.
        return False


def liberar_ciclo(**campos) -> None:
    set_estado(en_curso_hasta=None, **campos)


# ── Cola ───────────────────────────────────────────────────────────────────────

def clave_correo(msg: dict) -> str:
    return msg.get("gm_msgid") or msg.get("message_id") or f"uid:{msg.get('uid')}"


def registrar_recibido(msg: dict) -> str | None:
    """Inserta el correo en la cola. Devuelve el id, o None si ya estaba (otro ciclo lo tomó)."""
    asegurar_indices()
    doc = {
        "clave": clave_correo(msg),
        "gm_msgid": msg.get("gm_msgid", ""),
        "gm_thrid": msg.get("gm_thrid", ""),
        "uid": msg.get("uid"),
        "message_id": msg.get("message_id", ""),
        "references": msg.get("references", ""),
        "de": msg.get("de", ""),
        "de_nombre": msg.get("de_nombre", ""),
        "de_email": msg.get("de_email", ""),
        "reply_to": msg.get("reply_to", ""),
        "para": msg.get("para", []),
        "cc": msg.get("cc", []),
        "asunto": msg.get("asunto", ""),
        "fecha": msg.get("fecha") or _ahora(),
        "cuerpo": (msg.get("cuerpo") or "")[:20000],
        "adjuntos": [{"n": i, "nombre": a["nombre"], "tipo": a["tipo"], "tamano": a["tamano"]}
                     for i, a in enumerate(msg.get("adjuntos", []))],
        "estado": "procesando",
        "intentos": 0,
        "historial": [_evento_historial("recibido")],
        "created_at": _ahora(),
        "updated_at": _ahora(),
    }
    try:
        return str(correos_col.insert_one(doc).inserted_id)
    except DuplicateKeyError:
        return None


def guardar_analisis(correo_id: str, campos: dict, estado: str, detalle: str = "") -> None:
    correos_col.update_one(
        {"_id": _oid(correo_id)},
        {"$set": {**campos, "estado": estado, "updated_at": _ahora()},
         "$push": {"historial": _evento_historial(f"analizado → {estado}", detalle=detalle)}},
    )
    catalogos.invalidar()


def marcar_error(correo_id: str, error: str, contar_intento: bool = True) -> None:
    correos_col.update_one(
        {"_id": _oid(correo_id)},
        {"$set": {"estado": "error", "error": error[:1000], "updated_at": _ahora()},
         "$inc": {"intentos": 1 if contar_intento else 0},
         "$push": {"historial": _evento_historial("error", detalle=error)}},
    )
    catalogos.invalidar()


def para_reintentar(max_intentos: int = 3) -> list[dict]:
    """Correos cuyo análisis falló (API caída, saldo agotado) y aún tienen reintentos."""
    return list(correos_col.find({"estado": "error", "intentos": {"$lt": max_intentos},
                                  "analizado_at": {"$exists": False}}).limit(10))


def get_correo(correo_id: str) -> dict | None:
    oid = _oid(correo_id)
    return correos_col.find_one({"_id": oid}) if oid else None


def get_correo_por_telegram(msg_id: int) -> dict | None:
    return correos_col.find_one({"telegram_msg_id": msg_id})


def actualizar(correo_id: str, campos: dict, accion: str = "", usuario: str = "", detalle: str = "") -> None:
    update: dict = {"$set": {**campos, "updated_at": _ahora()}}
    if accion:
        update["$push"] = {"historial": _evento_historial(accion, usuario or "agente", detalle)}
    correos_col.update_one({"_id": _oid(correo_id)}, update)
    catalogos.invalidar()


def reclamar_para_envio(correo_id: str, usuario: str) -> dict | None:
    """Pasa a «enviando» de forma atómica: dos clics (web y Telegram) no envían dos veces."""
    doc = correos_col.find_one_and_update(
        {"_id": _oid(correo_id), "estado": {"$in": ["pendiente", "error"]}},
        {"$set": {"estado": "enviando", "enviando_desde": _ahora(), "enviando_por": usuario}},
        return_document=ReturnDocument.AFTER,
    )
    return doc


def liberar_envios_trabados(minutos: int = 5) -> int:
    r = correos_col.update_many(
        {"estado": "enviando", "enviando_desde": {"$lt": _ahora() - timedelta(minutes=minutos)}},
        {"$set": {"estado": "pendiente", "error": "El envío quedó interrumpido; revisa si salió antes de reintentar."}},
    )
    return r.modified_count


def liberar_analisis_trabados(minutos: int = 15) -> int:
    """Un reinicio a mitad de análisis deja el correo en «procesando»: pasa a error para reintentarlo."""
    r = correos_col.update_many(
        {"estado": "procesando", "updated_at": {"$lt": _ahora() - timedelta(minutes=minutos)}},
        {"$set": {"estado": "error", "error": "El análisis se interrumpió; se reintentará."}},
    )
    return r.modified_count


def pendientes_para_verificar(limite: int = 20) -> list[dict]:
    """Pendientes con hilo de Gmail, para detectar los que el equipo ya respondió desde Gmail."""
    return list(correos_col.find({"estado": "pendiente", "gm_thrid": {"$ne": ""}},
                                 {"gm_thrid": 1, "fecha": 1}).sort("fecha", DESCENDING).limit(limite))


def auto_enviados_recientes(de_email: str, horas: int = 24) -> int:
    return correos_col.count_documents({
        "de_email": de_email, "estado": "enviado_auto",
        "enviado_at": {"$gte": _ahora() - timedelta(hours=horas)},
    })


def auto_enviados_hoy() -> int:
    """Respuestas automáticas de las últimas 24 horas (tope diario de la configuración)."""
    return correos_col.count_documents({"estado": "enviado_auto",
                                        "enviado_at": {"$gte": _ahora() - timedelta(hours=24)}})


def contar_uso_faqs(fuentes: list[str]) -> None:
    ids = [_oid(f.split(":", 1)[1]) for f in fuentes if f.startswith("faq:")]
    ids = [i for i in ids if i]
    if ids:
        db.faqs.update_many({"_id": {"$in": ids}}, {"$inc": {"times_used": 1}, "$set": {"last_used": _ahora()}})


def listar(vista: str = "atender", categoria: str = "", search: str = "",
           page: int = 1, per_page: int = 25) -> tuple[list, int]:
    q: dict = {}
    if vista == "atender":
        q["estado"] = {"$in": list(catalogos.ESTADOS_CORREO_ABIERTOS)}
    elif vista == "respondidos":
        q["estado"] = {"$in": ["enviado", "enviado_auto", "respondido_gmail", "reenviado"]}
    elif vista == "sin_respuesta":
        q["estado"] = {"$in": ["ignorado", "descartado"]}
    if categoria:
        q["categoria"] = categoria
    if search:
        patron = {"$regex": re.escape(search.strip()), "$options": "i"}
        q["$or"] = [{"asunto": patron}, {"de": patron}, {"cuerpo": patron}, {"resumen": patron}]
    total = correos_col.count_documents(q)
    orden = [("prioridad_orden", ASCENDING), ("fecha", DESCENDING)] if vista == "atender" else [("fecha", DESCENDING)]
    docs = list(correos_col.find(q, {"cuerpo_original": 0}).sort(orden)
                .skip((max(page, 1) - 1) * per_page).limit(per_page))
    return docs, total


def contar_por_vista() -> dict:
    conteo = {d["_id"]: d["n"] for d in correos_col.aggregate([{"$group": {"_id": "$estado", "n": {"$sum": 1}}}])}
    abiertos = sum(conteo.get(e, 0) for e in catalogos.ESTADOS_CORREO_ABIERTOS)
    respondidos = sum(conteo.get(e, 0) for e in ("enviado", "enviado_auto", "respondido_gmail", "reenviado"))
    return {"atender": abiertos, "respondidos": respondidos,
            "sin_respuesta": conteo.get("ignorado", 0) + conteo.get("descartado", 0),
            "todos": sum(conteo.values())}


def resumen_uso(dias: int = 30) -> dict:
    desde = _ahora() - timedelta(days=dias)
    fila = next(correos_col.aggregate([
        {"$match": {"analizado_at": {"$gte": desde}, "uso.costo_usd": {"$exists": True}}},
        {"$group": {"_id": None, "costo": {"$sum": "$uso.costo_usd"}, "n": {"$sum": 1}}},
    ]), None) or {}
    return {"costo_usd": round(fila.get("costo", 0.0), 2), "analizados": fila.get("n", 0), "dias": dias}


def historial_remitente(de_email: str, excluir_id=None, limite: int = 3) -> list[dict]:
    q = {"de_email": de_email}
    if excluir_id:
        q["_id"] = {"$ne": excluir_id}
    return list(correos_col.find(q, {"asunto": 1, "fecha": 1, "resumen": 1, "estado": 1, "respuesta_enviada": 1})
                .sort("fecha", DESCENDING).limit(limite))


# ── Remitentes bloqueados (mismo listado que /bloquear de Telegram) ───────────

def bloquear_remitente(patron: str) -> None:
    patron = patron.strip().lower()
    if patron:
        _blocked.update_one({"pattern": patron}, {"$set": {"pattern": patron, "added_at": _ahora()}}, upsert=True)


def patrones_bloqueados() -> list[str]:
    return [d["pattern"] for d in _blocked.find({}, {"pattern": 1})]


def coincide(email: str, patrones: list[str]) -> bool:
    email = (email or "").lower()
    dominio = email.split("@")[-1]
    for p in patrones:
        p = (p or "").strip().lower()
        if not p:
            continue
        if p.startswith("@") and dominio == p[1:]:
            return True
        if "@" not in p and (dominio == p or dominio.endswith("." + p)):
            return True
        if email == p:
            return True
    return False


# ── Contexto del remitente ─────────────────────────────────────────────────────

def _sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto or "") if unicodedata.category(c) != "Mn").lower()


def buscar_socio_por_email(email: str) -> dict | None:
    if not email:
        return None
    return db.members.find_one({"emails": {"$elemMatch": {
        "email": {"$regex": f"^{re.escape(email.strip())}$", "$options": "i"}}}})


def buscar_socio_por_nombre(nombre: str) -> dict | None:
    """Coincidencia exacta de todas las palabras del nombre visible con apellidos + nombres.

    Solo sirve como pista para el equipo: nunca se toma como identidad verificada.
    """
    palabras = [p for p in re.split(r"[\s,]+", _sin_tildes(nombre)) if len(p) > 2]
    if len(palabras) < 2:
        return None
    patron = {"$regex": re.escape(palabras[0]), "$options": "i"}
    candidatos = db.members.find({"$or": [{"apellidos": patron}, {"nombres": patron}]},
                                 {"member_id": 1, "apellidos": 1, "nombres": 1}).limit(30)
    encontrados = []
    for c in candidatos:
        completo = _sin_tildes(f"{c.get('apellidos', '')} {c.get('nombres', '')}")
        if all(p in completo for p in palabras):
            encontrados.append(c)
    return encontrados[0] if len(encontrados) == 1 else None


def resumen_socio(socio: dict) -> dict:
    """Lo que el agente necesita saber del socio para responderle."""
    member_id = socio["member_id"]
    periodos_cobro = set(catalogos.get_periodos_cobro())
    pagos = []
    for p in db.payments.find({"member_id": member_id}).sort("periodo", DESCENDING).limit(4):
        cuotas = p.get("cuotas") or []
        pagos.append({
            "periodo": p.get("periodo"),
            "estado": catalogos.ESTADOS_PAGO.get(p.get("estado"), (p.get("estado"),))[0],
            "periodo_en_cobro": p.get("periodo") in periodos_cobro,
            "empresa_pagadora": p.get("empresa_pagadora") or "",
            "fecha_pago": p.get("fecha_pago") or "",
            "comprobante": p.get("num_comprobante") or "",
            "cuotas_pendientes": [
                {"numero": c.get("numero"), "monto": c.get("monto"), "vence": c.get("fecha_venc")}
                for c in cuotas if c.get("estado") != "pagado"
            ],
            "monto_pendiente_parcial": max(0.0, float(p.get("monto_total") or 0)
                                           - sum(float(x.get("monto") or 0) for x in p.get("pagos_parciales") or []))
            if p.get("monto_total") else None,
        })
    hoy = (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%Y-%m-%d")
    inscrito = [
        {"evento": e.get("titulo"), "fecha": e.get("fecha")}
        for e in db.eventos_ipidet.find({"inscritos.member_id": member_id, "fecha": {"$gte": hoy}},
                                        {"titulo": 1, "fecha": 1}).limit(5)
    ]
    tipos = {t["valor"]: t["label"] for t in catalogos.get_catalogo("tipos_socio")}
    return {
        "member_id": member_id,
        "nombre": f"{socio.get('nombres', '')} {socio.get('apellidos', '')}".strip(),
        "titulo": socio.get("titulo") or "",
        "tipo_socio": tipos.get(socio.get("tipo_socio"), socio.get("tipo_socio") or ""),
        "estado_socio": socio.get("estado") or "",
        "fecha_ingreso": str(socio.get("fecha_ingreso") or "")[:10],
        "ubicacion": socio.get("ubicacion") or "",
        "pagos": pagos,
        "inscrito_en_proximos_eventos": inscrito,
    }


def buscar_prospecto(email: str) -> dict | None:
    if not email:
        return None
    p = db.publico.find_one({"email": {"$regex": f"^{re.escape(email.strip())}$", "$options": "i"}})
    if not p:
        return None
    return {
        "prospecto_id": str(p["_id"]),
        "nombre": f"{p.get('nombre', '')} {p.get('apellido', '')}".strip(),
        "estado": p.get("estado") or "",
        "empresa": p.get("empresa") or "",
        "es_socio": bool(p.get("es_socio")),
        "eventos": [
            {"evento": z.get("evento_titulo"), "fecha": z.get("evento_fecha"), "asistio": z.get("asistio")}
            for z in (p.get("zoom_eventos") or [])[-5:]
        ],
    }
