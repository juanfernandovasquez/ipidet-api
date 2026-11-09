"""
Fuente única de catálogos y configuración editable de la plataforma.

Todo lo que el negocio puede cambiar (períodos, bancos, medios de pago, tipos de
socio, datos de contacto, firmante...) vive en MongoDB y se lee desde aquí. Ni el
código ni los templates deben repetir esos valores — ver regla R10 en CLAUDE.md.

Este módulo solo depende de config.mongo_client para que lo compartan la web, el
agente de email y el scheduler sin dependencias circulares ni efectos al importar.

Colección `config`:
  {_id: "<catálogo>", valores: [...]}
  {_id: "organizacion", email, web, telefono, ciudad, firmante_nombre, firmante_cargo}
  {_id: "periodo_actual", valor: "2026"}   # opcional; sin él se deriva de los datos
"""
import re
import time
from datetime import datetime, timedelta, timezone

from config.mongo_client import db
from config.settings import BREVO_FROM_EMAIL, WC_STORE_URL

_config = db.config

# Máquina de estados de payments.estado (sección 7.2 de CLAUDE.md). Es lógica del
# sistema, no un dato editable: por eso vive en código, pero solo aquí.
ESTADOS_PAGO: dict[str, tuple[str, str]] = {
    "pagado":          ("Pagado",          "green"),
    "debe":            ("Debe",            "red"),
    "fraccionamiento": ("Fraccionamiento", "amber"),
    "parcial":         ("Parcial",         "blue"),
    "exonerado":       ("Exonerado",       "gray"),
    "no_aplica":       ("N/A",             "gray"),
    "pendiente":       ("Pendiente",       "yellow"),
    "retirar":         ("Retirar",         "slate"),
    "en_revision":     ("En revisión",     "purple"),
    "revisar":         ("Revisar",         "orange"),
    "por_cobrar":      ("Por cobrar",      "orange"),
}

# Tipos de producto → qué cuota representan. También es lógica, no dato.
TIPOS_CUOTA = ("cuota_anual", "cuota_provincia")

# Definición de los catálogos que se editan en /configuracion.
#   texto      → lista de strings
#   valor_label → lista de {valor, label}; `valor` se guarda en los documentos
#   id_label    → lista de {id, label}; `id` es la clave del progreso guardado
CATALOGOS: dict[str, dict] = {
    "medios_pago":          {"titulo": "Medios de pago",           "formato": "texto",
                             "ayuda": "Opciones al registrar un pago. También se listan en los recordatorios de cuota."},
    "bancos":               {"titulo": "Bancos",                   "formato": "texto",
                             "ayuda": "Banco de origen al registrar un pago."},
    "tipos_socio":          {"titulo": "Tipos de socio",           "formato": "valor_label",
                             "ayuda": "El tipo «filial» decide el botón de cuota de provincia en el portal de socios."},
    "tipos_empresa":        {"titulo": "Tipos de empresa",         "formato": "valor_label",
                             "ayuda": "Clasificación de las empresas pagadoras."},
    "checklist_evento_pre": {"titulo": "Checklist antes del evento",   "formato": "id_label",
                             "ayuda": "Tareas que se marcan en el detalle de cada evento."},
    "checklist_evento_post": {"titulo": "Checklist después del evento", "formato": "id_label",
                              "ayuda": "Tareas que se marcan en el detalle de cada evento."},
    "tipos_institucion":    {"titulo": "Tipos de institución",     "formato": "valor_label",
                             "ayuda": "Clasificación de las instituciones en Alianzas. La búsqueda semanal solo usa estos tipos."},
}

# Etapas del seguimiento de una alianza: es lógica del sistema, no un dato editable.
ESTADOS_ALIANZA: dict[str, tuple[str, str]] = {
    "nueva":          ("Por revisar",    "blue"),
    "en_evaluacion":  ("En evaluación",  "amber"),
    "contactada":     ("Contactada",     "purple"),
    "en_negociacion": ("En negociación", "orange"),
    "aliada":         ("Aliada",         "green"),
    "descartada":     ("Descartada",     "gray"),
}
AMBITOS_ALIANZA = {"nacional": "Nacional", "regional": "América Latina", "internacional": "Internacional"}

