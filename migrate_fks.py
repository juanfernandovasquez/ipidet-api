"""
migrate_fks.py — Backfill de FKs en documentos históricos de MongoDB.

Qué hace:
  1. payments.comprobante_id       ← busca comprobantes.numero == payments.num_comprobante
  2. payments.empresa_id           ← busca companies.nombre == payments.empresa_pagadora
  3. payments.cuotas[].comprobante_id   ← busca comprobantes.numero == cuota.num_comprobante
  4. payments.pagos_parciales[].comprobante_id ← busca comprobantes.numero == parcial.num_comprobante
  5. comprobantes.empresa_id       ← busca companies.nombre == comprobantes.empresa
  6. comprobantes.items[].payment_id + tipo_pago + cuota_numero
     ← vía member_id + producto_id/nombre → periodo → payment (SIN cambiar estados)
  7. facturas_credito.empresa_id   ← busca companies.nombre == credito.empresa
  8. payments.producto_id          ← vía members.ubicacion → productos({tipo, periodo})

Seguro:
  - Solo escribe campos que AÚN NO EXISTEN en el documento (idempotente).
  - No toca: estado, monto, fecha_pago, ni ningún campo existente.
  - Se puede correr múltiples veces sin efecto duplicado.

Uso:
  python migrate_fks.py
"""

import re
import sys
from bson import ObjectId
import certifi
from pymongo import MongoClient
from config.settings import MONGODB_URI, DB_NAME

client = MongoClient(MONGODB_URI, tlsCAFile=certifi.where())
db = client[DB_NAME]

payments_col     = db.payments
comprobantes_col = db.comprobantes
companies_col    = db.companies
productos_col    = db.productos
members_col      = db.members
credito_col      = db.facturas_credito

# ── helpers ───────────────────────────────────────────────────────────────────

def _find_empresa_id(nombre: str) -> str | None:
    if not nombre:
        return None
    # 1. Exact match (nombre o razon_social)
    doc = companies_col.find_one(
        {"$or": [
            {"nombre":       {"$regex": f"^{re.escape(nombre)}$", "$options": "i"}},
            {"razon_social": {"$regex": f"^{re.escape(nombre)}$", "$options": "i"}},
        ]},
        {"_id": 1},
    )
    if doc:
        return str(doc["_id"])
    # 2. Prefix match — solo si hay exactamente UNA empresa que empieza con ese nombre
    matches = list(companies_col.find(
        {"$or": [
            {"nombre":       {"$regex": f"^{re.escape(nombre)}", "$options": "i"}},
            {"razon_social": {"$regex": f"^{re.escape(nombre)}", "$options": "i"}},
        ]},
        {"_id": 1},
    ))
    if len(matches) == 1:
        return str(matches[0]["_id"])
    # Ambiguous or no match
    return None


def _find_comprobante_id(numero: str) -> str | None:
    if not numero:
        return None
    doc = comprobantes_col.find_one({"numero": numero}, {"_id": 1})
    return str(doc["_id"]) if doc else None


# ── 1 & 2: payments principales ───────────────────────────────────────────────

def migrate_payments():
    updated = 0
    for pay in payments_col.find({}):
        pid = pay["_id"]
        upd = {}

        # 1. comprobante_id en pago principal
        if pay.get("num_comprobante") and not pay.get("comprobante_id"):
            cid = _find_comprobante_id(pay["num_comprobante"])
            if cid:
                upd["comprobante_id"] = cid

        # 2. empresa_id en pago principal
        if pay.get("empresa_pagadora") and not pay.get("empresa_id"):
            eid = _find_empresa_id(pay["empresa_pagadora"])
            if eid:
                upd["empresa_id"] = eid

        if upd:
            payments_col.update_one({"_id": pid}, {"$set": upd})
            updated += 1

    print(f"  payments principales:  {updated} documentos actualizados")


# ── 3: cuotas[].comprobante_id ────────────────────────────────────────────────

