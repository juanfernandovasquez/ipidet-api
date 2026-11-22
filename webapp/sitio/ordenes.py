"""Inscripciones y pagos de la web: colección `web_ordenes`.

Una orden es una inscripción a un evento (gratuita o de pago) o un pago de cuota que informa un socio.
  por_pagar → en_revision → confirmada            (observada vuelve a en_revision al corregir)
  anulada en cualquier momento antes de cobrar; una inscripción confirmada la puede anular el equipo.

`confirmar()` es el único camino que da un cobro por recibido (hoy el equipo; mañana también una pasarela).
Cobranzas se toca solo con las funciones de webapp.db (R3), por FK directa (R8). Aquí NUNCA se emite a SUNAT:
solo se prepara un borrador (pre_registrado).
"""
import re
import threading
from datetime import date, timedelta

from bson import Binary, ObjectId
from pymongo.errors import DuplicateKeyError

from config import catalogos
from config.mongo_client import db
from webapp import db as pdb
from webapp import tributos
from webapp import variables_correo
from webapp.sitio import config, eventos, pagos

ordenes_col = db.web_ordenes
archivos_col = db.web_archivos

ESTADOS: dict[str, tuple[str, str]] = {
    "por_pagar":   ("Por pagar", "amber"),
    "en_revision": ("Pago por verificar", "blue"),
    "observada":   ("Pago observado", "orange"),
    "confirmando": ("Confirmando", "purple"),
    "confirmada":  ("Confirmada", "green"),
    "anulada":     ("Anulada", "gray"),
}
SIN_COBRAR = ("por_pagar", "en_revision", "observada")
MAX_ARCHIVO = 4_000_000
# Desde este monto la boleta exige identificar al comprador (norma de SUNAT, no dato del negocio).
BOLETA_CON_DNI_DESDE = 700
_FIRMAS = ((b"%PDF-", "application/pdf", "pdf"), (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
           (b"\xff\xd8\xff", "image/jpeg", "jpg"))
_candado = threading.Lock()   # cupos: una sola instancia en Render


class Invalido(ValueError):
    """Error que se le puede mostrar a quien hizo la acción."""


def asegurar_indices() -> None:
    for claves, opciones in (
        ([("clave", 1)], {"unique": True, "partialFilterExpression": {"activa": True}}),
        ([("cuenta_id", 1)], {}), ([("member_id", 1)], {}), ([("email", 1)], {}),
        ([("evento_id", 1), ("activa", 1)], {}), ([("estado", 1), ("created_at", -1)], {}),
        ([("comprobante_id", 1)], {}),
    ):
        try:
            ordenes_col.create_index(claves, **opciones)
        except Exception as e:  # noqa: BLE001
            print(f"[sitio] Aviso: no se pudo crear el índice web_ordenes.{claves}: {e}")


def _hist(accion: str, usuario: str = "", detalle: str = "") -> dict:
    return {"fecha": config.ahora(), "accion": accion, "usuario": usuario or "web", "detalle": detalle}


def por_id(orden_id: str) -> dict | None:
    if not config.id_valido(orden_id):
        return None
    o = ordenes_col.find_one({"_id": ObjectId(orden_id)})
    if o:
        o["_id"] = str(o["_id"])
    return o


def _filtro_de(persona: dict) -> list[dict]:
    cond: list[dict] = [{"cuenta_id": persona["id"]}, {"email": persona["email"]}]
    if persona.get("member_id"):
        cond.append({"member_id": persona["member_id"]})
    return cond


def de_persona(persona: dict) -> list[dict]:
    filas = list(ordenes_col.find({"$or": _filtro_de(persona)}).sort("created_at", -1).limit(200))
    for o in filas:
        o["_id"] = str(o["_id"])
    return filas


def es_de(orden: dict | None, persona: dict | None) -> bool:
    if not orden or not persona or not persona.get("registrada"):
        return False
    return bool(orden.get("cuenta_id") == persona["id"] or orden.get("email") == persona["email"]
                or (persona.get("member_id") and orden.get("member_id") == persona["member_id"]))


# ── Crear ─────────────────────────────────────────────────────────────────────

def _insertar(doc: dict, usuario: str) -> tuple[dict, bool]:
    ahora = config.ahora()
    doc = {"activa": True, "moneda": "PEN", "metodo": None, "operacion": "", "fecha_pago": "",
           "voucher_id": None, "facturacion": {}, "observacion": "", "comprobante_id": None,
           "pasarela": None, **doc, "historial": [_hist("creada", usuario)],
           "created_at": ahora, "updated_at": ahora}
    try:
        _id = ordenes_col.insert_one(doc).inserted_id
    except DuplicateKeyError:
        existe = ordenes_col.find_one({"clave": doc["clave"], "activa": True})
        if not existe:
            raise Invalido("No se pudo registrar. Inténtalo de nuevo.")
        existe["_id"] = str(existe["_id"])
        return existe, False
    doc["_id"] = str(_id)
    return doc, True


def inscribir(evento_id: str, persona: dict, usuario: str = "", del_equipo: bool = False) -> tuple[dict, bool]:
    """(orden, si se creó). El precio sale del evento, nunca del navegador."""
    with _candado:
        ev = eventos.por_id(evento_id)
        if not ev:
            raise Invalido("No encontramos ese evento.")
        mid = persona.get("member_id")
        clave = f"evento:{evento_id}:{mid or persona['email']}"
        existe = ordenes_col.find_one({"activa": True, "$or": [
            {"clave": clave}, {"tipo": "evento", "evento_id": evento_id, "email": persona["email"]}]})
        if existe:
            existe["_id"] = str(existe["_id"])
            return existe, False
        ya_inscrito = bool(mid) and any(i.get("member_id") == mid for i in ev.get("inscritos") or [])
        if ya_inscrito and not del_equipo:
            raise Invalido("Ya estás inscrito en este evento.")
        if del_equipo:
            if ev.get("estado") == "cancelado":
                raise Invalido("El evento está cancelado.")
            cupo = ev.get("cupo_max")
            if cupo and not ya_inscrito and eventos.ocupados(ev) >= int(cupo):
                raise Invalido("El evento está lleno.")
        else:
            if not eventos.publicable(ev):
                raise Invalido("Este evento no está abierto en la web.")
            if eventos.estado_inscripcion(ev) != "abierta":
                raise Invalido("Las inscripciones a este evento no están abiertas.")
        if ev.get("cobro") not in eventos.COBROS:
            raise Invalido("Falta definir si el evento es gratuito o de pago.")
        monto = eventos.precio(ev, bool(mid))
        # Foto del IGV al inscribirse: si el evento cambia después, esta venta no.
        afect = eventos.afectacion(ev) if monto else ""
        tasa = catalogos.get_facturacion()["tasa_igv"] if afect == tributos.GRAVADO else None
        orden, creada = _insertar({
            "tipo_afectacion": afect, "tasa_igv": tasa,
            "tipo": "evento", "clave": clave, "evento_id": evento_id,
            "evento_fecha": ev.get("fecha", ""), "descripcion": ev.get("titulo", "Evento"),
            "cuenta_id": persona.get("id") or "", "email": persona["email"],
            "nombre": persona.get("nombre_completo") or persona["email"], "member_id": mid,
            "monto": monto, "estado": "por_pagar" if monto else "confirmando",
        }, usuario)
    if creada and not monto:
        _inscripcion_confirmada(orden)
        ordenes_col.update_one({"_id": ObjectId(orden["_id"])},
                               {"$set": {"estado": "confirmada", "confirmada_at": config.ahora()}})
        orden["estado"] = "confirmada"
    return orden, creada


def cuotas_por_pagar(member_id: str) -> list[dict]:
    """Lo que el socio debe, por período y, si fracciona, por cuota."""
    cache: dict = {}
    filas = []
    for p in pdb.payments_col.find({"member_id": member_id,
                                    "estado": {"$in": list(catalogos.ESTADOS_CON_DEUDA)}}).sort("periodo", -1):
        base = {"payment_id": str(p["_id"]), "periodo": p.get("periodo", ""), "estado": p.get("estado")}
        if p.get("estado") == "fraccionamiento":
            for c in sorted(p.get("cuotas") or [], key=lambda c: c.get("numero", 0)):
                if c.get("estado") != "pagado" and c.get("monto"):
                    filas.append({**base, "cuota_numero": c.get("numero"), "monto": round(float(c["monto"]), 2),
                                  "vence": c.get("fecha_venc") or "",
                                  "descripcion": f"Cuota {c.get('numero')} del fraccionamiento {base['periodo']}"})
        else:
            saldo = variables_correo.saldo_de_pago(p, cache)
            filas.append({**base, "cuota_numero": None, "monto": saldo, "vence": "",
                          "descripcion": f"Cuota anual {base['periodo']}"
                                         + (" (saldo)" if p.get("estado") == "parcial" else "")})
    return filas


def pagar_cuota(payment_id: str, cuota_numero, persona: dict) -> tuple[dict, bool]:
    if not persona.get("member_id"):
        raise Invalido("Las cuotas solo las pagan desde aquí los socios activos.")
    fila = next((f for f in cuotas_por_pagar(persona["member_id"])
                 if f["payment_id"] == payment_id and str(f["cuota_numero"] or "") == str(cuota_numero or "")), None)
    if not fila:
        raise Invalido("Esa cuota ya no figura como pendiente.")
    if fila["monto"] <= 0:
        raise Invalido("El monto de esa cuota todavía no está cargado. Escríbenos y te lo confirmamos.")
    return _insertar({
        "tipo": "cuota", "clave": f"cuota:{payment_id}:{fila['cuota_numero'] or 0}",
        "payment_id": payment_id, "cuota_numero": fila["cuota_numero"], "periodo": fila["periodo"],
        "descripcion": fila["descripcion"], "cuenta_id": persona["id"], "email": persona["email"],
        "nombre": persona["nombre_completo"], "member_id": persona["member_id"],
        "monto": fila["monto"], "estado": "por_pagar",
    }, persona["email"])


# ── Informar el pago ──────────────────────────────────────────────────────────

def _facturacion(datos: dict, monto: float) -> dict:
    from webapp.web_publica import dato_persona
    if datos.get("comp_tipo") == "factura":
        f = {"tipo": "factura", "ruc": re.sub(r"\D", "", str(datos.get("ruc") or "")),
             "razon_social": dato_persona(datos.get("razon_social"), 160),
             "direccion": dato_persona(datos.get("direccion"), 200)}
        if not re.fullmatch(r"(10|15|16|17|20)\d{9}", f["ruc"]):
            raise Invalido("El RUC debe tener 11 dígitos.")
        if not f["razon_social"] or not f["direccion"]:
            raise Invalido("Para la factura faltan la razón social o la dirección fiscal.")
        return f
    f = {"tipo": "boleta", "dni": re.sub(r"\D", "", str(datos.get("dni") or "")),
         "nombre": dato_persona(datos.get("comp_nombre"), 120)}
    if not f["nombre"]:
        raise Invalido("Falta el nombre para la boleta.")
    if f["dni"] and len(f["dni"]) != 8:
        raise Invalido("El DNI debe tener 8 dígitos.")
    if not f["dni"] and monto > BOLETA_CON_DNI_DESDE:
        raise Invalido(f"Para una boleta de más de S/ {BOLETA_CON_DNI_DESDE} se necesita el DNI.")
    return f


def _fecha_pago(valor: str) -> str:
    try:
        f = date.fromisoformat(str(valor or ""))
    except ValueError:
        raise Invalido("Revisa la fecha del pago.")
    hoy = date.fromisoformat(config.hoy())
    if f > hoy or f < hoy - timedelta(days=366):
        raise Invalido("La fecha del pago no puede ser futura ni de hace más de un año.")
    return f.isoformat()


def informar_pago(orden: dict, datos: dict, archivo: bytes, usuario: str) -> dict:
    from webapp.web_publica import dato_persona
    if orden.get("estado") not in SIN_COBRAR:
        raise Invalido("Esta solicitud ya no admite cambios.")
    metodo = next((m for m in pagos.disponibles("cuotas" if orden["tipo"] == "cuota" else "eventos")
                   if m.get("id") == datos.get("metodo_id")), None)
    if not metodo:
        raise Invalido("Elige un medio de pago.")
    cambios = {
        "fecha_pago": _fecha_pago(datos.get("fecha_pago")),
        "operacion": dato_persona(datos.get("operacion"), 40),
        "facturacion": _facturacion(datos, orden["monto"]),
        # Copia del medio tal como lo vio la persona, aunque luego se edite.
        "metodo": {k: metodo.get(k) for k in ("id", "tipo", "nombre", "banco", "numero", "cci", "titular")},
        "estado": "en_revision", "observacion": "", "updated_at": config.ahora(),
    }
    tipo_archivo = None
    if archivo:
        if len(archivo) > MAX_ARCHIVO:
            raise Invalido("La constancia pesa más de 4 MB.")
        tipo_archivo = next(((mime, ext) for firma, mime, ext in _FIRMAS if archivo.startswith(firma)), None)
        if not tipo_archivo:
            raise Invalido("La constancia debe ser una imagen JPG o PNG, o un PDF.")
    elif not cambios["operacion"] and not orden.get("voucher_id"):
        raise Invalido("Escribe el número de operación o adjunta la constancia.")
    if tipo_archivo:
        cambios["voucher_id"] = str(archivos_col.insert_one({
            "orden_id": orden["_id"], "datos": Binary(archivo), "tipo": tipo_archivo[0],
            "ext": tipo_archivo[1], "created_at": config.ahora()}).inserted_id)
    r = ordenes_col.update_one(
        {"_id": ObjectId(orden["_id"]), "estado": {"$in": list(SIN_COBRAR)}},
        {"$set": cambios, "$push": {"historial": _hist("pago informado", usuario, cambios["operacion"])}})
    if not r.matched_count:
        if tipo_archivo:
            archivos_col.delete_one({"_id": ObjectId(cambios["voucher_id"])})
        raise Invalido("Esta solicitud cambió mientras la enviabas. Vuelve a cargar la página.")
    if tipo_archivo and orden.get("voucher_id"):
        archivos_col.delete_one({"_id": ObjectId(orden["voucher_id"])})
    return {**orden, **cambios}


def voucher(orden: dict) -> dict | None:
    vid = orden.get("voucher_id")
    if not ObjectId.is_valid(vid or ""):
        return None
    return archivos_col.find_one({"_id": ObjectId(vid), "orden_id": orden["_id"]})


# ── Confirmar ─────────────────────────────────────────────────────────────────

def confirmar(orden_id: str, usuario: str, monto=None, fecha_pago: str = "", medio: str = "") -> dict:
    orden = por_id(orden_id)
    if not orden or orden.get("estado") not in SIN_COBRAR:
        raise Invalido("Esa orden no está pendiente de confirmar.")
    try:
        recibido = round(float(monto if monto not in (None, "") else orden["monto"]), 2)
    except (TypeError, ValueError):
        raise Invalido("El monto recibido no es válido.")
    if not 0 < recibido < 1_000_000:
        raise Invalido("El monto recibido no es válido.")
    fecha = _fecha_pago(fecha_pago or orden.get("fecha_pago") or config.hoy())
    medio = (medio or (orden.get("metodo") or {}).get("nombre") or "").strip()[:80]
    tomada = ordenes_col.find_one_and_update(
        {"_id": ObjectId(orden_id), "estado": orden["estado"]},
        {"$set": {"estado": "confirmando", "updated_at": config.ahora()}})
    if not tomada:
        raise Invalido("Otra persona está atendiendo esta orden.")
    try:
        cambios: dict = {}
        if orden["tipo"] == "evento":
            if recibido + 0.009 < orden["monto"]:
                raise Invalido(f"El evento cuesta {config.soles(orden['monto'])}: con menos no se confirma la inscripción.")
            _inscripcion_confirmada(orden)
        else:
            cambios["pago_aplicado"] = _aplicar_cuota(orden, recibido, fecha, medio)
            cambios["activa"] = False  # puede volver a pagar el saldo en otra orden
        cambios.update(estado="confirmada", monto_pagado=recibido, fecha_pago=fecha, medio_pago=medio,
                       observacion="", confirmada_at=config.ahora(), confirmada_por=usuario,
                       updated_at=config.ahora())
        ordenes_col.update_one({"_id": ObjectId(orden_id)}, {
            "$set": cambios, "$push": {"historial": _hist("pago confirmado", usuario, config.soles(recibido))}})
    except Invalido:
        ordenes_col.update_one({"_id": ObjectId(orden_id)}, {"$set": {"estado": orden["estado"]}})
        raise
    catalogos.invalidar()
    return {**orden, **cambios}


def _aplicar_cuota(orden: dict, recibido: float, fecha: str, medio: str) -> dict:
    pid, n = orden["payment_id"], orden.get("cuota_numero")
    pago = pdb.payments_col.find_one({"_id": ObjectId(pid), "member_id": orden["member_id"]})
    if not pago or pago.get("estado") not in catalogos.ESTADOS_CON_DEUDA:
        raise Invalido("En Cobranzas esa cuota ya no figura como deuda: revisa antes de confirmar.")
    if n:
        cuota = next((c for c in pago.get("cuotas") or [] if c.get("numero") == n), None)
        if pago.get("estado") != "fraccionamiento" or not cuota or cuota.get("estado") == "pagado":
            raise Invalido("Esa cuota del fraccionamiento ya no está pendiente.")
        if abs(recibido - float(cuota.get("monto") or 0)) > 0.009:
            raise Invalido("El monto no coincide con la cuota: corrige la cuota en Fraccionamientos y vuelve a confirmar.")
        pdb.update_cuota(pid, n, estado="pagado", fecha_pago=fecha, medio_pago=medio or None)
        return {"tipo_pago": "cuota", "numero": n}
    if pago.get("estado") == "fraccionamiento":
        raise Invalido("Ese período pasó a fraccionamiento: anula esta orden para que se pague por cuota.")
    saldo = variables_correo.saldo_de_pago(pago)
    if saldo <= 0:
        raise Invalido("No se puede calcular el saldo: revisa el producto del pago en Cobranzas.")
    if recibido > saldo + 0.009:
        raise Invalido(f"El saldo es {config.soles(saldo)}: no se puede registrar más que eso.")
    previos = pago.get("pagos_parciales") or []
    if not previos and recibido >= saldo - 0.009:
        pdb.update_payment(pid, estado="pagado", fecha_pago=fecha, medio=medio or None, pagado_por=orden.get("nombre"))
        return {"tipo_pago": "principal"}
    # Pago a cuenta, o el que cierra un parcial: el estado lo pone _sync_estado_from_parciales (R3).
    ya = sum(p.get("monto") or 0 for p in previos)
    if not pago.get("monto_total"):
        pdb.set_monto_total(pid, round(saldo + (ya if pago.get("estado") == "parcial" else 0), 2))
    nuevo = pdb.add_pago_parcial(pid, monto=recibido, fecha_pago=fecha, medio=medio or None)
    return {"tipo_pago": "parcial", "numero": nuevo["numero"]}


def _inscripcion_confirmada(orden: dict) -> None:
    """El socio entra a los inscritos del evento y toda persona a Público, como los asistentes de Zoom."""
    eid, email = orden["evento_id"], orden["email"]
    if orden.get("member_id"):
        pdb.eventos_col.update_one(
            {"_id": ObjectId(eid), "inscritos.member_id": {"$ne": orden["member_id"]}},
            {"$push": {"inscritos": {"member_id": orden["member_id"], "nombre": orden.get("nombre", ""),
                                     "email": email, "fecha_ins": config.hoy(), "asistio": None,
                                     "orden_id": orden["_id"]}}})
    from webapp.web_publica import _ficha_publico
    pid = _ficha_publico(email, {"nombre": orden.get("nombre", "")}, "web-evento", orden.get("descripcion", ""))
    pdb.publico_col.update_one(
        {"_id": ObjectId(pid), "zoom_eventos.evento_id": {"$ne": eid}},
        {"$push": {"zoom_eventos": {"evento_id": eid, "evento_titulo": orden.get("descripcion", ""),
                                    "evento_fecha": orden.get("evento_fecha", ""), "asistio": None,
                                    "origen": "web"}}})


# ── Observar, anular, asistencia ──────────────────────────────────────────────

def observar(orden_id: str, motivo: str, usuario: str) -> dict:
    orden, motivo = por_id(orden_id), (motivo or "").strip()[:300]
    if not orden or orden.get("estado") not in SIN_COBRAR:
        raise Invalido("Esa orden no está pendiente.")
    if not motivo:
        raise Invalido("Escribe qué debe corregir la persona.")
    r = ordenes_col.update_one(
        {"_id": ObjectId(orden_id), "estado": {"$in": list(SIN_COBRAR)}},
        {"$set": {"estado": "observada", "observacion": motivo, "updated_at": config.ahora()},
         "$push": {"historial": _hist("observada", usuario, motivo)}})
    if not r.matched_count:
        raise Invalido("La orden cambió: vuelve a cargar la página.")
    return {**orden, "estado": "observada", "observacion": motivo}


def anular(orden_id: str, usuario: str, motivo: str = "", por_persona: bool = False) -> dict:
    orden, motivo = por_id(orden_id), (motivo or "").strip()[:300]
    if not orden:
        raise Invalido("No encontramos esa solicitud.")
    estado = orden.get("estado")
    if estado == "confirmada":
        if orden["tipo"] == "cuota":
            raise Invalido("Un pago de cuota confirmado se corrige en Cobranzas.")
        if por_persona and orden.get("monto"):
            raise Invalido("Para cancelar una inscripción pagada, escríbenos.")
        if por_persona and orden.get("evento_fecha", "") < config.hoy():
            raise Invalido("El evento ya pasó.")
        if not por_persona and orden.get("monto") and not motivo:
            raise Invalido("Es una inscripción pagada: escribe el motivo (devolución, nota de crédito…).")
    elif estado not in SIN_COBRAR:
        raise Invalido("Esa solicitud no se puede anular.")
    elif por_persona and estado == "en_revision":
        raise Invalido("Tu pago está en verificación. Escríbenos para anularlo.")
    r = ordenes_col.update_one(
        {"_id": ObjectId(orden_id), "estado": estado},
        {"$set": {"estado": "anulada", "activa": False, "observacion": motivo, "updated_at": config.ahora()},
         "$push": {"historial": _hist("anulada", usuario, motivo)}})
    if not r.matched_count:
        raise Invalido("La solicitud cambió: vuelve a cargar la página.")
    if estado == "confirmada":
        eid = orden["evento_id"]
        pdb.eventos_col.update_one({"_id": ObjectId(eid)}, {"$pull": {"inscritos": {"orden_id": orden_id}}})
        pdb.publico_col.update_one({"email": orden["email"]},
                                   {"$pull": {"zoom_eventos": {"evento_id": eid, "origen": "web"}}})
    return {**orden, "estado": "anulada", "observacion": motivo}


def marcar_asistencia(orden_id: str, asistio: bool | None) -> None:
    o = por_id(orden_id)
    if not o or o.get("tipo") != "evento" or o.get("estado") != "confirmada":
        raise Invalido("Solo se marca asistencia de una inscripción confirmada.")
    ordenes_col.update_one({"_id": ObjectId(orden_id)}, {"$set": {"asistio": asistio}})
    pdb.publico_col.update_one({"email": o["email"], "zoom_eventos.evento_id": o["evento_id"]},
                               {"$set": {"zoom_eventos.$.asistio": asistio}})
    if o.get("member_id"):
        pdb.marcar_asistencia(o["evento_id"], o["member_id"], asistio)


# ── Facturación: solo borradores ──────────────────────────────────────────────

def comprobante_de(orden: dict) -> dict | None:
    cid = orden.get("comprobante_id")
    if not ObjectId.is_valid(cid or ""):
        return None
    c = pdb.comprobantes_col.find_one({"_id": ObjectId(cid), "estado": {"$ne": "anulado"}},
                                      {"xml_raw": 0, "sunat_xml": 0})
    if c:
        c["_id"] = str(c["_id"])
    return c


def preparar_comprobante(orden_id: str, usuario: str) -> str:
    """Deja un borrador en Comprobantes. No numera, no sincroniza pagos y no emite."""
    o = por_id(orden_id)
    if not o or o.get("estado") != "confirmada" or not o.get("monto"):
        raise Invalido("Solo se prepara el comprobante de un pago confirmado.")
    actual = comprobante_de(o)
    if actual:
        return actual["_id"]
    f = o.get("facturacion") or {}
    socio = pdb.members_col.find_one({"member_id": o["member_id"]}, {"dni": 1}) if o.get("member_id") else None
    factura = f.get("tipo") == "factura"
    monto = o.get("monto_pagado") or o["monto"]
    item = {"member_id": o.get("member_id") or "", "producto_nombre": o["descripcion"], "monto": monto,
            "orden_id": o["_id"]}
    ap = o.get("pago_aplicado") or {}
    if o["tipo"] == "evento":
        item["evento_id"] = o["evento_id"]   # nunca se cruza con cuotas
        afect = o.get("tipo_afectacion") or eventos.afectacion(eventos.por_id(o["evento_id"]) or {})
        item["tipo_afectacion"] = afect     # "" = el borrador queda «IGV por definir» y no se puede emitir
        if o.get("tasa_igv"):
            item["tasa_igv"] = o["tasa_igv"]
    else:
        item.update(payment_id=o["payment_id"], tipo_pago=ap.get("tipo_pago", "principal"))
        if ap.get("tipo_pago") == "cuota":
            item["cuota_numero"] = ap.get("numero")
    cid = pdb.create_comprobante(
        numero="", tipo="factura" if factura else "boleta", fecha_emision="", monto_total=monto,
        producto_nombre=o["descripcion"], concepto=o["descripcion"],
        empresa=f.get("razon_social", "") if factura else "",
        socios=[o["member_id"]] if o.get("member_id") else [],
        items=[item], ruc=f.get("ruc", "") if factura else "", estado="pre_registrado",
        destinatario_id="" if factura else (o.get("member_id") or ""),
        destinatario_nombre="" if factura else (f.get("nombre") or o.get("nombre", "")),
        destinatario_dni="" if factura else (f.get("dni") or (socio or {}).get("dni") or ""),
        medio_pago=o.get("medio_pago", ""), fecha_pago=o.get("fecha_pago", ""))
    if ap.get("tipo_pago") == "parcial":
        # Sin este vínculo, al completar el borrador el cruce registraría otra vez el mismo parcial.
        pdb.update_pago_parcial(o["payment_id"], ap["numero"], comprobante_id=cid)
    ordenes_col.update_one({"_id": ObjectId(orden_id)}, {
        "$set": {"comprobante_id": cid, "updated_at": config.ahora()},
        "$push": {"historial": _hist("borrador de comprobante", usuario)}})
    return cid
