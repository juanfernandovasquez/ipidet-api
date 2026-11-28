"""Cliente APISPERU — facturación electrónica SUNAT (UBL 2.1)."""
import httpx
import re
from datetime import datetime, timedelta, timezone

from config import catalogos
from webapp import forma_pago, tributos

from config.settings import (
    APISPERU_TOKEN,
    IPIDET_RUC,
    IPIDET_RAZON_SOCIAL,
    IPIDET_NOMBRE_COMERCIAL,
    IPIDET_DIRECCION,
    IPIDET_UBIGUEO,
    SUNAT_ENVIRONMENT,
)

_BASE = "https://facturacion.apisperu.com/api/v1"

_LIMA = timezone(timedelta(hours=-5))


def hoy_lima() -> str:
    """Render corre en UTC: después de las 7 p. m. de Lima ya sería mañana."""
    return datetime.now(_LIMA).strftime("%Y-%m-%d")


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
    if n >= 1_000_000:
        millones, resto = divmod(n, 1_000_000)
        cab = "UN MILLON" if millones == 1 else f"{_entero_a_letras(millones)} MILLONES"
        return f"{cab} {_entero_a_letras(resto)}" if resto else cab
    miles, cientos = n // 1000, n % 1000
    partes = []
    if miles:
        partes.append("MIL" if miles == 1 else f"{_cientos(miles)} MIL")
    if cientos:
        partes.append(_cientos(cientos))
    return " ".join(partes)


def _monto_a_letras(monto: float) -> str:
    entero, centavos = divmod(int(tributos.dinero(monto) * 100), 100)
    return f"SON {_entero_a_letras(entero)} CON {centavos:02d}/100 SOLES"


# ── Helpers ────────────────────────────────────────────────────────────────

def _headers() -> dict:
    return {"Authorization": f"Bearer {APISPERU_TOKEN}", "Content-Type": "application/json"}


