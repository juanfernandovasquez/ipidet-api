from datetime import datetime
from bson import ObjectId
from pymongo import MongoClient
from config.settings import MONGODB_URI, DB_NAME


def _clean(obj):
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    if isinstance(obj, ObjectId):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj

import certifi
_client = MongoClient(MONGODB_URI, tlsCAFile=certifi.where())
_db = _client[DB_NAME]

members_col = _db.members
payments_col = _db.payments


def get_member_by_email(email: str) -> dict | None:
    email = email.strip().lower()
    member = members_col.find_one({"emails.email": {"$regex": f"^{email}$", "$options": "i"}})
    if not member:
        return None

    payments = list(payments_col.find({"member_id": member["member_id"]}).sort("periodo", -1))
    for p in payments:
        cuotas = p.get("cuotas", [])
        p["cuotas_total"] = len(cuotas)
        p["cuotas_pagadas"] = sum(1 for c in cuotas if c.get("estado") == "pagado")

    member["payments"] = payments
    return _clean(member)


# ── Mapeo de productos WooCommerce → acción en MongoDB ────────────────────────
#
# INSTRUCCIONES PARA AGREGAR NUEVOS PRODUCTOS:
# 1. En WooCommerce Admin → Productos → Añadir nuevo, crea un producto por cada año y tipo.
# 2. Al guardar, anota el ID del producto (aparece en la URL: post=XXXXX).
# 3. Agrega ese ID aquí con el periodo y action correspondientes.
#
# CAMPOS:
#   action   : "pagar" | "fraccionamiento"
#   periodo  : "2025" | "2026" | "2027" | None  (None = usar año de la fecha de pago)
#   ubicacion: "lima" | "provincia" | None       (para filtrar botones de pago en portal)
#
WC_PRODUCT_MAP = {
    # ── Productos existentes (sin periodo fijo → usa año del pago) ────────────
    8882:  {"action": "pagar",          "descripcion": "Pago Ordinario Lima",      "periodo": None, "ubicacion": "lima"},
    19105: {"action": "pagar",          "descripcion": "Cuota anual provincia",    "periodo": None, "ubicacion": "provincia"},
    8880:  {"action": "fraccionamiento","descripcion": "Fraccionamiento 3 cuotas", "periodo": None, "ubicacion": None},

    # ── NUEVOS: un producto por año — COMPLETAR con IDs reales de WooCommerce ─
    # Lima
    # REEMPLAZA los números de abajo con los IDs que obtengas en WC Admin
    # EJEMPLO: 22001: {"action": "pagar", "descripcion": "Cuota Lima 2025", "periodo": "2025", "ubicacion": "lima"},
    # 22001: {"action": "pagar", "descripcion": "Cuota Lima 2025",     "periodo": "2025", "ubicacion": "lima"},
    # 22002: {"action": "pagar", "descripcion": "Cuota Lima 2026",     "periodo": "2026", "ubicacion": "lima"},
    # 22003: {"action": "pagar", "descripcion": "Cuota Lima 2027",     "periodo": "2027", "ubicacion": "lima"},

    # Provincia
    # 22004: {"action": "pagar", "descripcion": "Cuota Provincia 2025","periodo": "2025", "ubicacion": "provincia"},
    # 22005: {"action": "pagar", "descripcion": "Cuota Provincia 2026","periodo": "2026", "ubicacion": "provincia"},
    # 22006: {"action": "pagar", "descripcion": "Cuota Provincia 2027","periodo": "2027", "ubicacion": "provincia"},

    # Fraccionamiento por año (si quieres uno específico por año):
    # 22010: {"action": "fraccionamiento", "descripcion": "Fracc. Lima 2026",     "periodo": "2026", "ubicacion": "lima"},
    # 22011: {"action": "fraccionamiento", "descripcion": "Fracc. Provincia 2026","periodo": "2026", "ubicacion": "provincia"},
}

# ── Mapa de botones de pago para el portal de WordPress ──────────────────────
# Aquí pones el product_id de WC para cada año y tipo de socio.
# El widget de WordPress usará estos IDs para generar los botones "Pagar XXXX".
# Cuando no hay ID (None) no se muestra botón para ese año.
WC_PORTAL_PRODUCTS = {
    # Usa los productos genéricos publicados hasta que se creen versiones por año.
    # Si en /productos se configura un wc_product_id para un año específico,
    # ese valor tiene prioridad (override desde MongoDB).
    "lima": {
        "2025": 8882,   # Pago Ordinario (genérico Lima) — ID WC real
        "2026": 8882,
        "2027": 8882,
    },
    "provincia": {
        "2025": 19105,  # Cuota anual provincia (genérica) — ID WC real
        "2026": 19105,
        "2027": 19105,
    },
}

PERIODOS_ACTIVOS = ["2025", "2026", "2027"]

ESTADOS_PENDIENTES = {"debe", "pendiente", "sin_registro", "parcial"}


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
    fecha_pago_default = order.get("date_paid", "")[:10]  # YYYY-MM-DD
    anio_pago_default  = str(order.get("date_paid", "")[:4]) or str(datetime.now(timezone.utc).year)
    order_id = str(order.get("id", ""))
    results = []

    for item in order.get("line_items", []):
        product_id = item.get("product_id")
        cfg = WC_PRODUCT_MAP.get(product_id)
        if not cfg:
            results.append({"product_id": product_id, "skipped": True, "reason": "producto no mapeado"})
            continue

        # Si el producto tiene periodo fijo, lo usamos directamente.
        # Si no, buscamos el año pendiente más antiguo del socio en PERIODOS_ACTIVOS.
        # Esto permite que un producto genérico (ej. "Pago Ordinario") marque
        # el año correcto aunque el botón diga "Pagar 2025" o "Pagar 2026".
        if cfg.get("periodo"):
            periodo = cfg["periodo"]
        else:
            periodos_pendientes = sorted([
                p["periodo"] for p in payments_col.find({
                    "member_id": member_id,
                    "periodo":   {"$in": PERIODOS_ACTIVOS},
                    "estado":    {"$in": list(ESTADOS_PENDIENTES)},
                })
            ])
            periodo = periodos_pendientes[0] if periodos_pendientes else anio_pago_default
        fecha_pago = fecha_pago_default

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
