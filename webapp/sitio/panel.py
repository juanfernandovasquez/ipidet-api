"""Administración del sitio: /sitio (permiso billing) y sección «Web» de cada evento (permiso eventos).

Publicar, editar contenido y tocar los medios de pago es solo de administradores: cambiar un número de
cuenta desvía dinero, así que además avisa por Telegram.
"""
import math
import re
from datetime import date
from urllib.parse import urlencode

from bson import ObjectId
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from config import catalogos
from webapp import db as pdb
from webapp.sitio import avisos, config, cuentas, eventos, ordenes, pagos
from webapp.web_publica import dato_persona, email_valido

router = APIRouter()
POR_PAGINA = 50


def _volver(ruta: str, **q) -> RedirectResponse:
    return RedirectResponse(ruta + ("?" + urlencode(q) if q else ""), status_code=303)


def _usuario(request: Request) -> str:
    return request.session.get("user_email", "")


def _es_admin(request: Request) -> bool:
    return request.session.get("user_role") == "admin"


def _solo_admin(request: Request) -> None:
    if not _es_admin(request):
        raise HTTPException(403, "Solo un administrador puede cambiar esto.")


def _pintar(request: Request, plantilla: str, **kw):
    from webapp.app import templates, _ctx
    return templates.TemplateResponse(request, plantilla, _ctx(
        request, es_admin=_es_admin(request),
        por_verificar=ordenes.ordenes_col.count_documents({"estado": "en_revision"}), **kw))


async def _form(request: Request) -> dict:
    return {k: v for k, v in (await request.form()).items() if isinstance(v, str)}


def _entero(valor, defecto: int, minimo: int, maximo: int) -> int:
    try:
        return max(minimo, min(maximo, int(valor)))
    except (TypeError, ValueError):
        return defecto


# ── Resumen y publicación ─────────────────────────────────────────────────────

@router.get("/sitio")
def resumen(request: Request):
    from webapp import organizacion
    filas = []
    for ev in pdb.eventos_col.find(
        {"estado": {"$ne": "cancelado"},
         "$or": [{"link_grabacion": {"$nin": [None, ""]}}, {"grabaciones_extra.0": {"$exists": True}}]},
        {"zoom_registrants": 0, "inscritos": 0, "email_cuerpo": 0},
    ).sort("fecha", -1):
        partes = eventos.grabaciones_de(ev)
        filas.append({"_id": str(ev["_id"]), "titulo": ev.get("titulo", ""), "fecha": ev.get("fecha", ""),
                      "nivel": eventos.nivel_vigente(ev), "propio": ev.get("grabacion_nivel") in eventos.NIVELES,
                      "desde": ev.get("grabacion_desde", ""), "apertura": ev.get("grabacion_apertura") or {},
                      "embebe": bool(partes) and all(p["embed"] for p in partes)})
    fiscales = catalogos.get_datos_fiscales()
    listo = [
        (bool(pagos.disponibles("eventos")), "Un medio de pago para eventos", "/sitio/metodos"),
        (bool(pagos.disponibles("cuotas")), "Un medio de pago para cuotas", "/sitio/metodos"),
        (bool(organizacion.consejo(False)["vigente"]), "Consejo cargado (mientras tanto se muestra la lista de respaldo)", "/organizacion"),
        (bool(fiscales.get("razon_social") and fiscales.get("ruc")), "RUC y razón social para la política de privacidad", "/organizacion"),
        (bool(config.cuotas()), "Precio de la cuota del período de trabajo", "/productos"),
    ]
    return _pintar(request, "sitio_resumen.html", cfg=config.cargar(), grabaciones=filas,
                   niveles=eventos.NIVELES, listo=listo,
                   n_cuentas=cuentas.cuentas_col.count_documents({"perfil_completo": True}),
                   n_inscripciones=ordenes.ordenes_col.count_documents({"tipo": "evento", "estado": "confirmada"}))


@router.post("/sitio/publicar")
async def publicar(request: Request, tareas: BackgroundTasks):
    _solo_admin(request)
    publica = (await _form(request)).get("publicada") == "1"
    config.guardar({"publicada": publica})
    tareas.add_task(avisos.equipo, f"🌐 La web quedó {'PUBLICADA' if publica else 'sin publicar'} · {_usuario(request)}")
    resp = _volver("/sitio", ok="La web quedó publicada." if publica else "La web quedó en vista previa.")
    resp.background = tareas
    return resp