# Etapas de un posible ponente.
ESTADOS_PONENTE: dict[str, tuple[str, str]] = {
    "nuevo":         ("Por revisar",   "blue"),
    "en_evaluacion": ("En evaluación", "amber"),
    "invitado":      ("Invitado",      "purple"),
    "confirmado":    ("Confirmado",    "green"),
    "expuso":        ("Ya expuso",     "slate"),
    "no_disponible": ("No disponible", "orange"),
    "descartado":    ("Descartado",    "gray"),
}
ORIGENES_PONENTE = {"busqueda": "Búsqueda con IA", "evento": "Expositor de un evento", "manual": "Agregado a mano"}

# Valores si la colección aún no los tiene; se editan en /alianzas y /ponentes.
_ALIANZAS_DEFECTO = {"propuestas_por_corrida": 5, "perfil": "", "criterios": ""}
_PONENTES_DEFECTO = {"propuestas_por_corrida": 5, "publico": "", "temas": ""}

# ── Agente de correos (/inbox) ─────────────────────────────────────────────────
# Categorías y estados son lógica del agente: el prompt, los filtros y la regla de
# envío automático dependen de ellos. Lo editable (tono, firma, información de pago,
# remitentes internos, modo) vive en config.agente_correo_config.
CATEGORIAS_CORREO: dict[str, tuple[str, str, str]] = {
    # clave: (etiqueta, color de badge, cuándo aplica — se le explica así a Claude)
    "consulta_socio":  ("Socio",          "blue",   "Un socio pregunta por su cuota, estado de cuenta, constancia, comprobante, sus datos o los de otro socio"),
    "pago":            ("Pago",           "amber",  "Envía o menciona un comprobante, voucher o constancia de pago/transferencia"),
    "evento":          ("Evento",         "green",  "Consulta o inscripción a un evento, conversatorio, foro, grabación o certificado de asistencia"),
    "admision":        ("Admisión",       "purple", "Quiere asociarse: requisitos, procedimiento, documentos y cuotas de ingreso (no el estado de una postulación concreta: eso es consulta_socio)"),
    "consulta_general": ("Consulta",      "slate",  "Otra consulta del público que IPIDET puede responder"),
    "institucional":   ("Institucional",  "orange", "Otra institución, universidad o prensa: invitaciones, convenios, propuestas"),
    "proveedor":       ("Proveedor",      "gray",   "Facturas, cobros o avisos de proveedores y plataformas que usa IPIDET"),
    "interno":         ("Interno",        "gray",   "Conversación entre directivos o el equipo de IPIDET"),
    "critico":         ("Crítico",        "red",    "Queja grave, tema legal, reclamo formal o urgencia"),
    "automatico":      ("Automático",     "gray",   "Notificación, boletín, publicidad o código de acceso: no espera respuesta"),
}
# Ciclo de vida de un correo en la cola.
ESTADOS_CORREO: dict[str, tuple[str, str]] = {
    "procesando":   ("Analizando",            "blue"),
    "pendiente":    ("Por atender",           "amber"),
    "enviando":     ("Enviando",              "blue"),
    "enviado":      ("Respondido",            "green"),
    "enviado_auto": ("Respondido por el agente", "green"),
    "respondido_gmail": ("Respondido en Gmail", "green"),
    "reenviado":    ("Reenviado",             "purple"),
    "descartado":   ("Descartado",            "gray"),
    "ignorado":     ("Sin respuesta necesaria", "gray"),
    "error":        ("Error",                 "red"),
}
ESTADOS_CORREO_ABIERTOS = ("procesando", "pendiente", "enviando", "error")
MODOS_AGENTE = {
    "borrador":   "Solo borradores: el equipo revisa y envía cada respuesta",
    "automatico": "Automático: envía solo lo que cumple todas las condiciones; el resto queda en borrador",
}
ESFUERZOS_AGENTE = {"low": "Bajo (más rápido y barato)", "medium": "Medio (recomendado)", "high": "Alto (más cuidadoso)"}
# Categorías en las que una respuesta puede salir sola (si además se apoya solo en FAQs
# o eventos autorizados). El resto —consultas generales, pagos, socios, proveedores, quejas—
# siempre va a revisión del equipo.
CATEGORIAS_CORREO_AUTO = ("evento", "admision")
# Por defecto, estas categorías se responden desde la segunda cuenta (administración); el resto,
# desde la principal. Se cambia en /inbox/configuracion (`categorias_cuenta_2`) y en cada correo.
CATEGORIAS_CUENTA_2_DEFECTO = ["consulta_socio", "pago", "proveedor", "critico"]