def _ipidet_company() -> dict:
    """Emisor: sección IPIDET de la plataforma, o las variables de Render si allí está vacío.
    El RUC siempre es el de Render (IPIDET_RUC): está atado al token de APISPERU."""
    from config import catalogos
    f = catalogos.get_datos_fiscales()
    return {
        "ruc": IPIDET_RUC,
        "razonSocial": f["razon_social"],
        "nombreComercial": f["nombre_comercial"],
        "address": {
            "direccion": f["direccion_fiscal"],
            # Sin dato cargado, lo que siempre se envió: sede en Lima.
            "provincia": (f["provincia"] or "LIMA").upper(),
            "departamento": (f["departamento"] or "LIMA").upper(),
            "distrito": (f["distrito"] or "LIMA").upper(),
            "ubigueo": f["ubigeo"] or "150101",
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

    apisperu_success = bool(sr.get("success", False))
    cdr_code = str(cdr.get("code") if cdr.get("code") is not None else "").strip()

    # Regla de SUNAT (la misma de greenter, que usa APISPERU): CDR 0 o ≥ 4000 = aceptado (≥ 4000 con
    # observaciones); de 100 a 3999 = excepción o rechazo. `success` solo dice que SUNAT respondió.
    if cdr_code.isdigit():
        accepted = int(cdr_code) == 0 or int(cdr_code) >= 4000
    elif cdr.get("accepted") is not None:
        accepted = bool(cdr["accepted"])
    else:
        accepted = apisperu_success and not sr.get("error")

    sunat_ok = accepted

    err_msg = None
    if not sunat_ok:
        if sr.get("error"):
            e = sr["error"]
            err_msg = f"[{e.get('code', '')}] {e.get('message', str(e))}"
        if not err_msg:
            err_msg = cdr.get("description") or "Rechazado por SUNAT"

    return {
        "ok": sunat_ok,
        "xml": data.get("xml", ""),
        "hash": data.get("hash", ""),
        "cdr_code": cdr_code,
        "cdr_description": cdr.get("description", ""),
        "cdr_notes": cdr.get("notes") or [],
        "accepted": accepted,
        "error": err_msg,
    }


# ── Payload builder (webapp/tributos.py calcula el IGV) ────────────────────

def _build_details(lineas: list, fallback_concepto: str) -> list:
    """details[] de SUNAT. Cada línea es una unidad a precio final."""
    detalles = []
    for x in lineas:
        gravada = x["afectacion"] == tributos.GRAVADO
        base, igv = float(x["base"]), float(x["igv"])
        de_cuota = not x["tipo_producto"] or x["tipo_producto"] in tributos.TIPOS_QUE_SALDAN_CUOTA
        detalles.append({
            "codProducto": x["codigo_sunat"] or ("CUOTA-IPIDET" if de_cuota and not gravada else "SERVICIO-IPIDET"),
            "unidad": "ZZ",
            "descripcion": (x["producto_nombre"] or fallback_concepto).upper()[:500],
            "cantidad": 1,
            "mtoValorUnitario": base,
            "mtoValorVenta": base,
            "mtoBaseIgv": base,
            "porcentajeIgv": float(x["tasa"]) if gravada else 0,
            "igv": igv,
            "tipAfeIgv": int(x["afectacion"]),
            "totalImpuestos": igv,
            "mtoPrecioUnitario": float(x["total"]),
        })
    return detalles


def _totales_payload(lineas: list) -> dict:
    if not lineas:
        raise ValueError("El comprobante no tiene líneas con monto.")
    if any(x["afectacion"] not in tributos.AFECTACIONES for x in lineas):
        raise ValueError("Hay líneas sin definir si llevan IGV.")
    t = tributos.totales(lineas)
    campos = {"mtoIGV": t["igv"], "totalImpuestos": t["igv"], "mtoImpVenta": t["total"]}
    # La plantilla UBL escribe un subtotal por cada campo presente (aunque valga 0): solo van los que aplican.
    # Exonerado conserva el «gravadas 0» con el que SUNAT ya acepta las boletas de membresía.
    if t["gravadas"] or t["exoneradas"]:
        campos["mtoOperGravadas"] = t["gravadas"]
    if t["exoneradas"]:
        campos["mtoOperExoneradas"] = t["exoneradas"]
    if t["inafectas"]:
        campos["mtoOperInafectas"] = t["inafectas"]
    return campos


def _build_invoice_payload(comprobante: dict, tipo_sunat: str, client: dict) -> dict:
    serie, correlativo = _parse_numero(comprobante["numero"])
    lineas = tributos.lineas_de(comprobante)
    totales = _totales_payload(lineas)
    monto = totales["mtoImpVenta"]
    if tributos.dinero(monto) != tributos.dinero(comprobante.get("monto_total")):
        raise ValueError("Las líneas no suman el total del comprobante.")
    fecha = comprobante.get("fecha_emision") or hoy_lima()
    fecha_iso = f"{fecha}T00:00:00-05:00"
    concepto = (
        comprobante.get("concepto") or comprobante.get("producto_nombre") or "CUOTA MEMBRESIA IPIDET"
    ).upper()

    details = _build_details(lineas, concepto)

    # Al crédito: monto neto pendiente y cada cuota con su fecha (RS 193-2020). Solo facturas.
    pago, cuotas = forma_pago.para_sunat(comprobante) if tipo_sunat == "01" else ({"moneda": "PEN", "tipo": "Contado"}, [])

    payload = {
        "ublVersion": "2.1",
        "tipoOperacion": "0101",
        "tipoDoc": tipo_sunat,
        "serie": serie,
        "correlativo": correlativo,
        "fechaEmision": fecha_iso,
        "formaPago": pago,
        "tipoMoneda": "PEN",
        "client": client,
        "company": _ipidet_company(),
        **totales,
        "valorVenta": float(tributos.dinero(monto) - tributos.dinero(totales["mtoIGV"])),
        "subTotal": monto,
        "details": details,
        "legends": [{"code": "1000", "value": _monto_a_letras(monto)}],
    }
    if cuotas:
        payload["cuotas"] = cuotas
    return payload


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
    try:
        payload = _build_invoice_payload(comprobante, "03", client)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    try:
        with httpx.Client(timeout=30) as http:
            r = http.post(f"{_BASE}/invoice/send", json=payload, headers=_headers())
    except httpx.TimeoutException:
        return {"ok": False, "error": "APISPERU no respondió en 30 s. Puede que SUNAT SÍ lo haya recibido: "
                                      "consúltalo en el portal SOL antes de volver a enviarlo."}
    except Exception as e:
        return {"ok": False, "error": f"Error de conexión con APISPERU: {e}"}

    return _process_response(r)


def emitir_factura(comprobante: dict, empresa: dict | None) -> dict:
    """Envía factura a SUNAT vía APISPERU. Retorna dict {ok, xml, hash, cdr_*, error}.

    La forma de pago (contado o crédito con sus cuotas) sale de webapp/forma_pago.py.
    """
    if not APISPERU_TOKEN:
        return {"ok": False, "error": "APISPERU_TOKEN no configurado en .env"}
    if not IPIDET_RUC:
        return {"ok": False, "error": "IPIDET_RUC no configurado en .env"}

    ruc = (empresa or {}).get("ruc", "").strip()
    razon = (empresa or {}).get("razon_social", "").strip() or comprobante.get("empresa", "CLIENTE")

    if not ruc:
        return {"ok": False, "error": "RUC de la empresa no encontrado. Completa el campo RUC en Empresas."}

    client = {"tipoDoc": "6", "numDoc": ruc, "rznSocial": razon.upper()}
    try:
        payload = _build_invoice_payload(comprobante, "01", client)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    try:
        with httpx.Client(timeout=30) as http:
            r = http.post(f"{_BASE}/invoice/send", json=payload, headers=_headers())
    except httpx.TimeoutException:
        return {"ok": False, "error": "APISPERU no respondió en 30 s. Puede que SUNAT SÍ lo haya recibido: "
                                      "consúltalo en el portal SOL antes de volver a enviarlo."}
    except Exception as e:
        return {"ok": False, "error": f"Error de conexión con APISPERU: {e}"}

    return _process_response(r)


# ── Anulación ──────────────────────────────────────────────────────────────

def serie_nota_credito(serie_original: str) -> str:
    """Serie de la nota según el comprobante: B001 → BC01, F001 → FC01.
    SUNAT exige que empiece con B (si modifica una boleta) o F (si modifica una factura)."""
    s = (serie_original or "").upper()
    return f"{s[:1] or 'B'}C{s[-2:] if len(s) == 4 else '01'}"


def _cliente_de(comprobante: dict, member: dict | None, empresa: dict | None) -> dict:
    if comprobante.get("tipo") == "factura":
        ruc = (empresa or {}).get("ruc", "").strip() or comprobante.get("ruc_empresa", "")
        razon = ((empresa or {}).get("razon_social") or comprobante.get("empresa") or "CLIENTE").strip()
        return {"tipoDoc": "6", "numDoc": ruc, "rznSocial": razon.upper()}
    dni = comprobante.get("destinatario_dni") or (member or {}).get("dni") or "00000000"
    nombre = (comprobante.get("destinatario_nombre") or " ".join(filter(None, [
        (member or {}).get("nombres", ""), (member or {}).get("apellidos", "")])).strip() or "CLIENTE")
    return {"tipoDoc": "1", "numDoc": str(dni), "rznSocial": nombre.upper()}


def payload_nota_credito(original: dict, nota: dict, member: dict | None = None,
                         empresa: dict | None = None) -> dict:
    """Arma la Nota de Crédito (tipoDoc 07) para APISPERU.
    nota = {numero 'BC01-00001', fecha_emision, motivo_codigo, motivo, monto, lineas_idx}.
    La nota repite la afectación y la tasa de IGV del comprobante original."""
    serie_ref, corr_ref = _parse_numero(original["numero"])
    serie, correlativo = _parse_numero(nota["numero"])
    monto = round(float(nota.get("monto") or 0), 2)
    fecha = nota.get("fecha_emision") or hoy_lima()
    motivo = (nota.get("motivo") or "ANULACION DE LA OPERACION").upper()[:250]
    lineas = tributos.lineas_de_nota(original, monto, nota.get("lineas_idx"), motivo)
    totales = _totales_payload(lineas)
    return {
        "ublVersion": "2.1",
        "tipoDoc": "07",
        "serie": serie,
        "correlativo": correlativo,
        "fechaEmision": f"{fecha}T00:00:00-05:00",
        "tipDocAfectado": "01" if original.get("tipo") == "factura" else "03",
        "numDocfectado": f"{serie_ref}-{corr_ref}",
        "codMotivo": nota["motivo_codigo"],
        "desMotivo": motivo,
        "tipoMoneda": "PEN",
        "client": _cliente_de(original, member, empresa),
        "company": _ipidet_company(),
        **totales,
        "details": _build_details(lineas, motivo),
        "legends": [{"code": "1000", "value": _monto_a_letras(monto)}],
    }


def emitir_nota_credito(original: dict, nota: dict, member: dict | None = None,
                        empresa: dict | None = None) -> dict:
    """Envía una Nota de Crédito a SUNAT vía APISPERU. Retorna {ok, xml, hash, cdr_*, error}."""
    if not APISPERU_TOKEN:
        return {"ok": False, "error": "APISPERU_TOKEN no configurado en .env"}
    if not IPIDET_RUC:
        return {"ok": False, "error": "IPIDET_RUC no configurado en .env"}
    if nota.get("motivo_codigo") == "13":
        return {"ok": False, "error": "El motivo 13 (ajuste de cuotas) no se puede emitir desde la plataforma."}
    try:
        payload = payload_nota_credito(original, nota, member, empresa)
    except (ValueError, KeyError) as e:
        return {"ok": False, "error": str(e)}
    if payload["client"]["tipoDoc"] == "6" and not re.match(r"^\d{11}$", payload["client"]["numDoc"] or ""):
        return {"ok": False, "error": "La empresa del comprobante no tiene un RUC válido. Complétalo en Empresas."}

    try:
        with httpx.Client(timeout=30) as http:
            r = http.post(f"{_BASE}/note/send", json=payload, headers=_headers())
    except httpx.TimeoutException:
        return {"ok": False, "error": "APISPERU no respondió en 30 s. Puede que SUNAT SÍ lo haya recibido: "
                                      "consúltalo en el portal SOL antes de volver a enviarlo."}
    except Exception as e:
        return {"ok": False, "error": f"Error de conexión con APISPERU: {e}"}

    return _process_response(r)


def anular_factura(comprobante: dict, correlativo_baja: str,
                   motivo: str = "ERROR EN EMISION") -> dict:
    """Anula una factura via Comunicación de Baja."""
    if not APISPERU_TOKEN:
        return {"ok": False, "error": "APISPERU_TOKEN no configurado en .env"}

    serie, correlativo = _parse_numero(comprobante["numero"])
    fecha_hoy = hoy_lima()
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
        return {"ok": False, "error": "APISPERU no respondió en 30 s. Puede que SUNAT SÍ lo haya recibido: "
                                      "consúltalo en el portal SOL antes de volver a enviarlo."}
    except Exception as e:
        return {"ok": False, "error": f"Error de conexión con APISPERU: {e}"}

    return _process_response(r)


def validate_comprobante(
    comp: dict,
    member: dict | None,
    empresa: dict | None,
    ultimo_correlativo: int | None,
    ultimo_ambiente: str | None = None,
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
            dias_atras = (datetime.strptime(hoy_lima(), "%Y-%m-%d") - fecha_dt).days
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
        if (not dni or dni == "00000000") and monto > tributos.BOLETA_CON_DNI_DESDE:
            errores.append(f"Una boleta de más de S/ {tributos.BOLETA_CON_DNI_DESDE} necesita el nombre y el DNI del comprador.")
            destinatario_display = nombre or "SIN NOMBRE"
        elif not dni or dni == "00000000":
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
                if tipo == "factura":
                    errores.append(
                        f"Salto en secuencia: se esperaba {siguiente_esperado:05d} "
                        f"pero este es {correlativo_num:05d} (salto de {gap}). "
                        "SUNAT rechaza facturas con correlativos no consecutivos."
                    )
                else:
                    advertencias.append(
                        f"Salto en secuencia: se esperaba {siguiente_esperado:05d} "
                        f"pero este es {correlativo_num:05d} (salto de {gap}). "
                        "SUNAT es más tolerante con boletas pero se recomienda "
                        "corregir la secuencia."
                    )
        elif correlativo_num > 1:
            advertencias.append(
                f"No hay comprobantes previos emitidos en la serie {serie} en esta plataforma. "
                f"Si ya emitiste comprobantes fuera de la plataforma, "
                f"asegúrate de que el correlativo {correlativo_str} sea el siguiente en tu serie."
            )

    # ── Crédito: cuotas que se informarán a SUNAT ───────────────────────
    cuotas = forma_pago.cuotas_de(comp)
    if cuotas:
        errores.extend(forma_pago.errores(comp, cuotas))
        if tipo == "factura":
            advertencias.append("Se emitirá al crédito en " + (f"{len(cuotas)} cuotas" if len(cuotas) > 1 else "una cuota")
                                + ": " + ", ".join(f"S/ {c['monto']:.2f} al {c['fecha_venc']}" for c in cuotas) + ".")

    # ── Config APISPERU ─────────────────────────────────────────────────
    if not APISPERU_TOKEN:
        errores.append(
            "APISPERU_TOKEN no configurado. Agrega el token en las variables de entorno."
        )
    if not IPIDET_RUC:
        errores.append(
            "IPIDET_RUC no configurado. Agrega el RUC de IPIDET en las variables de entorno."
        )

    # ── IGV ─────────────────────────────────────────────────────────────
    impuestos = tributos.resumen(comp)
    if impuestos["sin_definir"]:
        errores.append("Define si cada línea lleva IGV, es exonerada o inafecta (edita el comprobante).")
    if abs(impuestos["descuadre"]) >= 0.01:
        errores.append(f"Las líneas suman S/ {impuestos['total']:.2f} y el comprobante dice S/ {monto:.2f}.")
    if impuestos["gravadas"]:
        if not catalogos.get_facturacion().get("emitir_gravados"):
            errores.append(
                "La emisión con IGV todavía no está habilitada. Emítelo en el portal SOL e importa su XML, o "
                "pide a un administrador habilitarla en Configuración → Facturación e IGV tras la revisión del contador.")
        if tipo == "factura" and monto > tributos.DETRACCION_DESDE:
            errores.append(
                f"Una factura con IGV de más de S/ {tributos.DETRACCION_DESDE} puede estar sujeta a detracción, "
                "que la plataforma no informa. Emítela en el portal SOL e importa su XML.")

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
            "impuestos":          impuestos,
            "cuotas":             cuotas,
            "fecha_emision":      fecha,
            "destinatario":       destinatario_display,
            "ultimo_emitido":     ultimo_correlativo,
            "ultimo_ambiente":    ultimo_ambiente,
            "siguiente_esperado": siguiente_esperado,
            "ambiente_actual":    SUNAT_ENVIRONMENT,
        },
    }


def check_configured() -> bool:
    """Returns True if APISPERU integration is properly configured."""
    return bool(APISPERU_TOKEN and IPIDET_RUC)