@router.post("/sitio/ajustes")
async def ajustes(request: Request):
    _solo_admin(request)
    f = await _form(request)
    nivel = f.get("grabacion_nivel_defecto")
    config.guardar({
        "cifra_socios": _entero(f.get("cifra_socios"), 0, 0, 100000),
        "mostrar_cifra_socios": f.get("mostrar_cifra_socios") == "1",
        "grabacion_nivel_defecto": nivel if nivel in eventos.NIVELES else "socios",
        "max_codigos_dia": _entero(f.get("max_codigos_dia"), 150, 20, 2000),
    })
    return _volver("/sitio", ok="Ajustes guardados.")


# ── Contenido ─────────────────────────────────────────────────────────────────

@router.get("/sitio/contenido")
def contenido(request: Request):
    cfg = config.cargar()
    return _pintar(request, "sitio_contenido.html", grupos=config.GRUPOS,
                   valores={k: config.a_texto(cfg.get(k), fmt) for k, (_, fmt, _c) in config.CAMPOS.items()})


@router.post("/sitio/contenido")
async def contenido_guardar(request: Request):
    _solo_admin(request)
    f, cfg = await _form(request), config.cargar()
    cambios = {}
    try:
        for clave, (_, formato, _c) in config.CAMPOS.items():
            if clave not in f:
                continue
            nuevo = config.desde_texto(clave, f[clave])
            # Solo se guarda lo que cambió: lo demás sigue tomando su valor inicial.
            if config.a_texto(nuevo, formato) != config.a_texto(cfg.get(clave), formato):
                cambios[clave] = nuevo
    except ValueError as e:
        return _volver("/sitio/contenido", error=str(e))
    config.guardar(cambios)
    return _volver("/sitio/contenido", ok="Contenido guardado." if cambios else "No había cambios.")


# ── Medios de pago ────────────────────────────────────────────────────────────

@router.get("/sitio/metodos")
def metodos(request: Request):
    return _pintar(request, "sitio_metodos.html", metodos=pagos.todos(), tipos=pagos.TIPOS, para=pagos.PARA,
                   monedas=pagos.MONEDAS, pasarelas=pagos.PASARELAS,
                   titular=catalogos.get_datos_fiscales().get("razon_social", ""))


@router.post("/sitio/metodos/guardar")
async def metodo_guardar(request: Request, tareas: BackgroundTasks):
    _solo_admin(request)
    form = await request.form()
    datos = {k: v for k, v in form.items() if isinstance(v, str)}
    datos["para"] = form.getlist("para")
    datos["activo"] = form.get("activo") == "1"
    try:
        m = pagos.guardar(datos, datos.get("id", ""))
    except ValueError as e:
        return _volver("/sitio/metodos", error=str(e))
    fin = (m.get("numero") or m.get("cci") or "")[-4:]
    tareas.add_task(avisos.equipo, f"🏦 Cambió un medio de pago de la web: {m['nombre']} (termina en {fin or 's/n'}) · "
                                   f"{_usuario(request)}. Si no lo esperabas, revísalo ya.")
    resp = _volver("/sitio/metodos", ok="Medio de pago guardado.")
    resp.background = tareas
    return resp


@router.post("/sitio/metodos/{metodo_id}/eliminar")
def metodo_eliminar(request: Request, metodo_id: str, tareas: BackgroundTasks):
    _solo_admin(request)
    m = pagos.por_id(metodo_id)
    pagos.eliminar(metodo_id)
    tareas.add_task(avisos.equipo, f"🏦 Se quitó un medio de pago de la web: {(m or {}).get('nombre', metodo_id)} · {_usuario(request)}")
    resp = _volver("/sitio/metodos", ok="Medio de pago eliminado.")
    resp.background = tareas
    return resp


# ── Pagos e inscripciones ─────────────────────────────────────────────────────