_AGENTE_DEFECTO = {
    "activo": False,
    "modo": "borrador",
    "intervalo_min": 20,
    "max_por_ciclo": 15,
    # Haiku: clasificar y responder con FAQs es una tarea simple; cuesta ~40 veces menos que Opus.
    "modelo": "claude-haiku-5-5",
    "esfuerzo": "medium",
    "umbral_auto": 0.9,
    "max_auto_dia": 30,
    "max_auto_por_remitente_dia": 2,
    "firma": "Saludos cordiales,\nEquipo IPIDET",
    "estilo": (
        "Profesional y cercano. Escribe en primera persona del plural (nosotros). "
        "Saluda por el nombre cuando lo conozcas. Responde todas las preguntas del correo. "
        "Evita clichés como «a la brevedad posible» y no prometas nada que no esté confirmado."
    ),
    "informacion_adicional": "",
    "remitentes_internos": [],
    "remitentes_ignorar": [],
    "remitentes_permitidos": [],
    "categorias_cuenta_2": CATEGORIAS_CUENTA_2_DEFECTO,
    # USD por millón de tokens (tarifa de Haiku 5.5); solo para estimar el costo que se muestra.
    "precio_entrada": 0.10,
    "precio_salida": 0.50,
    "precio_cache_lectura": 0.01,
    "precio_cache_escritura": 0.125,
}

CAMPOS_ORGANIZACION = {
    "email":           "Email de contacto",
    "web":             "Sitio web",
    "telefono":        "Teléfono",
    "ciudad":          "Ciudad (pie de constancia)",
    "firmante_nombre": "Firmante de constancias",
    "firmante_cargo":  "Cargo del firmante",
}

# ── Caché breve: _ctx() lee esto en cada request y Atlas no está al lado ───────
_TTL_SEGUNDOS = 60
_cache: dict[str, tuple[float, object]] = {}


def _cached(key: str, loader):
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < _TTL_SEGUNDOS:
        return hit[1]
    value = loader()
    _cache[key] = (time.monotonic(), value)
    return value


def invalidar() -> None:
    """Descarta la caché. Llamar tras escribir en config, productos o cobros."""
    _cache.clear()


# ── Catálogos ──────────────────────────────────────────────────────────────────

def _slug(texto: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", texto.strip().lower()).strip("_")


def get_catalogo(key: str) -> list:
    def _load():
        doc = _config.find_one({"_id": key})
        return (doc or {}).get("valores") or []
    return _cached(f"catalogo:{key}", _load)


def set_catalogo(key: str, valores: list) -> list:
    """Valida según el formato del catálogo, guarda y devuelve lo guardado."""
    meta = CATALOGOS.get(key)
    if not meta:
        raise ValueError(f"Catálogo desconocido: {key}")
    limpio: list = []
    vistos: set = set()
    if meta["formato"] == "texto":
        for v in valores:
            v = v.strip() if isinstance(v, str) else ""
            if v and v not in vistos:
                vistos.add(v)
                limpio.append(v)
    else:
        clave = "valor" if meta["formato"] == "valor_label" else "id"
        for item in valores:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "").strip()
            codigo = str(item.get(clave) or "").strip() or _slug(label)
            if label and codigo and codigo not in vistos:
                vistos.add(codigo)
                limpio.append({clave: codigo, "label": label})
    _config.replace_one({"_id": key}, {"_id": key, "valores": limpio}, upsert=True)
    invalidar()
    return limpio


def get_valores_validos(key: str) -> set:
    """Códigos permitidos de un catálogo valor_label."""
    return {item["valor"] for item in get_catalogo(key)}


# ── Organización ───────────────────────────────────────────────────────────────

def get_organizacion() -> dict:
    def _load():
        doc = _config.find_one({"_id": "organizacion"}) or {}
        org = {campo: doc.get(campo) or "" for campo in CAMPOS_ORGANIZACION}
        org["email"] = org["email"] or BREVO_FROM_EMAIL
        org["web"] = org["web"] or WC_STORE_URL
        return org
    return _cached("organizacion", _load)


def set_organizacion(campos: dict) -> None:
    limpio = {c: str(campos.get(c) or "").strip() for c in CAMPOS_ORGANIZACION}
    _config.replace_one({"_id": "organizacion"}, {"_id": "organizacion", **limpio}, upsert=True)
    invalidar()


# ── Alianzas ───────────────────────────────────────────────────────────────────

