"""Variables {{...}} de las comunicaciones a socios (envío directo, campañas por lotes y programados).

Los valores salen de la base en el momento del envío (R10): la deuda de cada socio, el período
elegido en los filtros, los medios de pago del catálogo y los datos de la organización. Así una
misma plantilla sirve para cualquier año y nunca lleva montos escritos a mano.
"""
import re

from bson import ObjectId

from config import catalogos
from webapp import db as pdb

ESTADOS_CON_DEUDA = catalogos.ESTADOS_CON_DEUDA

# (variable, qué pone) — se listan en la pantalla de Comunicaciones.
VARIABLES: list[tuple[str, str]] = [
    ("nombre_corto",       "Nombre y primer apellido: «Juan Pérez»"),
    ("tratamiento",        "C.P.C., Dr., Econ. … según su título (vacío si no aplica)"),
    ("nombres",            "Solo los nombres"),
    ("apellidos",          "Solo los apellidos"),
    ("nombre",             "Apellidos y nombres, como en el padrón"),
    ("numero_registro",    "Código de asociado: IPIDET-0123"),
    ("periodo",            "Período elegido en los filtros"),
    ("monto",              "Lo que debe del período elegido"),
    ("periodos_adeudados", "Todos los períodos con deuda: «2025 y 2026»"),
    ("deuda_total",        "Suma de lo que debe en todos los períodos"),
    ("medios_pago",        "Medios de pago (catálogo de Configuración)"),
    ("org_email",          "Email de contacto de la organización"),
    ("org_telefono",       "Teléfono de la organización"),
    ("org_web",            "Sitio web de la organización"),
    ("firmante",           "Nombre del firmante (Configuración)"),
    ("firmante_cargo",     "Cargo del firmante"),
]

# Variables que solo tienen sentido si el socio debe algo.
_VARS_DEUDA_PERIODO = ("monto",)
_VARS_DEUDA_TOTAL = ("periodos_adeudados", "deuda_total")

_RE_VAR = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")
_PARTICULAS = {"de", "del", "la", "las", "los", "y", "san"}


def _titulo_propio(texto: str) -> str:
    palabras = (texto or "").strip().lower().split()
    return " ".join(p if (i and p in _PARTICULAS) else p[:1].upper() + p[1:] for i, p in enumerate(palabras))


def _soles(monto: float) -> str:
    return f"S/ {monto:,.2f}"  # mismo formato que el filtro `soles` de la web


def _lista(items: list[str]) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " y " + items[-1]


def _precio_producto(producto_id: str | None, cache: dict) -> float:
    if not producto_id:
        return 0.0
    if producto_id not in cache:
        try:
            prod = pdb.productos_col.find_one({"_id": ObjectId(producto_id)}, {"precio": 1})
        except Exception:
            prod = None
        cache[producto_id] = float((prod or {}).get("precio") or 0)
    return cache[producto_id]


def saldo_de_pago(pago: dict, cache: dict | None = None) -> float:
    """Lo que falta pagar de un registro de payments, según su estado."""
    cache = {} if cache is None else cache
    estado = pago.get("estado")
    if estado not in ESTADOS_CON_DEUDA:
        return 0.0
    if estado == "fraccionamiento":
        cuotas = pago.get("cuotas") or []
        pendiente = sum(c.get("monto") or 0 for c in cuotas if c.get("estado") != "pagado")
        sin_cuota = max(0.0, (pago.get("monto_objetivo") or 0) - sum(c.get("monto") or 0 for c in cuotas))
        return round(pendiente + sin_cuota, 2)
    total = pago.get("monto_total") or _precio_producto(pago.get("producto_id"), cache)
    if estado == "parcial":
        return round(max(0.0, total - sum(p.get("monto") or 0 for p in pago.get("pagos_parciales") or [])), 2)
    return round(total, 2)


