import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta
from fastapi import Request
from fastapi.responses import JSONResponse
from config.settings import WC_WEBHOOK_SECRET
import webapp.portal_db as pdb

# ── Caché en memoria para respuestas del portal (2 min TTL) ──────────────────
_cache: dict[str, tuple] = {}  # email → (data, expires_at)
_CACHE_TTL = timedelta(minutes=2)

def _cache_get(email: str) -> dict | None:
    entry = _cache.get(email)
    if entry and datetime.now() < entry[1]:
        return entry[0]
    return None

def _cache_set(email: str, data: dict):
    _cache[email] = (data, datetime.now() + _CACHE_TTL)

def _cache_del(email: str):
    _cache.pop(email, None)

STATUS_ES = {
    "pagado":          "Pagado",
    "debe":            "Debe",
    "fraccionamiento": "Fraccionamiento",
    "exonerado":       "Exonerado",
    "no_aplica":       "N/A",
    "pendiente":       "Pendiente",
    "retirar":         "Retirar",
    "en_revision":     "En revisión",
    "revisar":         "Revisar",
}


def build_member_status(email: str) -> dict:
    email = email.strip().lower()
    cached = _cache_get(email)
    if cached:
        return cached

    member = pdb.get_member_by_email(email)
    if not member:
        return {"found": False, "email": email}

    # Determinar tipo de socio para los botones de pago.
    # tipo_socio="filial" → cuota de filial (provincia); cualquier otro → cuota ordinaria (Lima).
    ubicacion = member.get("ubicacion", "")
    tipo_socio = member.get("tipo_socio", "ordinario")
    ubicacion_key = "provincia" if tipo_socio == "filial" else "lima"
    # Usar el dict estático directamente — siempre sincronizado con los IDs reales de WC.
    wc_products = pdb.WC_PORTAL_PRODUCTS

    payments_out = []
    periodos_con_registro = set()

    for p in member.get("payments", []):
        cuotas = p.get("cuotas", [])
        estado = p.get("estado", "")
        periodo = p.get("periodo", "")
        periodos_con_registro.add(periodo)

        payment_entry = {
            "periodo":        periodo,
            "estado":         estado,
            "estado_label":   STATUS_ES.get(estado, estado),
            "empresa_pagadora": p.get("empresa_pagadora") or "",
            "fecha_pago":     p.get("fecha_pago") or "",
            "cuotas_total":   p.get("cuotas_total", 0),
            "cuotas_pagadas": p.get("cuotas_pagadas", 0),
            "cuotas": [
                {
                    "numero":     c.get("numero"),
                    "monto":      c.get("monto"),
                    "fecha_venc": c.get("fecha_venc") or "",
                    "fecha_pago": c.get("fecha_pago") or "",
                    "estado":     c.get("estado", "pendiente"),
                }
                for c in cuotas
            ],
        }

        # Agregar botón de pago si el período está pendiente y hay producto WC configurado
        if estado in pdb.ESTADOS_PENDIENTES and periodo in pdb.PERIODOS_ACTIVOS:
            wc_id = wc_products.get(ubicacion_key, {}).get(periodo)
            if wc_id:
                payment_entry["wc_product_id"] = wc_id
                payment_entry["wc_pay_url"] = f"https://ipidet.org/carrito/?add-to-cart={wc_id}"

        payments_out.append(payment_entry)

    # Agregar períodos activos sin registro (nunca pagaron ese año)
    for periodo in pdb.PERIODOS_ACTIVOS:
        if periodo not in periodos_con_registro:
            wc_id = wc_products.get(ubicacion_key, {}).get(periodo)
            entry = {
                "periodo":          periodo,
                "estado":           "sin_registro",
                "estado_label":     "Sin pago registrado",
                "empresa_pagadora": "",
                "fecha_pago":       "",
                "cuotas_total":     0,
                "cuotas_pagadas":   0,
                "cuotas":           [],
            }
            if wc_id:
                entry["wc_product_id"] = wc_id
                entry["wc_pay_url"] = f"https://ipidet.org/carrito/?add-to-cart={wc_id}"
            payments_out.append(entry)

    # Ordenar por período descendente
    payments_out.sort(key=lambda p: p["periodo"], reverse=True)

    result = {
        "found":        True,
        "member_id":    member.get("member_id", ""),
        "nombre":       f"{member.get('nombres', '')} {member.get('apellidos', '')}".strip(),
        "titulo":       member.get("titulo", ""),
        "ubicacion":    ubicacion,
        "estado":       member.get("estado", ""),
        "estado_label": "Activo" if member.get("estado") == "activo" else member.get("estado", "").capitalize(),
        "payments":     payments_out,
    }
    _cache_set(email, result)
    return result


def update_alternative_email(primary_email: str, alternative_email: str) -> dict:
    member = pdb.members_col.find_one(
        {"emails.email": {"$regex": f"^{primary_email}$", "$options": "i"}},
        {"member_id": 1, "emails": 1},
    )
    if not member:
        return {"ok": False, "error": f"Socio no encontrado con email {primary_email}"}

    emails = member.get("emails", [])
    # Buscar si ya hay un email alternativo (no principal) habilitado
    alt_idx = next(
        (i for i, e in enumerate(emails) if not e.get("principal") and e.get("estado") == "habilitado"),
        None,
    )
    if alt_idx is not None:
        pdb.members_col.update_one(
            {"member_id": member["member_id"]},
            {"$set": {f"emails.{alt_idx}.email": alternative_email}},
        )
    else:
        pdb.members_col.update_one(
            {"member_id": member["member_id"]},
            {"$push": {"emails": {"email": alternative_email, "estado": "habilitado", "principal": False}}},
        )
    _cache_del(primary_email)
    return {"ok": True, "member_id": member["member_id"], "alternative_email": alternative_email}


async def handle_wc_webhook(request: Request):
    body = await request.body()

    # Verificar firma HMAC-SHA256 de WooCommerce
    if WC_WEBHOOK_SECRET:
        sig_header = request.headers.get("x-wc-webhook-signature", "")
        expected_sig = hmac.new(
            WC_WEBHOOK_SECRET.encode("utf-8"),
            body,
            hashlib.sha256,
        ).digest()
        expected_b64 = base64.b64encode(expected_sig).decode()
        if not hmac.compare_digest(expected_b64, sig_header):
            return JSONResponse({"error": "Firma inválida"}, status_code=401)

    try:
        order = json.loads(body)
    except Exception:
        return JSONResponse({"error": "JSON inválido"}, status_code=400)

    status = order.get("status", "")
    if status not in ("completed", "processing"):
        return {"ok": True, "skipped": True, "reason": f"estado ignorado: {status}"}

    email = order.get("billing", {}).get("email", "")
    if not email:
        return JSONResponse({"error": "orden sin email de facturación"}, status_code=400)

    result = pdb.apply_woocommerce_order(email, order)
    if result.get("ok"):
        _cache_del(email.strip().lower())
    return result
