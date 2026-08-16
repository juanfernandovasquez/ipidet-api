import base64
import hashlib
import hmac
import json
from fastapi import Request
from fastapi.responses import JSONResponse
from config.settings import WC_WEBHOOK_SECRET
import webapp.portal_db as pdb

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
    member = pdb.get_member_by_email(email)
    if not member:
        return {"found": False, "email": email}

    payments_out = []
    for p in member.get("payments", []):
        cuotas = p.get("cuotas", [])
        payments_out.append({
            "periodo":        p.get("periodo", ""),
            "estado":         p.get("estado", ""),
            "estado_label":   STATUS_ES.get(p.get("estado", ""), p.get("estado", "")),
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
        })

    return {
        "found":       True,
        "member_id":   member.get("member_id", ""),
        "nombre":      f"{member.get('nombres', '')} {member.get('apellidos', '')}".strip(),
        "titulo":      member.get("titulo", ""),
        "ubicacion":   member.get("ubicacion", ""),
        "estado":      member.get("estado", ""),
        "estado_label": "Activo" if member.get("estado") == "activo" else member.get("estado", "").capitalize(),
        "payments":    payments_out,
    }


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
    return result
