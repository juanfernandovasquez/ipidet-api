"""Cliente APISPERU — facturación electrónica SUNAT (UBL 2.1)."""
import httpx
import re
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

    # B001 → BB01, B002 → BB02 (SUNAT convention for NC de boleta)
    nc_serie = "BB" + serie[2:] if len(serie) == 4 else "BB01"

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


def validate_comprobante(
    comp: dict,
    member: dict | None,
    empresa: dict | None,
    ultimo_correlativo: int | None,
) -> dict:
    """
    Pre-emission validation. Returns:
    {errores, advertencias, puede_emitir, resumen}
    errores block emission; advertencias are warnings only.
    """
    errores: list[str] = []
    advertencias: list[str] = []

    tipo  = comp.get("tipo", "")
    monto = float(comp.get("monto_total") or 0)
    numero = (comp.get("numero") or "").strip()

    # ── Número / serie / correlativo ────────────────────────────────────
    serie = correlativo_str = ""
    correlativo_num = 0
    try:
        serie, correlativo_str = _parse_numero(numero)
        correlativo_num = int(correlativo_str)
        if correlativo_num <= 0:
            errores.append("El correlativo debe ser mayor a 0.")
    except (ValueError, Exception) as exc:
        errores.append(str(exc))

    if serie:
        if tipo == "boleta" and not re.match(r"^B\d{3}$", serie):
            errores.append(
                f"Serie inválida '{serie}'. Para boletas debe ser B001, B002, etc."
            )
        elif tipo == "factura" and not re.match(r"^F\d{3}$", serie):
            errores.append(
                f"Serie inválida '{serie}'. Para facturas debe ser F001, F002, etc."
            )

    # ── Monto ───────────────────────────────────────────────────────────
    if monto <= 0:
        errores.append(f"El monto debe ser mayor a 0 (actual: S/ {monto:.2f}).")

    # ── Fecha ───────────────────────────────────────────────────────────
    fecha = (comp.get("fecha_emision") or "").strip()
    if not fecha:
        errores.append("La fecha de emisión es requerida.")
    else:
        try:
            fecha_dt = datetime.strptime(fecha, "%Y-%m-%d")
            dias_atras = (datetime.now() - fecha_dt).days
            if dias_atras < 0:
                errores.append("La fecha de emisión no puede ser futura.")
            elif dias_atras > 7:
                advertencias.append(
                    f"La fecha de emisión tiene {dias_atras} días de antigüedad. "
                    "SUNAT puede rechazar documentos con más de 7 días."
                )
        except ValueError:
            errores.append(f"Fecha de emisión inválida: '{fecha}'.")

    # ── Validaciones por tipo ────────────────────────────────────────────
    destinatario_display = ""
    if tipo == "boleta":
        dni = (
            comp.get("destinatario_dni")
            or (member or {}).get("dni")
            or ""
        ).strip()
        nombre = (
            comp.get("destinatario_nombre")
            or " ".join(filter(None, [
                (member or {}).get("nombres", ""),
                (member or {}).get("apellidos", ""),
            ])).strip()
            or ""
        )
        if not dni or dni == "00000000":
            advertencias.append(
                "Sin DNI del destinatario — se enviará '00000000'. "
                "Carga el DNI del socio para un comprobante correcto."
            )
            destinatario_display = nombre or "SIN NOMBRE"
        elif not re.match(r"^\d{8}$", dni):
            errores.append(f"DNI inválido: '{dni}'. Debe tener exactamente 8 dígitos.")
            destinatario_display = nombre or "SIN NOMBRE"
        else:
            destinatario_display = f"{nombre or 'SIN NOMBRE'} / DNI {dni}"

    elif tipo == "factura":
        if not empresa:
            errores.append(
                "No se encontró la empresa vinculada. "
                "Verifica que el comprobante tenga una empresa asignada."
            )
        else:
            ruc = empresa.get("ruc", "").strip()
            razon = (empresa.get("razon_social") or empresa.get("nombre") or "").strip()
            if not ruc:
                errores.append(
                    "La empresa no tiene RUC. Ve a Empresas y completa el RUC antes de emitir."
                )
            elif not re.match(r"^\d{11}$", ruc):
                errores.append(f"RUC inválido: '{ruc}'. Debe tener exactamente 11 dígitos.")
            if not razon:
                advertencias.append(
                    "La empresa no tiene razón social. Se usará el nombre comercial."
                )
            destinatario_display = f"{razon or empresa.get('nombre', '')} / RUC {ruc}"
    else:
        errores.append(
            f"Tipo '{tipo}' no soportado para emisión electrónica. Solo boleta o factura."
        )

    # ── Secuencia de correlativos ────────────────────────────────────────
    siguiente_esperado = (ultimo_correlativo + 1) if ultimo_correlativo is not None else 1
    if correlativo_num > 0:
        if ultimo_correlativo is not None:
            if correlativo_num < siguiente_esperado:
                errores.append(
                    f"El correlativo {correlativo_str} ya fue emitido "
                    f"(último emitido en {serie}: {ultimo_correlativo:05d}). "
                    "SUNAT rechazará este número como duplicado."
                )
            elif correlativo_num > siguiente_esperado:
                gap = correlativo_num - siguiente_esperado
                advertencias.append(
                    f"Salto en secuencia: se esperaba {siguiente_esperado:05d} "
                    f"pero este es {correlativo_num:05d} (salto de {gap}). "
                    "SUNAT puede rechazar o generar inconsistencias."
                )
        elif correlativo_num > 1:
            advertencias.append(
                f"No hay comprobantes previos emitidos en la serie {serie} en esta plataforma. "
                f"Si ya emitiste comprobantes fuera de la plataforma, "
                f"asegúrate de que el correlativo {correlativo_str} sea el siguiente en tu serie."
            )

    # ── Config APISPERU ─────────────────────────────────────────────────
    if not APISPERU_TOKEN:
        errores.append(
            "APISPERU_TOKEN no configurado. Agrega el token en las variables de entorno."
        )
    if not IPIDET_RUC:
        errores.append(
            "IPIDET_RUC no configurado. Agrega el RUC de IPIDET en las variables de entorno."
        )

    # ── Estado del comprobante ──────────────────────────────────────────
    if comp.get("estado") == "anulado":
        errores.append("No se puede emitir un comprobante anulado en la plataforma.")

    return {
        "errores":      errores,
        "advertencias": advertencias,
        "puede_emitir": len(errores) == 0,
        "resumen": {
            "tipo":               tipo,
            "numero":             numero,
            "serie":              serie,
            "correlativo":        correlativo_str,
            "monto":              monto,
            "fecha_emision":      fecha,
            "destinatario":       destinatario_display,
            "ultimo_emitido":     ultimo_correlativo,
            "siguiente_esperado": siguiente_esperado,
        },
    }


def check_configured() -> bool:
    """Returns True if APISPERU integration is properly configured."""
    return bool(APISPERU_TOKEN and IPIDET_RUC)