def migrate_cuotas():
    updated_docs = 0
    updated_cuotas = 0
    for pay in payments_col.find({"cuotas": {"$exists": True, "$ne": []}}):
        pid = pay["_id"]
        cuotas = pay.get("cuotas") or []
        changed = False
        for i, c in enumerate(cuotas):
            if c.get("num_comprobante") and not c.get("comprobante_id"):
                cid = _find_comprobante_id(c["num_comprobante"])
                if cid:
                    payments_col.update_one(
                        {"_id": pid, "cuotas.numero": c["numero"]},
                        {"$set": {"cuotas.$.comprobante_id": cid}},
                    )
                    updated_cuotas += 1
                    changed = True
        if changed:
            updated_docs += 1

    print(f"  cuotas:                {updated_cuotas} cuotas en {updated_docs} pagos actualizadas")


# ── 4: pagos_parciales[].comprobante_id ──────────────────────────────────────

def migrate_parciales():
    updated_docs = 0
    updated_parciales = 0
    for pay in payments_col.find({"pagos_parciales": {"$exists": True, "$ne": []}}):
        pid = pay["_id"]
        parciales = pay.get("pagos_parciales") or []
        changed = False
        for pp in parciales:
            if pp.get("num_comprobante") and not pp.get("comprobante_id"):
                cid = _find_comprobante_id(pp["num_comprobante"])
                if cid:
                    payments_col.update_one(
                        {"_id": pid, "pagos_parciales.numero": pp["numero"]},
                        {"$set": {"pagos_parciales.$.comprobante_id": cid}},
                    )
                    updated_parciales += 1
                    changed = True
        if changed:
            updated_docs += 1

    print(f"  pagos parciales:       {updated_parciales} parciales en {updated_docs} pagos actualizados")


# ── 5: comprobantes.empresa_id ────────────────────────────────────────────────

def migrate_comprobantes_empresa():
    updated = 0
    for comp in comprobantes_col.find({"empresa": {"$exists": True, "$ne": ""},
                                        "empresa_id": {"$exists": False}}):
        eid = _find_empresa_id(comp.get("empresa", ""))
        if eid:
            comprobantes_col.update_one({"_id": comp["_id"]}, {"$set": {"empresa_id": eid}})
            updated += 1

    print(f"  comprobantes empresa:  {updated} documentos actualizados")


# ── 6: comprobantes.items[].payment_id + tipo_pago + cuota_numero ────────────

def migrate_comprobante_items():
    """
    Infiere payment_id para items que tienen member_id + producto_nombre.
    Hace el mismo matching que sync_comprobante_to_payments PERO solo escribe
    los campos FK en el item del comprobante — NO toca estados ni montos en payments.
    """
    # Pre-carga productos
    all_prods = list(productos_col.find(
        {}, {"nombre": 1, "tipo": 1, "periodo": 1, "codigo_sunat": 1, "cuota_numero": 1}
    ))
    prod_by_code = {p["codigo_sunat"]: p for p in all_prods if p.get("codigo_sunat")}
    prod_by_name = {}
    for p in all_prods:
        k = (p.get("nombre") or "").lower().strip()
        if k:
            prod_by_name[k] = p

    TIPOS_CUOTA    = ("cuota_anual", "cuota_provincia")
    TIPOS_FRACCION = ("fraccionamiento",)
    TIPOS_PARCIAL  = ("parcial",)

    updated_comps  = 0
    updated_items  = 0
    skipped_no_prod = 0
    skipped_no_pay  = 0

    for comp in comprobantes_col.find({"items": {"$exists": True, "$ne": []}}):
        items = comp.get("items") or []
        new_items = list(items)
        changed = False

        for idx, item in enumerate(new_items):
            # Skip if already has FK
            if item.get("payment_id"):
                continue

            mid    = item.get("member_id")
            pcode  = (item.get("codigo_sunat") or "").strip()
            pname  = (item.get("producto_nombre") or "").strip()

            if not mid:
                continue

            # Resolve product
            prod = prod_by_code.get(pcode) if pcode else None
            if not prod and pname:
                prod = prod_by_name.get(pname.lower())
            if not prod:
                skipped_no_prod += 1
                continue

            tipo_prod = prod.get("tipo", "")
            periodo   = (prod.get("periodo") or "").strip()
            cuota_num = prod.get("cuota_numero")

            if not periodo:
                skipped_no_prod += 1
                continue

            # Find payment
            pay = payments_col.find_one(
                {"member_id": mid, "periodo": periodo},
                {"_id": 1, "cuotas": 1},
            )
            if not pay:
                skipped_no_pay += 1
                continue

            payment_id = str(pay["_id"])

            # Determine tipo_pago
            if tipo_prod in TIPOS_CUOTA:
                tipo_pago    = "principal"
                cuota_numero = None
            elif tipo_prod in TIPOS_FRACCION:
                tipo_pago    = "cuota"
                cuota_numero = cuota_num
            elif tipo_prod in TIPOS_PARCIAL:
                tipo_pago    = "parcial"
                cuota_numero = None
            else:
                skipped_no_prod += 1
                continue

            new_items[idx] = dict(item)
            new_items[idx]["payment_id"]   = payment_id
            new_items[idx]["tipo_pago"]    = tipo_pago
            new_items[idx]["cuota_numero"] = cuota_numero
            changed = True
            updated_items += 1

        if changed:
            comprobantes_col.update_one(
                {"_id": comp["_id"]},
                {"$set": {"items": new_items}},
            )
            updated_comps += 1

    print(f"  comprobante items:     {updated_items} items en {updated_comps} comprobantes actualizados")
    if skipped_no_prod:
        print(f"    (sin producto/período coincidente: {skipped_no_prod} items)")
    if skipped_no_pay:
        print(f"    (sin payment encontrado: {skipped_no_pay} items)")


