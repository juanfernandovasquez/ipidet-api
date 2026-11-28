"""Forma de pago de un comprobante: contado o crédito en cuotas (RS 193-2020/SUNAT).

Una factura al crédito informa a SUNAT el monto neto pendiente de pago y, por cada cuota, su monto y su fecha
de vencimiento. Aquí se guardan en `comprobantes.cuotas_credito` [{numero, monto, fecha_venc, estado, fecha_pago}].
`fecha_vencimiento` queda como la fecha de la última cuota: así siguen funcionando las listas y los filtros.

Un comprobante anterior con solo `fecha_vencimiento` se lee como crédito de una sola cuota.
Hoy el monto neto pendiente es el total: la plataforma no emite facturas con detracción ni con pago inicial.
"""
import re
from datetime import date

from webapp import tributos

_FECHA = re.compile(r"\d{4}-\d{2}-\d{2}")


def es_credito(comp: dict) -> bool:
    return bool(comp.get("cuotas_credito") or comp.get("fecha_vencimiento"))


def cuotas_de(comp: dict) -> list[dict]:
    if comp.get("cuotas_credito"):
        return [{"numero": c.get("numero") or n, "monto": float(tributos.dinero(c.get("monto"))),
                 "fecha_venc": c.get("fecha_venc") or "", "estado": c.get("estado") or "pendiente",
                 "fecha_pago": c.get("fecha_pago") or ""}
                for n, c in enumerate(comp["cuotas_credito"], 1)]
    if comp.get("fecha_vencimiento"):
        cobrado = comp.get("estado_credito") == "cobrado"
        return [{"numero": 1, "monto": float(tributos.dinero(comp.get("monto_total"))),
                 "fecha_venc": comp["fecha_vencimiento"], "estado": "pagado" if cobrado else "pendiente",
                 "fecha_pago": comp.get("fecha_cobro") or ""}]
    return []


def pendiente(comp: dict) -> float:
    """Monto neto pendiente de pago al emitir (sin detracción ni pago inicial: el total)."""
    return float(tributos.dinero(comp.get("monto_total")))


def limpiar(cuotas: list, total) -> list[dict]:
    """Cuotas recibidas del formulario, numeradas y ordenadas por fecha. No valida: ver `errores`."""
    filas = []
    for c in cuotas or []:
        if not isinstance(c, dict):
            continue
        fecha = str(c.get("fecha_venc") or "").strip()[:10]
        monto = float(tributos.dinero(c.get("monto")))
        if fecha or monto:
            filas.append({"monto": monto, "fecha_venc": fecha, "estado": "pendiente", "fecha_pago": ""})
    filas.sort(key=lambda c: c["fecha_venc"] or "9999")
    for n, c in enumerate(filas, 1):
        c["numero"] = n
    return filas


def errores(comp: dict, cuotas: list | None = None) -> list[str]:
    """Lo que SUNAT exige de un crédito. Sirve al guardar y antes de emitir."""
    cuotas = cuotas_de(comp) if cuotas is None else cuotas
    if not cuotas:
        return []
    res = []
    if comp.get("tipo") != "factura":
        res.append("El pago al crédito con cuotas se informa a SUNAT solo en facturas.")
    emision = comp.get("fecha_emision") or ""
    for c in cuotas:
        n = c.get("numero")
        if not _FECHA.fullmatch(c.get("fecha_venc") or ""):
            res.append(f"La cuota {n} no tiene fecha de vencimiento.")
            continue
        try:
            date.fromisoformat(c["fecha_venc"])
        except ValueError:
            res.append(f"La fecha de la cuota {n} no es válida.")
            continue
        if emision and c["fecha_venc"] <= emision:
            res.append(f"La cuota {n} vence el {c['fecha_venc']}: debe ser posterior a la emisión ({emision}).")
        if tributos.dinero(c.get("monto")) <= 0:
            res.append(f"La cuota {n} debe tener un monto mayor a cero.")
    fechas = [c["fecha_venc"] for c in cuotas if c.get("fecha_venc")]
    if len(set(fechas)) != len(fechas):
        res.append("Dos cuotas no pueden vencer el mismo día.")
    suma = sum(tributos.dinero(c.get("monto")) for c in cuotas)
    if suma != tributos.dinero(pendiente(comp)):
        res.append(f"Las cuotas suman S/ {suma:.2f} y el monto pendiente es S/ {pendiente(comp):.2f}.")
    return res


def estado(comp: dict, hoy: str) -> str:
    """cobrado · vencido · pendiente (por cuotas, no por lo guardado: una fecha pasa sola a vencida)."""
    cuotas = cuotas_de(comp)
    if not cuotas:
        return ""
    if all(c["estado"] == "pagado" for c in cuotas):
        return "cobrado"
    if any(c["estado"] != "pagado" and c["fecha_venc"] and c["fecha_venc"] < hoy for c in cuotas):
        return "vencido"
    return "pendiente"


def para_sunat(comp: dict) -> tuple[dict, list[dict]]:
    """(formaPago, cuotas) del payload de APISPERU."""
    cuotas = cuotas_de(comp)
    if not cuotas or comp.get("tipo") != "factura":
        return {"moneda": "PEN", "tipo": "Contado"}, []
    return ({"moneda": "PEN", "tipo": "Credito", "monto": pendiente(comp)},
            [{"moneda": "PEN", "monto": c["monto"], "fechaPago": f"{c['fecha_venc']}T00:00:00-05:00"} for c in cuotas])
