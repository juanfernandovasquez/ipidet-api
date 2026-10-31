from datetime import datetime
from bson import ObjectId
from config import catalogos
from webapp.mongo_client import db as _db


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

members_col = _db.members
payments_col = _db.payments
productos_col = _db.productos


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


# ── Productos de WooCommerce ──────────────────────────────────────────────────
#
# La correspondencia producto WC ↔ producto IPIDET vive en la colección `productos`
# (campo wc_product_id, editable en /productos). Para vender un período nuevo basta
# crear ahí el producto con su ID de WooCommerce; no hay que tocar código.
#
#   tipo cuota_anual / cuota_provincia → marca el pago del período como pagado
#   tipo fraccionamiento               → registra una cuota pagada
#   periodo vacío                      → producto genérico: se aplica al período
#                                        pendiente más antiguo del socio

_UBICACION_POR_TIPO = {"cuota_anual": "lima", "cuota_provincia": "provincia"}


def _accion_de_producto(tipo: str) -> str | None:
    if tipo in _UBICACION_POR_TIPO:
        return "pagar"
    if tipo == "fraccionamiento":
        return "fraccionamiento"
    return None


def get_wc_product_map() -> dict:
    """{wc_product_id: {action, descripcion, periodo, ubicacion, producto_id}}"""
    docs = productos_col.find({"wc_product_id": {"$nin": [None, "", 0]}})
    # Si dos productos comparten ID de WC gana el activo y, a igualdad, el genérico.
    orden = sorted(docs, key=lambda d: (not d.get("activo"), d.get("periodo") or ""))
    mapa: dict = {}
    for d in orden:
        mapa.setdefault(d["wc_product_id"], {
            "action":      _accion_de_producto(d.get("tipo", "")),
            "descripcion": d.get("nombre", ""),
            "periodo":     d.get("periodo") or None,
            "ubicacion":   _UBICACION_POR_TIPO.get(d.get("tipo", "")),
            "producto_id": str(d["_id"]),
        })
    return mapa


def get_portal_products() -> dict:
    """{"lima": {periodo: wc_id}, "provincia": {periodo: wc_id}} — botones de pago del portal."""
    salida: dict = {"lima": {}, "provincia": {}}
    docs = productos_col.find({
        "activo": True,
        "tipo": {"$in": list(_UBICACION_POR_TIPO)},
        "wc_product_id": {"$nin": [None, "", 0]},
        "periodo": {"$nin": [None, ""]},
    })
    for d in docs:
        salida[_UBICACION_POR_TIPO[d["tipo"]]][d["periodo"]] = d["wc_product_id"]
    return salida


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
    wc_map = get_wc_product_map()
    periodos_activos = catalogos.get_periodos_cobro()

    for item in order.get("line_items", []):
        product_id = item.get("product_id")
        cfg = wc_map.get(product_id)
        if not cfg or not cfg["action"]:
            results.append({"product_id": product_id, "skipped": True, "reason": "producto no mapeado"})
            continue

        # Si el producto tiene periodo fijo, lo usamos directamente.
        # Si no, buscamos el año pendiente más antiguo del socio entre los períodos activos.
        # Esto permite que un producto genérico (ej. "Pago Ordinario") marque
        # el año correcto aunque el botón diga "Pagar 2025" o "Pagar 2026".
        if cfg.get("periodo"):
            periodo = cfg["periodo"]
        else:
            periodos_pendientes = sorted([
                p["periodo"] for p in payments_col.find({
                    "member_id": member_id,
                    "periodo":   {"$in": periodos_activos},
                    "estado":    {"$in": list(ESTADOS_PENDIENTES)},
                })
            ])
            periodo = periodos_pendientes[0] if periodos_pendientes else anio_pago_default
        fecha_pago = fecha_pago_default

        payment = payments_col.find_one({"member_id": member_id, "periodo": periodo})

        if cfg["action"] == "pagar":
            if payment:
                pagado = {
                    "estado": "pagado",
                    "fecha_pago": fecha_pago,
                    "medio_pago": "WooCommerce",
                    "pagado_por": f"WC#{order_id}",
                }
                # El producto comprado es el del pago, salvo que sea genérico (sin período).
                if cfg["periodo"]:
                    pagado["producto_id"] = cfg["producto_id"]
                payments_col.update_one(
                    {"member_id": member_id, "periodo": periodo},
                    {"$set": pagado},
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
                    "producto_id": cfg["producto_id"],
                    "raw_original": cfg["descripcion"],
                    "cuotas": [],
                })
            results.append({"product_id": product_id, "action": "pagado", "periodo": periodo})

        elif cfg["action"] == "fraccionamiento":
            monto = float(item.get("total", 0))
            if payment:
                cuotas = payment.get("cuotas", [])
                numero = max((c.get("numero", 0) for c in cuotas), default=0) + 1
                nueva_cuota = {
                    "numero": numero,
                    "monto": monto,
                    "fecha_pago": fecha_pago,
                    "fecha_venc": None,
                    "estado": "pagado",
                    "medio_pago": "WooCommerce",
                    "pagado_por": f"WC#{order_id}",
                    "producto_nombre": cfg.get("descripcion") or None,
                    "link_constancia": None,
                }
                payments_col.update_one(
                    {"member_id": member_id, "periodo": periodo},
                    {"$push": {"cuotas": nueva_cuota}},
                )
                # Sync estado through proper channel (R3)
                pay_doc = payments_col.find_one(
                    {"member_id": member_id, "periodo": periodo},
                    {"_id": 1, "estado": 1, "cuotas": 1},
                )
                if pay_doc and pay_doc.get("cuotas") and pay_doc.get("estado") != "fraccionamiento":
                    payments_col.update_one(
                        {"_id": pay_doc["_id"]},
                        {"$set": {"estado": "fraccionamiento"}},
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
                    "producto_id": cfg["producto_id"],
                    "raw_original": cfg["descripcion"],
                    "cuotas": [{
                        "numero": 1,
                        "monto": monto,
                        "fecha_pago": fecha_pago,
                        "fecha_venc": None,
                        "estado": "pagado",
                        "medio_pago": "WooCommerce",
                        "pagado_por": f"WC#{order_id}",
                        "producto_nombre": cfg.get("descripcion") or None,
                        "link_constancia": None,
                    }],
                })
            results.append({"product_id": product_id, "action": "cuota_registrada", "periodo": periodo})

    return {"ok": True, "member_id": member_id, "results": results}
