from pymongo import MongoClient
from config.settings import MONGODB_URI, DB_NAME

_client = MongoClient(MONGODB_URI)
_db = _client[DB_NAME]

members_col = _db.members
payments_col = _db.payments


def get_member_by_email(email: str) -> dict | None:
    email = email.strip().lower()
    member = members_col.find_one({"emails.email": {"$regex": f"^{email}$", "$options": "i"}})
    if not member:
        return None

    member["_id"] = str(member["_id"])
    payments = list(payments_col.find({"member_id": member["member_id"]}).sort("periodo", -1))
    for p in payments:
        p["_id"] = str(p["_id"])
        cuotas = p.get("cuotas", [])
        p["cuotas_total"] = len(cuotas)
        p["cuotas_pagadas"] = sum(1 for c in cuotas if c.get("estado") == "pagado")

    member["payments"] = payments
    return member


# ── Mapeo de productos WooCommerce → acción en MongoDB ────────────────────────
# product_id → (estado, descripcion)
WC_PRODUCT_MAP = {
    8882:  {"action": "pagar",         "descripcion": "Pago Ordinario"},
    19105: {"action": "pagar",         "descripcion": "Cuota anual provincia"},
    8880:  {"action": "fraccionamiento","descripcion": "Fraccionamiento en 3 meses"},
}


def apply_woocommerce_order(email: str, order: dict) -> dict:
    """
    Procesa un pedido WooCommerce completado y actualiza MongoDB.
    Devuelve un dict con el resultado de cada item procesado.
    """
    email = email.strip().lower()
    member = members_col.find_one(
        {"emails.email": {"$regex": f"^{email}$", "$options": "i"}},
        {"member_id": 1}
    )
    if not member:
        return {"ok": False, "error": f"No se encontró socio con email {email}"}

    member_id = member["member_id"]
    from datetime import datetime, timezone
    periodo = str(order.get("date_paid", "")[:4]) or str(datetime.now(timezone.utc).year)
    fecha_pago = order.get("date_paid", "")[:10]  # YYYY-MM-DD
    order_id = str(order.get("id", ""))
    results = []

    for item in order.get("line_items", []):
        product_id = item.get("product_id")
        cfg = WC_PRODUCT_MAP.get(product_id)
        if not cfg:
            results.append({"product_id": product_id, "skipped": True, "reason": "producto no mapeado"})
            continue

        payment = payments_col.find_one({"member_id": member_id, "periodo": periodo})

        if cfg["action"] == "pagar":
            if payment:
                payments_col.update_one(
                    {"member_id": member_id, "periodo": periodo},
                    {"$set": {
                        "estado": "pagado",
                        "fecha_pago": fecha_pago,
                        "medio_pago": "WooCommerce",
                        "pagado_por": f"WC#{order_id}",
                    }}
                )
            else:
                payments_col.insert_one({
                    "member_id": member_id,
                    "periodo": periodo,
                    "estado": "pagado",
                    "fecha_pago": fecha_pago,
                    "medio_pago": "WooCommerce",
                    "pagado_por": f"WC#{order_id}",
                    "empresa_pagadora": None,
                    "raw_original": cfg["descripcion"],
                    "cuotas": [],
                })
            results.append({"product_id": product_id, "action": "pagado", "periodo": periodo})

        elif cfg["action"] == "fraccionamiento":
            monto = float(item.get("total", 0))
            if payment:
                cuotas = payment.get("cuotas", [])
                numero = max((c.get("numero", 0) for c in cuotas), default=0) + 1
                payments_col.update_one(
                    {"member_id": member_id, "periodo": periodo},
                    {
                        "$push": {"cuotas": {
                            "numero": numero,
                            "monto": monto,
                            "fecha_pago": fecha_pago,
                            "fecha_venc": None,
                            "estado": "pagado",
                        }},
                        "$set": {"estado": "fraccionamiento"},
                    }
                )
            else:
                payments_col.insert_one({
                    "member_id": member_id,
                    "periodo": periodo,
                    "estado": "fraccionamiento",
                    "fecha_pago": None,
                    "medio_pago": "WooCommerce",
                    "pagado_por": f"WC#{order_id}",
                    "empresa_pagadora": None,
                    "raw_original": cfg["descripcion"],
                    "cuotas": [{
                        "numero": 1,
                        "monto": monto,
                        "fecha_pago": fecha_pago,
                        "fecha_venc": None,
                        "estado": "pagado",
                    }],
                })
            results.append({"product_id": product_id, "action": "cuota_registrada", "periodo": periodo})

    return {"ok": True, "member_id": member_id, "results": results}
