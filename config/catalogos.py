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
