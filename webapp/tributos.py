"""IGV de boletas, facturas y notas de crédito. Único lugar donde se calcula.

Regla de la plataforma: todo monto guardado es el precio FINAL (con IGV si lo lleva). La base sale
dividiendo por (1 + tasa) y el IGV es la resta, línea por línea: las líneas siempre suman el total.

Cada línea guarda una foto al crearse (`tipo_afectacion`, `tasa_igv`, `tipo_producto`). Cambiar luego
el producto o la tasa no altera un comprobante existente.
  - «10», «20», «30»: catálogo 07 de SUNAT.
  - ""  : por definir. Sirve para un borrador; no se emite.
  - sin la clave: documento anterior a esta función. Todo se facturaba exonerado.
Al emitir o importar un XML se guarda además `comprobantes.tributos`, que desde ahí manda.

No importa webapp.db: lo usan db, sunat_client, pdf_builder y mailer.
"""
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from bson import ObjectId

from config import catalogos
from config.mongo_client import db

GRAVADO, EXONERADO, INAFECTO = "10", "20", "30"
# Norma tributaria (catálogo 07), no dato del negocio.
AFECTACIONES = {GRAVADO: "Con IGV (gravado)", EXONERADO: "Exonerado", INAFECTO: "Inafecto"}
# Solo estos productos pueden saldar una cuota del socio.
TIPOS_QUE_SALDAN_CUOTA = (*catalogos.TIPOS_CUOTA, "fraccionamiento")
BOLETA_CON_DNI_DESDE = 700          # Reglamento de Comprobantes de Pago
DETRACCION_DESDE = 700              # SPOT, servicios gravados con factura
_CENT = Decimal("0.01")