@router.get("/sitio/pagos")
def lista_pagos(request: Request, estado: str = "", tipo: str = "", evento: str = "", search: str = "", page: int = 1):
    q: dict = {}
    if estado in ordenes.ESTADOS:
        q["estado"] = estado
    if tipo in ("evento", "cuota"):
        q["tipo"] = tipo
    if config.id_valido(evento):
        q["evento_id"] = evento
    if search.strip():
        rx = {"$regex": re.escape(search.strip()[:80]), "$options": "i"}
        q["$or"] = [{"nombre": rx}, {"email": rx}, {"descripcion": rx}, {"operacion": rx}, {"member_id": rx}]
    total = ordenes.ordenes_col.count_documents(q)
    paginas = max(1, math.ceil(total / POR_PAGINA))
    page = max(1, min(page, paginas))
    filas = list(ordenes.ordenes_col.find(q).sort("created_at", -1).skip((page - 1) * POR_PAGINA).limit(POR_PAGINA))
    ids = [ObjectId(o["comprobante_id"]) for o in filas if ObjectId.is_valid(o.get("comprobante_id") or "")]
    comps = {str(c["_id"]): c for c in pdb.comprobantes_col.find(
        {"_id": {"$in": ids}, "estado": {"$ne": "anulado"}}, {"numero": 1, "estado": 1, "tipo": 1})}
    for o in filas:
        o["_id"] = str(o["_id"])
        o["comprobante"] = comps.get(o.get("comprobante_id") or "")
    return _pintar(request, "sitio_pagos.html", ordenes_web=filas, total=total, page=page, paginas=paginas,
                   estados=ordenes.ESTADOS, hoy=config.hoy(),
                   f={"estado": estado, "tipo": tipo, "evento": evento, "search": search})


@router.post("/sitio/pagos/{orden_id}/{accion}")
async def pago_accion(request: Request, orden_id: str, accion: str, tareas: BackgroundTasks):
    from fastapi.concurrency import run_in_threadpool
    f, usuario = await _form(request), _usuario(request)
    mensaje = ""
    try:
        if accion == "confirmar":
            if f.get("abono_verificado") != "1":
                return _volver("/sitio/pagos", error="Marca que viste el abono en el banco antes de confirmar.")
            o = await run_in_threadpool(ordenes.confirmar, orden_id, usuario, f.get("monto"),
                                        f.get("fecha_pago", ""), f.get("medio", ""))
            tareas.add_task(avisos.de_orden, o)
            mensaje = "Pago confirmado y avisado a la persona."
            if f.get("preparar") == "1":
                await run_in_threadpool(ordenes.preparar_comprobante, orden_id, usuario)
                mensaje += " Quedó el borrador en Comprobantes (no se emitió)."
        elif accion == "observar":
            o = await run_in_threadpool(ordenes.observar, orden_id, f.get("motivo", ""), usuario)
            tareas.add_task(avisos.de_orden, o)
            mensaje = "Se le pidió a la persona corregir su pago."
        elif accion == "anular":
            o = await run_in_threadpool(ordenes.anular, orden_id, usuario, f.get("motivo", ""))
            tareas.add_task(avisos.de_orden, o)
            mensaje = "Solicitud anulada."
        elif accion == "comprobante":
            await run_in_threadpool(ordenes.preparar_comprobante, orden_id, usuario)
            mensaje = "Borrador listo en Comprobantes. No se emitió nada."
        else:
            raise HTTPException(404)
    except ordenes.Invalido as e:
        return _volver("/sitio/pagos", error=str(e))
    resp = _volver("/sitio/pagos", ok=mensaje)
    resp.background = tareas
    return resp


@router.get("/sitio/pagos/{orden_id}/constancia")
def constancia(orden_id: str):
    o = ordenes.por_id(orden_id)
    doc = ordenes.voucher(o) if o else None
    if not doc:
        raise HTTPException(404)
    # La subió un tercero: siempre como descarga y sin permiso de ejecutar nada.
    return Response(bytes(doc["datos"]), media_type=doc["tipo"], headers={
        "Content-Disposition": f'attachment; filename="constancia-{orden_id}.{doc["ext"]}"',
        "Content-Security-Policy": "default-src 'none'; sandbox", "Cache-Control": "private, no-store"})


# ── Cuentas ───────────────────────────────────────────────────────────────────