def get_alianzas_config() -> dict:
    def _load():
        return {**_ALIANZAS_DEFECTO, **(_config.find_one({"_id": "alianzas_config"}) or {})}
    return _cached("alianzas_config", _load)


def set_alianzas_config(campos: dict) -> None:
    _config.update_one({"_id": "alianzas_config"}, {"$set": campos}, upsert=True)
    invalidar()


def get_alianzas_por_revisar() -> int:
    return _cached("alianzas_por_revisar", lambda: db.alianzas.count_documents({"estado": "nueva"}))


def get_ponentes_config() -> dict:
    def _load():
        return {**_PONENTES_DEFECTO, **(_config.find_one({"_id": "ponentes_config"}) or {})}
    return _cached("ponentes_config", _load)


def set_ponentes_config(campos: dict) -> None:
    _config.update_one({"_id": "ponentes_config"}, {"$set": campos}, upsert=True)
    invalidar()


def get_ponentes_por_revisar() -> int:
    return _cached("ponentes_por_revisar", lambda: db.ponentes.count_documents({"estado": "nuevo"}))


# ── Agente de correos ──────────────────────────────────────────────────────────

def get_agente_config() -> dict:
    def _load():
        return {**_AGENTE_DEFECTO, **(_config.find_one({"_id": "agente_correo_config"}) or {})}
    return _cached("agente_correo_config", _load)


def set_agente_config(campos: dict) -> None:
    _config.update_one({"_id": "agente_correo_config"}, {"$set": campos}, upsert=True)
    invalidar()


def get_correos_por_atender() -> int:
    return _cached("correos_por_atender", lambda: db.correos.count_documents(
        {"estado": {"$in": ["pendiente", "error"]}}))


# ── Períodos ───────────────────────────────────────────────────────────────────

def _anio_lima() -> int:
    return datetime.now(timezone(timedelta(hours=-5))).year


def _es_periodo(valor) -> bool:
    return isinstance(valor, str) and bool(re.fullmatch(r"\d{4}", valor))


def _periodos_con_cobros() -> list[str]:
    return sorted((p for p in db.payments.distinct("periodo") if _es_periodo(p)), reverse=True)


def get_periodos() -> list[str]:
    """Todos los períodos conocidos (con cobros o con producto), del más nuevo al más viejo."""
    def _load():
        conocidos = set(_periodos_con_cobros())
        conocidos.update(p for p in db.productos.distinct("periodo") if _es_periodo(p))
        return sorted(conocidos, reverse=True)
    return _cached("periodos", _load)


def get_periodos_cobro() -> list[str]:
    """Períodos que se pueden cobrar hoy: los que tienen un producto de cuota activo."""
    def _load():
        valores = db.productos.distinct(
            "periodo", {"activo": True, "tipo": {"$in": list(TIPOS_CUOTA)}}
        )
        return sorted((p for p in valores if _es_periodo(p)), reverse=True)
    return _cached("periodos_cobro", _load)


def get_periodo_actual() -> str:
    """
    Período de trabajo de toda la plataforma.

    Se puede fijar en /configuracion. Si no, es el año en curso cuando ya tiene
    cobros generados; si aún no, el último período con cobros — así el cambio de
    año ocurre al generar los cobros del año nuevo, no a medianoche del 1 de enero.
    """
    def _load():
        doc = _config.find_one({"_id": "periodo_actual"})
        if doc and _es_periodo(doc.get("valor")):
            return doc["valor"]
        anio = str(_anio_lima())
        con_cobros = _periodos_con_cobros()
        if anio in con_cobros:
            return anio
        anteriores = [p for p in con_cobros if p < anio]
        return anteriores[0] if anteriores else anio
    return _cached("periodo_actual", _load)


def set_periodo_actual(valor: str) -> None:
    """Fija el período de trabajo; con valor vacío vuelve a derivarse de los datos."""
    if _es_periodo(valor):
        _config.replace_one({"_id": "periodo_actual"}, {"_id": "periodo_actual", "valor": valor}, upsert=True)
    else:
        _config.delete_one({"_id": "periodo_actual"})
    invalidar()


def es_periodo_fijado() -> bool:
    return _config.find_one({"_id": "periodo_actual"}) is not None


def get_periodo_anterior(periodo: str | None = None) -> str:
    """Período inmediatamente anterior a `periodo` entre los conocidos."""
    periodo = periodo or get_periodo_actual()
    previos = [p for p in get_periodos() if p < periodo]
    return previos[0] if previos else str(int(periodo) - 1)