def dinero(valor) -> Decimal:
    """Decimal con 2 decimales, redondeo comercial (0.005 → 0.01)."""
    try:
        d = Decimal(str(valor if valor not in (None, "") else 0))
        if not d.is_finite():
            return Decimal("0.00")
        return d.quantize(_CENT, ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return Decimal("0.00")


def _tasa(valor) -> Decimal:
    try:
        t = Decimal(str(valor))
    except (InvalidOperation, ValueError):
        return Decimal("0")
    return t if t.is_finite() and 0 < t <= 30 else Decimal("0")


def desglosar(total, afectacion: str, tasa) -> tuple[Decimal, Decimal]:
    """(valor de venta, IGV) de un precio final."""
    t = dinero(total)
    if afectacion != GRAVADO:
        return t, Decimal("0.00")
    base = (t * 100 / (100 + _tasa(tasa))).quantize(_CENT, ROUND_HALF_UP)
    return base, t - base


def repartir(total, partes: int) -> list[float]:
    """Reparte un total en partes que suman exacto (1000 entre 3 = 333.34 + 333.33 + 333.33)."""
    if partes <= 0:
        return []
    cent = int(dinero(total) * 100)
    base, resto = divmod(cent, partes)
    return [(base + (1 if i < resto else 0)) / 100 for i in range(partes)]


# ── De dónde sale la afectación ───────────────────────────────────────────────

def _producto(producto_id, cache: dict) -> dict | None:
    pid = str(producto_id or "")
    if not ObjectId.is_valid(pid):
        return None
    if pid not in cache:
        cache[pid] = db.productos.find_one({"_id": ObjectId(pid)}, {"tipo": 1, "tipo_afectacion": 1})
    return cache[pid]


def tipo_de_linea(item: dict, cache: dict | None = None) -> str:
    """Tipo de producto de la línea, solo por FK (R8)."""
    if item.get("evento_id"):
        return "evento"
    prod = _producto(item.get("producto_id"), {} if cache is None else cache)
    if prod and prod.get("tipo"):
        return prod["tipo"]
    if item.get("tipo_producto"):
        return item["tipo_producto"]
    if item.get("payment_id"):
        return "fraccionamiento" if item.get("tipo_pago") == "cuota" else "cuota_anual"
    return ""


def salda_cuota(item: dict, cache: dict | None = None) -> bool:
    """False si la línea es una entrada u otra venta: nunca marca pagos del socio."""
    tipo = tipo_de_linea(item, cache)
    return not tipo or tipo in TIPOS_QUE_SALDAN_CUOTA


def por_defecto(item: dict, cache: dict | None = None) -> str:
    """Lo que dice el producto o, si no, la configuración de su tipo. "" = por definir."""
    cache = {} if cache is None else cache
    prod = _producto(item.get("producto_id"), cache)
    if prod and prod.get("tipo_afectacion") in AFECTACIONES:
        return prod["tipo_afectacion"]
    valor = catalogos.get_facturacion()["afectacion_por_tipo"].get(tipo_de_linea(item, cache), "")
    return valor if valor in AFECTACIONES else ""


def sellar(items: list, historico: bool = False) -> list:
    """Deja en cada línea su foto tributaria. No pisa lo que la línea ya trae elegido.
    `historico`: al editar un documento anterior, una línea sin la clave sigue exonerada."""
    tasa = catalogos.get_facturacion()["tasa_igv"]
    cache: dict = {}
    for it in items or []:
        tipo = tipo_de_linea(it, cache)
        if tipo:
            it["tipo_producto"] = tipo
        if it.get("tipo_afectacion") not in AFECTACIONES:
            sin_clave = "tipo_afectacion" not in it
            valor = por_defecto(it, cache)
            it["tipo_afectacion"] = valor or (EXONERADO if historico and sin_clave and tipo != "evento" else "")
        if it["tipo_afectacion"] == GRAVADO:
            if not _tasa(it.get("tasa_igv")):
                it["tasa_igv"] = tasa
        else:
            it.pop("tasa_igv", None)
    return items


def de_linea(item: dict, cache: dict | None = None) -> str:
    """Afectación vigente de una línea guardada."""
    if "tipo_afectacion" in item:
        return item["tipo_afectacion"] if item["tipo_afectacion"] in AFECTACIONES else ""
    if tipo_de_linea(item, cache) in ("evento", "otro"):
        return por_defecto(item, cache)
    return EXONERADO    # anterior a esta función


def lineas_de(comp: dict) -> list[dict]:
    """Líneas normalizadas para calcular, imprimir y emitir."""
    items = comp.get("items") or []
    total = dinero(comp.get("monto_total"))
    nombre = comp.get("concepto") or comp.get("producto_nombre") or ""
    cache: dict = {}
    tasa_actual = catalogos.get_facturacion()["tasa_igv"]
    if not items:
        afect = comp["tipo_afectacion"] if "tipo_afectacion" in comp else EXONERADO
        items = [{"producto_nombre": nombre, "monto": total, "tipo_afectacion": afect,
                  "tasa_igv": comp.get("tasa_igv"), "member_id": (comp.get("socios") or [""])[0]}]
    elif total and not any(dinero(i.get("monto")) for i in items):
        # Líneas sin monto (altas antiguas): el total va en la primera.
        primera = items[0]
        items = [{**primera, "monto": total, "producto_nombre": primera.get("producto_nombre") or nombre,
                  "tipo_afectacion": de_linea(primera, cache)}]
    filas = []
    for it in items:
        monto = dinero(it.get("monto"))
        if monto <= 0:
            continue
        afect = de_linea(it, cache)
        tasa = (_tasa(it.get("tasa_igv")) or _tasa(tasa_actual)) if afect == GRAVADO else Decimal("0")
        base, igv = desglosar(monto, afect, tasa)
        filas.append({"producto_nombre": it.get("producto_nombre") or nombre, "member_id": it.get("member_id") or "",
                      "codigo_sunat": it.get("codigo_sunat") or "", "tipo_producto": tipo_de_linea(it, cache),
                      "afectacion": afect, "tasa": tasa, "base": base, "igv": igv, "total": monto})
    return filas


# ── Totales ───────────────────────────────────────────────────────────────────

def totales(lineas: list[dict]) -> dict:
    suma = lambda afect: sum((x["base"] for x in lineas if x["afectacion"] == afect), Decimal("0.00"))  # noqa: E731
    tasas = {x["tasa"] for x in lineas if x["afectacion"] == GRAVADO}
    igv = sum((x["igv"] for x in lineas), Decimal("0.00"))
    total = sum((x["total"] for x in lineas), Decimal("0.00"))
    return {
        "gravadas": float(suma(GRAVADO)), "exoneradas": float(suma(EXONERADO)), "inafectas": float(suma(INAFECTO)),
        "igv": float(igv), "valor_venta": float(total - igv), "total": float(total),
        "tasa": float(next(iter(tasas))) if len(tasas) == 1 else None,
    }


def resumen(comp: dict) -> dict:
    """Lo que muestran el PDF, el correo, la lista y la revisión previa."""
    foto = comp.get("tributos")
    if foto and abs(dinero(foto.get("total")) - dinero(comp.get("monto_total"))) < _CENT:
        return {"tasa": None, **foto, "sin_definir": 0, "descuadre": 0.0}
    lineas = lineas_de(comp)
    t = totales(lineas)
    sin_definir = sum(1 for x in lineas if not x["afectacion"])
    return {**t, "sin_definir": sin_definir,
            "descuadre": float(dinero(comp.get("monto_total")) - dinero(t["total"]))}


def lineas_de_nota(original: dict, monto, indices: list | None = None, motivo: str = "") -> list[dict]:
    """Líneas de una nota de crédito con la misma afectación y tasa del original.
    Si el monto es menor que lo elegido, se reparte en proporción entre cada clase de IGV."""
    items = original.get("items") or []
    if indices is not None and items:
        items = [items[i] for i in indices if 0 <= i < len(items)] or items
        base_doc = {**original, "items": items,
                    "monto_total": float(sum((dinero(i.get("monto")) for i in items), Decimal("0.00")))}
    else:
        base_doc = original
    lineas = lineas_de(base_doc)
    pedido = dinero(monto)
    disponible = sum((x["total"] for x in lineas), Decimal("0.00"))
    if not lineas or disponible == pedido:
        return lineas
    clases: dict[tuple, dict] = {}
    for x in lineas:
        c = clases.setdefault((x["afectacion"], x["tasa"]), {**x, "total": Decimal("0.00"), "member_id": "",
                                                             "producto_nombre": motivo or x["producto_nombre"]})
        c["total"] += x["total"]
    filas, resta = list(clases.values()), pedido
    for n, c in enumerate(filas):
        parte = resta if n == len(filas) - 1 else (pedido * c["total"] / disponible).quantize(_CENT, ROUND_HALF_UP)
        resta -= parte
        c["total"] = parte
        c["base"], c["igv"] = desglosar(parte, c["afectacion"], c["tasa"])
    return [c for c in filas if c["total"] > 0]


def aplicar_xml(items: list, datos: dict) -> list:
    """Pone en las líneas la afectación que trae el XML, que es el documento oficial."""
    del_xml = datos.get("items") or []
    unicas = {i.get("tipo_afectacion") for i in del_xml}
    for n, it in enumerate(items or []):
        fuente = del_xml[n] if len(items) == len(del_xml) else (del_xml[0] if len(unicas) == 1 and del_xml else {})
        if fuente.get("tipo_afectacion") in AFECTACIONES:
            it["tipo_afectacion"] = fuente["tipo_afectacion"]
            if fuente.get("tasa_igv"):
                it["tasa_igv"] = fuente["tasa_igv"]
            else:
                it.pop("tasa_igv", None)
    return items