@router.get("/sitio/cuentas")
def lista_cuentas(request: Request, search: str = "", page: int = 1):
    q: dict = {}
    if search.strip():
        rx = {"$regex": re.escape(search.strip()[:80]), "$options": "i"}
        q["$or"] = [{"email": rx}, {"nombre": rx}, {"apellido": rx}]
    total = cuentas.cuentas_col.count_documents(q)
    paginas = max(1, math.ceil(total / POR_PAGINA))
    page = max(1, min(page, paginas))
    filas = list(cuentas.cuentas_col.find(q).sort("created_at", -1).skip((page - 1) * POR_PAGINA).limit(POR_PAGINA))
    for c in filas:
        c["_id"] = str(c["_id"])
        fichas = cuentas.activas_con(c["email"])
        c["socio"] = fichas[0]["member_id"] if len(fichas) == 1 else ""
        c["repetido"] = [m["member_id"] for m in fichas] if len(fichas) > 1 else []
    return _pintar(request, "sitio_cuentas.html", cuentas_web=filas, total=total, page=page, paginas=paginas,
                   search=search)


@router.post("/sitio/cuentas/{cuenta_id}/{accion}")
def cuenta_accion(cuenta_id: str, accion: str):
    if not config.id_valido(cuenta_id) or accion not in ("suspender", "reactivar", "cerrar-sesiones"):
        raise HTTPException(404)
    if accion != "cerrar-sesiones":
        cuentas.cuentas_col.update_one({"_id": ObjectId(cuenta_id)}, {"$set": {"bloqueada": accion == "suspender"}})
    if accion != "reactivar":
        cuentas.cerrar_todas(cuenta_id)
    return _volver("/sitio/cuentas", ok="Cuenta actualizada.")


# ── Ficha del evento ──────────────────────────────────────────────────────────

def contexto_evento(evento: dict) -> dict:
    eid = str(evento["_id"])
    filas = list(ordenes.ordenes_col.find({"tipo": "evento", "evento_id": eid, "estado": {"$ne": "anulada"}})
                 .sort("created_at", -1))
    for o in filas:
        o["_id"] = str(o["_id"])
    return {
        "niveles": eventos.NIVELES, "niveles_apertura": eventos.NIVELES_APERTURA, "cobros": eventos.COBROS,
        "defecto": config.cargar().get("grabacion_nivel_defecto", "socios"),
        "vigente": eventos.nivel_vigente(evento), "partes": eventos.grabaciones_de(evento),
        "precio_texto": eventos.texto_precio(evento), "ordenes": filas, "estados": ordenes.ESTADOS,
        "extra_texto": "\n".join(f"{g.get('titulo', '')} | {g.get('url', '')}"
                                 for g in evento.get("grabaciones_extra") or []),
        "cobrado": sum(float(o.get("monto_pagado") or 0) for o in filas if o.get("estado") == "confirmada"),
        "publicable": eventos.publicable(evento),
    }


def _fecha(valor: str, nombre: str) -> str:
    if not (valor or "").strip():
        return ""
    try:
        return date.fromisoformat(valor.strip()).isoformat()
    except ValueError:
        raise ValueError(f"{nombre}: fecha no válida.")


def _monto(valor: str, nombre: str) -> float:
    try:
        n = round(float((valor or "0").replace(",", ".")), 2)
    except ValueError:
        raise ValueError(f"{nombre}: monto no válido.")
    if not 0 <= n < 100000:
        raise ValueError(f"{nombre}: monto no válido.")
    return n


