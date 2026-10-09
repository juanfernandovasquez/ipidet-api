"""Cliente APISPERU — facturación electrónica SUNAT (UBL 2.1)."""
import httpx
from datetime import datetime

from config.settings import (
    APISPERU_TOKEN,
    IPIDET_RUC,
    IPIDET_RAZON_SOCIAL,
    IPIDET_NOMBRE_COMERCIAL,
    IPIDET_DIRECCION,
    IPIDET_UBIGUEO,
)

_BASE = "https://facturacion.apisperu.com/api/v1"

# ── Número a letras (español, para el campo "legends") ────────────────────

_UNIDADES = [
    "", "UN", "DOS", "TRES", "CUATRO", "CINCO", "SEIS", "SIETE",
    "OCHO", "NUEVE", "DIEZ", "ONCE", "DOCE", "TRECE", "CATORCE",
    "QUINCE", "DIECISEIS", "DIECISIETE", "DIECIOCHO", "DIECINUEVE",
]
_DECENAS = [
    "", "", "VEINTE", "TREINTA", "CUARENTA", "CINCUENTA",
    "SESENTA", "SETENTA", "OCHENTA", "NOVENTA",
]
_CENTENAS = [
    "", "CIEN", "DOSCIENTOS", "TRESCIENTOS", "CUATROCIENTOS", "QUINIENTOS",
    "SEISCIENTOS", "SETECIENTOS", "OCHOCIENTOS", "NOVECIENTOS",
]


def _decenas(n: int) -> str:
    if n < 20:
        return _UNIDADES[n]
    d, u = n // 10, n % 10
    if u == 0:
        return _DECENAS[d]
    if d == 2:
        return f"VEINTI{_UNIDADES[u]}"
    return f"{_DECENAS[d]} Y {_UNIDADES[u]}"


def _cientos(n: int) -> str:
    if n == 0:
        return ""
    c, resto = n // 100, n % 100
    res = "CIENTO" if (c == 1 and resto > 0) else _CENTENAS[c]
    if resto:
        res = (res + " " if c else "") + _decenas(resto)
    return res


def _entero_a_letras(n: int) -> str:
    if n == 0:
        return "CERO"
    miles, cientos = n // 1000, n % 1000
    partes = []
    if miles:
        partes.append("MIL" if miles == 1 else f"{_cientos(miles)} MIL")
    if cientos:
        partes.append(_cientos(cientos))
    return " ".join(partes)


def _monto_a_letras(monto: float) -> str:
    entero = int(monto)
    centavos = round((monto - entero) * 100)
    return f"SON {_entero_a_letras(entero)} CON {centavos:02d}/100 SOLES"


# ── Helpers ────────────────────────────────────────────────────────────────

def _headers() -> dict:
    return {"Authorization": f"Bearer {APISPERU_TOKEN}", "Content-Type": "application/json"}


def _ipidet_company() -> dict:
    return {
        "ruc": IPIDET_RUC,
        "razonSocial": IPIDET_RAZON_SOCIAL,
        "nombreComercial": IPIDET_NOMBRE_COMERCIAL or IPIDET_RAZON_SOCIAL,
        "address": {
            "direccion": IPIDET_DIRECCION,
            "provincia": "LIMA",
            "departamento": "LIMA",
            "distrito": "LIMA",
            "ubigueo": IPIDET_UBIGUEO or "150101",
        },
    }


def _parse_numero(numero: str) -> tuple[str, str]:
    """'B001-000123' → ('B001', '000123')"""
    parts = (numero or "").strip().split("-", 1)
    if len(parts) != 2:
        raise ValueError(
            f"Número de comprobante inválido: {numero!r} — "
            "formato esperado: SERIE-CORRELATIVO (ej. B001-00001)"
        )
    return parts[0], parts[1]


def _process_response(r: httpx.Response) -> dict:
    try:
        data = r.json()
    except Exception:
        return {"ok": False, "error": f"Respuesta inválida ({r.status_code}): {r.text[:300]}"}

    if r.status_code == 401:
        return {"ok": False, "error": "Token APISPERU inválido o expirado. Actualiza APISPERU_TOKEN en .env"}
    if r.status_code not in (200, 201):
        msg = data.get("message") or data.get("error") or str(data)[:200]
        return {"ok": False, "error": f"APISPERU {r.status_code}: {msg}"}

    # DocumentResponse: {xml, hash, sunatResponse: {success, error, cdrZip, cdrResponse}}
    sr = data.get("sunatResponse") or {}
    cdr = sr.get("cdrResponse") or {}
    accepted = bool(cdr.get("accepted", False))
    sunat_ok = bool(sr.get("success", False)) and accepted

    err_msg = None
    if not sunat_ok:
        if sr.get("error"):
            e = sr["error"]
            err_msg = f"[{e.get('code', '')}] {e.get('message', str(e))}"
        if not err_msg:
            err_msg = cdr.get("description") or ("Rechazado por SUNAT" if not accepted else None)

    return {
        "ok": sunat_ok,
        "xml": data.get("xml", ""),
        "hash": data.get("hash", ""),
        "cdr_code": cdr.get("code", ""),
        "cdr_description": cdr.get("description", ""),
        "cdr_notes": cdr.get("notes") or [],
        "accepted": accepted,
        "error": err_msg,
    }