# ── 7: facturas_credito.empresa_id ───────────────────────────────────────────

def migrate_facturas_credito_empresa():
    updated = 0
    for fac in credito_col.find({"empresa": {"$exists": True, "$ne": ""},
                                  "empresa_id": {"$exists": False}}):
        eid = _find_empresa_id(fac.get("empresa", ""))
        if eid:
            credito_col.update_one({"_id": fac["_id"]}, {"$set": {"empresa_id": eid}})
            updated += 1

    print(f"  facturas credito:      {updated} documentos actualizados")


# ── 8: payments.producto_id ──────────────────────────────────────────────────

def migrate_payments_producto_id():
    """
    Asigna producto_id a payments que no lo tienen.
    Lima → cuota_anual; demás ubicaciones → cuota_provincia.
    Solo escribe si el producto existe para ese periodo.
    """
    # Pre-carga todos los productos activos de tipo cuota por (tipo, periodo)
    prods = list(productos_col.find(
        {"tipo": {"$in": ["cuota_anual", "cuota_provincia"]}, "activo": True},
        {"_id": 1, "tipo": 1, "periodo": 1},
    ))
    # {("cuota_anual","2026"): "abc123", ...}
    prod_map: dict[tuple, str] = {(p["tipo"], p["periodo"]): str(p["_id"]) for p in prods if p.get("periodo")}

    # Pre-carga ubicaciones de socios
    ubicaciones: dict[str, str] = {
        m["member_id"]: (m.get("ubicacion") or "").strip().lower()
        for m in members_col.find({}, {"member_id": 1, "ubicacion": 1})
    }

    updated = 0
    skipped_no_prod = 0

    for pay in payments_col.find({"producto_id": {"$exists": False}, "periodo": {"$exists": True}}):
        periodo   = pay.get("periodo", "")
        member_id = pay.get("member_id", "")
        if not periodo or not member_id:
            continue

        ubicacion = ubicaciones.get(member_id, "")
        tipo      = "cuota_anual" if ubicacion == "lima" else "cuota_provincia"
        prod_id   = prod_map.get((tipo, periodo))

        if not prod_id:
            skipped_no_prod += 1
            continue

        payments_col.update_one({"_id": pay["_id"]}, {"$set": {"producto_id": prod_id}})
        updated += 1

    print(f"  payments producto_id:  {updated} documentos actualizados")
    if skipped_no_prod:
        print(f"    (sin producto en BD para ese periodo/ubicacion: {skipped_no_prod} payments)")


# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Migrando FKs en documentos históricos…")
    print()
    migrate_payments()
    migrate_cuotas()
    migrate_parciales()
    migrate_comprobantes_empresa()
    migrate_comprobante_items()
    migrate_facturas_credito_empresa()
    migrate_payments_producto_id()
    print()
    print("Migración completada.")