@router.post("/eventos/{evento_id}/web")
async def evento_web(request: Request, evento_id: str):
    ev = eventos.por_id(evento_id)
    if not ev:
        raise HTTPException(404)
    f, ruta = await _form(request), f"/eventos/{evento_id}"
    try:
        cobro = f.get("cobro") if f.get("cobro") in eventos.COBROS else ""
        inscribe = f.get("inscripcion_web") == "1"
        if inscribe and not cobro:
            raise ValueError("Para recibir inscripciones define si el evento es gratuito o de pago.")
        socio, publico = 0.0, 0.0
        if cobro == "pago":
            socio = _monto(f.get("precio_socio", ""), "Precio de socio")
            publico = _monto(f.get("precio_publico", ""), "Precio para no socios")
            if not (socio or publico):
                raise ValueError("Un evento de pago necesita al menos un precio mayor a cero.")
        nivel = f.get("grabacion_nivel") if f.get("grabacion_nivel") in eventos.NIVELES else ""
        ap_fecha = _fecha(f.get("apertura_fecha", ""), "Apertura")
        ap_nivel = f.get("apertura_nivel") if f.get("apertura_nivel") in eventos.NIVELES_APERTURA else ""
        if bool(ap_fecha) != bool(ap_nivel):
            raise ValueError("La apertura programada necesita fecha y a quién se abre.")
        desde = _fecha(f.get("grabacion_desde", ""), "Disponible desde")
        if ap_fecha and desde and ap_fecha < desde:
            raise ValueError("La apertura no puede ser antes de que la grabación esté disponible.")
        extra = []
        for n, linea in enumerate([x.strip() for x in f.get("grabaciones_extra", "").splitlines() if x.strip()][:12], 2):
            titulo, sep, url = linea.rpartition("|")
            url = url.strip()
            if not eventos.youtube(url):
                raise ValueError(f"Video {n}: no es un enlace de YouTube válido.")
            extra.append({"titulo": titulo.strip()[:80] if sep else f"Parte {n}", "url": url})
    except ValueError as e:
        return _volver(ruta, error=str(e))
    cambios = {"inscripcion_web": inscribe, "cobro": cobro, "precio_socio": socio, "precio_publico": publico,
               "grabacion_nivel": nivel, "grabacion_desde": desde,
               "grabacion_apertura": {"fecha": ap_fecha, "nivel": ap_nivel} if ap_fecha else {},
               "grabaciones_extra": extra}
    if cobro:
        # «precio» es el texto que ya usan la ficha, los correos y el agente: se deriva de los montos.
        cambios["precio"] = eventos.texto_precio({**ev, **cambios}).replace(" ", " ")
    pdb.eventos_col.update_one({"_id": ObjectId(evento_id)}, {"$set": cambios})
    catalogos.invalidar()
    return _volver(ruta, ok="Configuración de la web guardada.")


@router.post("/eventos/{evento_id}/inscripcion-web")
async def evento_inscribir(request: Request, evento_id: str, tareas: BackgroundTasks):
    """Inscripción que hace el equipo. Si es de pago queda por pagar: se confirma en Sitio web → Pagos."""
    ev = eventos.por_id(evento_id)
    if not ev:
        raise HTTPException(404)
    f, ruta = await _form(request), f"/eventos/{evento_id}"
    mid = f.get("member_id", "").strip().upper()
    email = f.get("email", "").strip().lower()
    nombre = dato_persona(f.get("nombre"), 120)
    socio = None
    if mid:
        m = pdb.members_col.find_one({"member_id": mid})
        if not m:
            return _volver(ruta, error="Ese número de socio no existe.")
        nombre = nombre or f"{m.get('nombres', '')} {m.get('apellidos', '')}".strip().title()
        habil = [e for e in m.get("emails") or [] if e.get("estado") == "habilitado"]
        email = email or next((e["email"] for e in habil if e.get("principal")),
                              habil[0]["email"] if habil else "").strip().lower()
        # El precio de socio es solo para socios activos, igual que en la web.
        socio = mid if m.get("estado") == "activo" else None
    if not email_valido(email) or not nombre:
        return _volver(ruta, error="Faltan el nombre o un correo válido.")
    try:
        o, creada = ordenes.inscribir(evento_id, {"id": "", "email": email, "nombre_completo": nombre,
                                                  "member_id": socio}, _usuario(request), del_equipo=True)
    except ordenes.Invalido as e:
        return _volver(ruta, error=str(e))
    if not creada:
        return _volver(ruta, error="Esa persona ya tiene una inscripción en este evento.")
    if f.get("avisar") == "1":
        tareas.add_task(avisos.de_orden, o)
    resp = _volver(ruta, ok="Inscripción registrada." if o["estado"] == "confirmada"
                   else "Inscripción registrada por pagar: confírmala en Sitio web → Pagos al recibir el abono.")
    resp.background = tareas
    return resp


@router.post("/eventos/{evento_id}/asistencia-web/{orden_id}")
async def evento_asistencia(request: Request, evento_id: str, orden_id: str):
    o = ordenes.por_id(orden_id)
    if not o or o.get("evento_id") != evento_id:
        raise HTTPException(404)
    valor = (await _form(request)).get("asistio", "")
    try:
        ordenes.marcar_asistencia(orden_id, None if valor == "" else valor == "1")
    except ordenes.Invalido as e:
        return _volver(f"/eventos/{evento_id}", error=str(e))
    return _volver(f"/eventos/{evento_id}", ok="Asistencia guardada.")