# ── Payload builder (exonerado de IGV — servicios de membresía) ───────────

def _build_invoice_payload(comprobante: dict, tipo_sunat: str, client: dict) -> dict:
    serie, correlativo = _parse_numero(comprobante["numero"])
    monto = float(comprobante.get("monto_total") or 0)
    fecha = comprobante.get("fecha_emision") or datetime.now().strftime("%Y-%m-%d")
    fecha_iso = f"{fecha}T00:00:00-05:00"
    concepto = (
        comprobante.get("concepto") or comprobante.get("producto_nombre") or "CUOTA MEMBRESIA IPIDET"
    ).upper()

    return {
        "ublVersion": "2.1",
        "tipoOperacion": "0101",
        "tipoDoc": tipo_sunat,
        "serie": serie,
        "correlativo": correlativo,
        "fechaEmision": fecha_iso,
        "formaPago": {"moneda": "PEN", "tipo": "Contado"},
        "tipoMoneda": "PEN",
        "client": client,
        "company": _ipidet_company(),
        "mtoOperGravadas": 0,
        "mtoOperExoneradas": monto,
        "mtoIGV": 0,
        "totalImpuestos": 0,
        "valorVenta": monto,
        "subTotal": monto,
        "mtoImpVenta": monto,
        "details": [{
            "codProducto": "CUOTA-IPIDET",
            "unidad": "ZZ",
            "descripcion": concepto,
            "cantidad": 1,
            "mtoValorUnitario": monto,
            "mtoValorVenta": monto,
            "mtoBaseIgv": monto,
            "porcentajeIgv": 0,
            "igv": 0,
            "tipAfeIgv": 20,
            "totalImpuestos": 0,
            "mtoPrecioUnitario": monto,
        }],
        "legends": [{"code": "1000", "value": _monto_a_letras(monto)}],
    }


# ── Emisión ────────────────────────────────────────────────────────────────

def emitir_boleta(comprobante: dict, member: dict | None) -> dict:
    """Envía boleta a SUNAT vía APISPERU. Retorna dict {ok, xml, hash, cdr_*, error}."""
    if not APISPERU_TOKEN:
        return {"ok": False, "error": "APISPERU_TOKEN no configurado en .env"}
    if not IPIDET_RUC:
        return {"ok": False, "error": "IPIDET_RUC no configurado en .env"}

    dni = (
        comprobante.get("destinatario_dni")
        or (member or {}).get("dni")
        or "00000000"
    )
    nombre = (
        comprobante.get("destinatario_nombre")
        or " ".join(filter(None, [
            (member or {}).get("nombres", ""),
            (member or {}).get("apellidos", ""),
        ])).strip()
        or "CLIENTE"
    )
    client = {"tipoDoc": "1", "numDoc": str(dni), "rznSocial": nombre.upper()}
    payload = _build_invoice_payload(comprobante, "03", client)

    try:
        with httpx.Client(timeout=30) as http:
            r = http.post(f"{_BASE}/invoice/send", json=payload, headers=_headers())
    except httpx.TimeoutException:
        return {"ok": False, "error": "Timeout al conectar con APISPERU (30s)"}
    except Exception as e:
        return {"ok": False, "error": f"Error de conexión con APISPERU: {e}"}

    return _process_response(r)


def emitir_factura(comprobante: dict, empresa: dict | None) -> dict:
    """Envía factura a SUNAT vía APISPERU. Retorna dict {ok, xml, hash, cdr_*, error}."""
    if not APISPERU_TOKEN:
        return {"ok": False, "error": "APISPERU_TOKEN no configurado en .env"}
    if not IPIDET_RUC:
        return {"ok": False, "error": "IPIDET_RUC no configurado en .env"}

    ruc = (empresa or {}).get("ruc", "").strip()
    razon = (empresa or {}).get("razon_social", "").strip() or comprobante.get("empresa", "CLIENTE")

    if not ruc:
        return {"ok": False, "error": "RUC de la empresa no encontrado. Completa el campo RUC en Empresas."}

    client = {"tipoDoc": "6", "numDoc": ruc, "rznSocial": razon.upper()}
    payload = _build_invoice_payload(comprobante, "01", client)

    try:
        with httpx.Client(timeout=30) as http:
            r = http.post(f"{_BASE}/invoice/send", json=payload, headers=_headers())
    except httpx.TimeoutException:
        return {"ok": False, "error": "Timeout al conectar con APISPERU (30s)"}
    except Exception as e:
        return {"ok": False, "error": f"Error de conexión con APISPERU: {e}"}

    return _process_response(r)