def contexto(member_ids: list[str], periodo: str | None) -> dict[str, dict]:
    """Valores por socio. Dos consultas a la base para toda la lista."""
    periodo = periodo or catalogos.get_periodo_actual()
    org = catalogos.get_organizacion()
    comunes = {
        "periodo":        periodo,
        "medios_pago":    _lista(catalogos.get_catalogo("medios_pago")),
        "org_email":      org.get("email", ""),
        "org_telefono":   org.get("telefono", ""),
        "org_web":        org.get("web", ""),
        "firmante":       _titulo_propio(org.get("firmante_nombre", "")),
        "firmante_cargo": org.get("firmante_cargo", ""),
    }
    ids = [m for m in dict.fromkeys(member_ids) if m]
    socios = {m["member_id"]: m for m in pdb.members_col.find(
        {"member_id": {"$in": ids}}, {"member_id": 1, "nombres": 1, "apellidos": 1, "titulo": 1})}
    deudas: dict[str, dict[str, float]] = {}
    cache: dict = {}
    for p in pdb.payments_col.find({"member_id": {"$in": ids}, "estado": {"$in": list(ESTADOS_CON_DEUDA)}},
                                   {"member_id": 1, "periodo": 1, "estado": 1, "producto_id": 1, "cuotas": 1,
                                    "monto_objetivo": 1, "monto_total": 1, "pagos_parciales": 1}):
        saldo = saldo_de_pago(p, cache)
        if saldo > 0:
            deudas.setdefault(p["member_id"], {})[p.get("periodo", "")] = saldo

    out = {}
    for mid in ids:
        m = socios.get(mid, {})
        nombres = _titulo_propio(m.get("nombres", ""))
        apellidos = _titulo_propio(m.get("apellidos", ""))
        primer_apellido = apellidos.split(" ")[0] if apellidos else ""
        if primer_apellido.lower() in _PARTICULAS:  # «De la Cruz»: se deja completo
            primer_apellido = apellidos
        d = deudas.get(mid, {})
        out[mid] = {
            **comunes,
            "nombres":            nombres,
            "apellidos":          apellidos,
            "nombre_corto":       " ".join(x for x in (nombres.split(" ")[0] if nombres else "", primer_apellido) if x),
            "tratamiento":        catalogos.get_tratamiento(m.get("titulo")),
            "numero_registro":    mid,
            "member_id":          mid,
            "monto":              _soles(d[periodo]) if d.get(periodo) else "",
            "periodos_adeudados": _lista(sorted(d)),
            "deuda_total":        _soles(sum(d.values())) if d else "",
        }
    return out


def muestra(periodo: str | None) -> dict:
    """Vista previa sin destinatarios: los datos comunes reales y los del socio como «variable»."""
    vals = {k: f"«{k}»" for k, _ in VARIABLES}
    vals.update(contexto(["__muestra__"], periodo)["__muestra__"])
    for k in ("nombres", "apellidos", "nombre_corto", "tratamiento", "numero_registro", "member_id",
              "monto", "periodos_adeudados", "deuda_total"):
        vals[k] = f"«{k}»"
    return vals


def usadas(*textos: str) -> set[str]:
    return {m.group(1) for t in textos for m in _RE_VAR.finditer(t or "")}


def sustituir(texto: str, destinatario: dict, valores: dict | None) -> str:
    """Reemplaza las variables. Sin `valores` (Público u otro origen) usa lo que trae el destinatario."""
    nombre = destinatario.get("nombre", "")
    vals = {"nombre": nombre or "asociado(a)", "titulo": destinatario.get("titulo", ""),
            "ubicacion": destinatario.get("ubicacion", ""), "estado_pago": destinatario.get("estado_pago", "")}
    vals.update(valores or {})
    vals.setdefault("nombre_corto", nombre)
    texto = _RE_VAR.sub(lambda m: str(vals.get(m.group(1), "")), texto or "")
    return re.sub(r"(?<=\S)  +(?=\S)", " ", texto)  # «Estimado(a)  Juan» cuando no hay tratamiento


def sin_deuda(destinatarios: list[dict], valores: dict[str, dict], asunto: str, cuerpo: str) -> list[dict]:
    """Destinatarios a los que el mensaje les hablaría de una deuda que no tienen."""
    vars_usadas = usadas(asunto, cuerpo)
    pide_periodo = bool(vars_usadas & set(_VARS_DEUDA_PERIODO))
    pide_total = bool(vars_usadas & set(_VARS_DEUDA_TOTAL))
    if not (pide_periodo or pide_total):
        return []
    malos = []
    for d in destinatarios:
        v = valores.get(d.get("member_id", ""), {})
        if (pide_periodo and not v.get("monto")) or (pide_total and not v.get("deuda_total")):
            malos.append(d)
    return malos


def falta_completar(*textos: str) -> bool:
    return any("[COMPLETAR" in (t or "").upper() for t in textos)