# ── Anulación ──────────────────────────────────────────────────────────────

def anular_boleta(comprobante: dict, correlativo_nc: str,
                  motivo: str = "ANULACION DE LA OPERACION") -> dict:
    """Anula una boleta via Nota de Crédito (tipoDoc 07)."""
    if not APISPERU_TOKEN:
        return {"ok": False, "error": "APISPERU_TOKEN no configurado en .env"}

    serie, correlativo = _parse_numero(comprobante["numero"])
    monto = float(comprobante.get("monto_total") or 0)
    fecha_hoy = datetime.now().strftime("%Y-%m-%d")

    # NC serie: BB01 para B001, BB02 para B002, etc.
    nc_serie = serie[0] + serie[1:2] + "0" + serie[-1] if len(serie) == 4 else "BB01"
    nc_serie = nc_serie.replace("B", "B", 1)  # stays as BB01

    dni = comprobante.get("destinatario_dni") or "00000000"
    nombre = (comprobante.get("destinatario_nombre") or "CLIENTE").upper()

    payload = {
        "ublVersion": "2.1",
        "tipoDoc": "07",
        "serie": nc_serie,
        "correlativo": str(correlativo_nc),
        "fechaEmision": f"{fecha_hoy}T00:00:00-05:00",
        "tipDocAfectado": "03",
        "numDocfectado": f"{serie}-{correlativo}",
        "codMotivo": "01",
        "desMotivo": motivo.upper(),
        "tipoMoneda": "PEN",
        "client": {"tipoDoc": "1", "numDoc": str(dni), "rznSocial": nombre},
        "company": _ipidet_company(),
        "mtoOperExoneradas": monto,
        "mtoIGV": 0,
        "totalImpuestos": 0,
        "mtoImpVenta": monto,
        "details": [{
            "codProducto": "CUOTA-IPIDET",
            "unidad": "ZZ",
            "descripcion": (comprobante.get("concepto") or "ANULACION").upper(),
            "cantidad": 1,
            "mtoValorUnitario": monto,
            "mtoValorVenta": monto,
            "mtoBaseIgv": monto,
            "porcentajeIgv": 0,
            "igv": 0,
            "tipAfeIgv": 20,
            "totalImpuestos": 0,
            "mtoPrecioUnitario": monto,
        }],
        "legends": [{"code": "1000", "value": _monto_a_letras(monto)}],
    }

    try:
        with httpx.Client(timeout=30) as http:
            r = http.post(f"{_BASE}/note/send", json=payload, headers=_headers())
    except httpx.TimeoutException:
        return {"ok": False, "error": "Timeout al conectar con APISPERU (30s)"}
    except Exception as e:
        return {"ok": False, "error": f"Error de conexión con APISPERU: {e}"}

    return _process_response(r)


def anular_factura(comprobante: dict, correlativo_baja: str,
                   motivo: str = "ERROR EN EMISION") -> dict:
    """Anula una factura via Comunicación de Baja."""
    if not APISPERU_TOKEN:
        return {"ok": False, "error": "APISPERU_TOKEN no configurado en .env"}

    serie, correlativo = _parse_numero(comprobante["numero"])
    fecha_hoy = datetime.now().strftime("%Y-%m-%d")
    fecha_iso = f"{fecha_hoy}T00:00:00-05:00"

    payload = {
        "correlativo": str(correlativo_baja),
        "fecGeneracion": fecha_iso,
        "fecComunicacion": fecha_iso,
        "company": _ipidet_company(),
        "details": [{
            "tipoDoc": "01",
            "serie": serie,
            "correlativo": correlativo,
            "desMotivoBaja": motivo.upper(),
        }],
    }

    try:
        with httpx.Client(timeout=30) as http:
            r = http.post(f"{_BASE}/voided/send", json=payload, headers=_headers())
    except httpx.TimeoutException:
        return {"ok": False, "error": "Timeout al conectar con APISPERU (30s)"}
    except Exception as e:
        return {"ok": False, "error": f"Error de conexión con APISPERU: {e}"}

    return _process_response(r)


def check_configured() -> bool:
    """Returns True if APISPERU integration is properly configured."""
    return bool(APISPERU_TOKEN and IPIDET_RUC)
