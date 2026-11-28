"""Páginas y API del sitio (/web).

Mientras `publicada` sea False solo lo ve el equipo con sesión del panel (vista previa).
Todas las rutas pasan por RutaSitio: publicación, mismo origen en los POST y cabeceras de seguridad.
La CSP solo admite scripts propios porque el sitio comparte dominio con el panel del equipo.
"""
import logging
import re
from pathlib import Path
from urllib.parse import quote, urlsplit

from bson import ObjectId
from fastapi import APIRouter, BackgroundTasks, Body, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.routing import APIRoute
from fastapi.templating import Jinja2Templates

from config import catalogos
from webapp import db as pdb
from webapp import fotos, organizacion, tributos
from webapp.sitio import avisos, config, cuentas, eventos, ordenes, pagos
from webapp.web_publica import _TEMAS_CONTACTO, _ficha_publico, _ip, dato_persona, email_valido

logger = logging.getLogger(__name__)
AQUI = Path(__file__).parent
BASE = "/web"
MAX_CUERPO = ordenes.MAX_ARCHIVO + 200_000

plantillas = Jinja2Templates(directory=str(AQUI / "plantillas"))
_MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "setiembre", "octubre",
          "noviembre", "diciembre")


def _partes_fecha(valor) -> dict:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(valor or ""))
    if not m or not 1 <= int(m.group(2)) <= 12:
        return {"dia": "", "mes": "", "anio": "", "larga": ""}
    a, mes, d = m.group(1), _MESES[int(m.group(2)) - 1], int(m.group(3))
    return {"dia": d, "mes": mes[:3], "anio": a, "larga": f"{d} de {mes} de {a}"}


def _titulo_corto(t: str) -> str:
    return re.sub(r"(?i)^(conversatorio institucional|presentaci[oó]n( de libro)?):\s*", "", t or "")


plantillas.env.filters.update(soles=config.soles, fecha=_partes_fecha, corto=_titulo_corto,
                              enlace=config.url_segura)
plantillas.env.globals.update(B=BASE)

_CSP = "; ".join((
    "default-src 'self'", "script-src 'self'", "object-src 'none'", "base-uri 'self'", "form-action 'self'",
    "frame-ancestors 'self'", "connect-src 'self'", "img-src 'self' https: data:",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com", "font-src https://fonts.gstatic.com",
    "frame-src https://www.youtube-nocookie.com https://www.youtube.com",
))


def visible(request: Request) -> bool:
    """Publicada, o vista previa de alguien del equipo con sesión del panel."""
    return bool(request.session.get("user_email")) or config.publicada()


def _mismo_origen(request: Request) -> bool:
    if request.headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
        return False
    origen = request.headers.get("origin")
    if not origen:
        return True
    propios = {request.headers.get("host", ""), request.headers.get("x-forwarded-host", "")} - {""}
    try:
        return urlsplit(origen).netloc in propios
    except ValueError:
        return False


def _asegurar(resp: Response, publicada: bool) -> Response:
    resp.headers["Content-Security-Policy"] = _CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    # YouTube necesita el origen: con no-referrer el reproductor da error 153.
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["Cache-Control"] = "private, no-store"
    if not publicada:
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


class RutaSitio(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def manejar(request: Request) -> Response:
            publicada = await run_in_threadpool(config.publicada)
            ruta = request.scope["path"]   # request.url.path se altera con la cabecera Host
            api = ruta.startswith(f"{BASE}/api/")
            if not publicada and not request.session.get("user_email"):
                if api:
                    return _asegurar(_error("La web todavía no está publicada.", 404), False)
                return _asegurar(HTMLResponse(
                    '<!doctype html><html lang="es"><meta charset="utf-8"><title>IPIDET</title>'
                    "<p>Esta página todavía no está publicada.</p>", status_code=404), False)
            if request.method != "GET":
                foto = ruta == f"{BASE}/api/foto"
                archivo = ruta.endswith("/informar") or foto
                tipo = request.headers.get("content-type", "").split(";")[0].strip().lower()
                if not _mismo_origen(request) or tipo != ("multipart/form-data" if archivo else "application/json"):
                    return _asegurar(_error("Solicitud no válida.", 403), publicada)
                largo = request.headers.get("content-length", "")
                tope = fotos.MAX_ARCHIVO + 200_000 if foto else MAX_CUERPO if archivo else 50_000
                if not largo.isdigit() or int(largo) > tope:
                    return _asegurar(_error("El envío es demasiado grande. " + ("La foto admite hasta 8 MB." if foto
                                            else "La constancia admite hasta 4 MB."), 413), publicada)
            try:
                resp = await original(request)
            except ordenes.Invalido as e:
                resp = _error(str(e))
            except HTTPException as e:
                if api:
                    resp = _error("No encontrado." if e.status_code == 404 else str(e.detail), e.status_code)
                else:
                    resp = await run_in_threadpool(_pagina, request, "error.html", "", e.status_code)
            return _asegurar(resp, publicada)

        return manejar


router = APIRouter(prefix=BASE, route_class=RutaSitio)


def _error(msg: str, code: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": msg}, status_code=code)


def _bien(**datos) -> JSONResponse:
    return JSONResponse({"ok": True, **datos})


def _siguiente(valor: str) -> str:
    """Solo rutas de este sitio: ni otros dominios ni el panel."""
    v = str(valor or "")
    return v if re.fullmatch(r"/web(/[A-Za-z0-9_-]+)*", v) else f"{BASE}/cuenta"


def _pagina(request: Request, plantilla: str, activa: str = "", codigo: int = 200, **kw) -> HTMLResponse:
    cfg = config.cargar()
    return plantillas.TemplateResponse(request, plantilla, {
        "cfg": cfg, "c": config.contacto(), "persona": cuentas.persona(request), "activa": activa,
        "vista_previa": not cfg.get("publicada"), "hoy": config.hoy(),
        "anios": int(config.hoy()[:4]) - int(str(cfg.get("fundacion") or config.hoy())[:4]),
        **kw,
    }, status_code=codigo)


def _a_ingresar(request: Request) -> RedirectResponse:
    return RedirectResponse(f"{BASE}/ingresar?next={quote(request.scope["path"], safe='')}", status_code=303)


def _con_cuenta(request: Request) -> dict:
    p = cuentas.persona(request)
    if not p or not p["registrada"]:
        raise HTTPException(401, "Ingresa a tu cuenta para continuar.")
    return p


def _limite(clave: str, maximo: int, segundos: int = 3600) -> None:
    if not cuentas.permitido(clave, maximo, segundos):
        raise HTTPException(429, "Recibimos varios intentos seguidos. Espera unos minutos e inténtalo de nuevo.")


# ── Institución ───────────────────────────────────────────────────────────────

def _consejo() -> list[dict]:
    vigente = organizacion.consejo(incluir_anteriores=False)["vigente"]
    if vigente:
        return [{"nombre": d["nombre"].title() if d["nombre"].isupper() else d["nombre"],
                 "cargo": d.get("cargo", ""),
                 "foto": (f"{BASE}/consejo/{quote(d['member_id'], safe='')}/foto?v={d['foto_at']}" if d.get("foto_at")
                          else config.url_segura(d.get("foto_url", "")))} for d in vigente]
    return [{"nombre": n, "cargo": c, "foto": config.url_segura(f)}
            for n, c, f in config.cargar().get("consejo_respaldo") or []]


@router.get("")
def inicio(request: Request):
    ag = eventos.agenda()
    consejo = _consejo()
    aliadas = [{"nombre": a.get("siglas") or a.get("nombre", ""), "web": config.url_segura(a.get("web", ""))}
               for a in pdb.alianzas_col.find({"estado": "aliada"}, {"nombre": 1, "siglas": 1, "web": 1}).limit(20)]
    return _pagina(request, "inicio.html", "inicio",
                   agenda=(ag["proximos"] or ag["pasados"])[:4], hay_proximos=bool(ag["proximos"]),
                   presidencia=next((p for p in consejo if p["cargo"].lower().startswith("president")), None),
                   aliadas=aliadas)


@router.get("/nosotros")
def nosotros(request: Request):
    consejo = _consejo()
    return _pagina(request, "nosotros.html", "nosotros", consejo=consejo,
                   presidencia=next((p for p in consejo if p["cargo"].lower().startswith("president")), None))


@router.get("/consejo/{member_id}/foto")
def foto_consejo(member_id: str):
    """Pública solo la foto de quien está hoy en el consejo directivo."""
    if not any(d.get("member_id") == member_id for d in organizacion.consejo(incluir_anteriores=False)["vigente"]):
        raise HTTPException(404)
    return fotos.respuesta(member_id, "public, max-age=3600")


@router.get("/tramites")
def tramites(request: Request):
    valores = config.marcadores()
    cfg = config.cargar()
    return _pagina(request, "tramites.html", "tramites", cuotas=config.cuotas(),
                   preguntas=[(q, config.rellenar(r, valores)) for q, r in cfg.get("preguntas") or []])


@router.get("/contacto")
def contacto(request: Request):
    return _pagina(request, "contacto.html", "contacto", temas=_TEMAS_CONTACTO)


@router.get("/privacidad")
def privacidad(request: Request):
    return _legal(request, "privacidad", "Política de privacidad")


@router.get("/terminos")
def terminos(request: Request):
    return _legal(request, "terminos", "Términos y condiciones")


def _legal(request: Request, clave: str, titulo: str):
    valores = config.marcadores()
    parrafos = [config.rellenar(x, valores) for x in config.cargar().get(clave) or []]
    return _pagina(request, "legal.html", titulo=titulo, parrafos=parrafos)


# ── Eventos y grabaciones ─────────────────────────────────────────────────────

@router.get("/eventos")
def lista_eventos(request: Request):
    ag = eventos.agenda()
    todos = ag["proximos"] + ag["pasados"]
    for e in todos:
        e["precio_texto"] = eventos.texto_precio(e)
    return _pagina(request, "eventos.html", "eventos", principal=todos[0] if todos else None, resto=todos[1:])


@router.get("/eventos/{evento_id}")
def ver_evento(request: Request, evento_id: str):
    ev, p = eventos.por_id(evento_id), cuentas.persona(request)
    if not ev:
        raise HTTPException(404)
    mios = eventos.eventos_de(p)
    if not eventos.publicable(ev) and evento_id not in mios:
        raise HTTPException(404)
    orden = None
    if p:
        orden = next((o for o in ordenes.de_persona(p)
                      if o.get("evento_id") == evento_id and o.get("activa")), None)
    inscrito = evento_id in mios
    return _pagina(request, "evento.html", "eventos",
                   ev={"_id": ev["_id"], "titulo": ev.get("titulo", ""), "fecha": ev.get("fecha", ""),
                       "hora": ev.get("hora", ""), "lugar": ev.get("lugar", ""), "modalidad": ev.get("modalidad", ""),
                       "descripcion": ev.get("descripcion", ""), "flyer": eventos.flyer(ev),
                       "limite": ev.get("fecha_limite_inscripcion", ""),
                       "precio_texto": eventos.texto_precio(ev), "de_pago": ev.get("cobro") == "pago",
                       "con_igv": ev.get("cobro") == "pago" and eventos.afectacion(ev) == tributos.GRAVADO,
                       "proximo": (ev.get("fecha") or "") >= config.hoy(),
                       "externo": config.url_segura(ev.get("link_registro", "")),
                       # El enlace de la sesión solo lo recibe quien está inscrito.
                       "sesion": config.url_segura(ev.get("link_zoom", ""))
                       if inscrito and (ev.get("fecha") or "") >= config.hoy() else "",
                       "expositores": [{k: x.get(k, "") for k in ("nombre", "titulo", "empresa")}
                                       for x in ev.get("expositores") or [] if x.get("confirmado")]},
                   inscripcion=eventos.estado_inscripcion(ev), inscrito=inscrito, orden=orden,
                   mi_precio=eventos.precio(ev, bool(p and p.get("socio"))),
                   con_grabacion=bool(eventos.grabaciones_de(ev))
                   and eventos.se_anuncia(ev, eventos.acceso(ev, p, mios)))


@router.get("/grabaciones")
def lista_grabaciones(request: Request):
    return _pagina(request, "grabaciones.html", "grabaciones",
                   filas=eventos.biblioteca(cuentas.persona(request)), niveles=eventos.NIVELES)


@router.get("/grabaciones/{evento_id}")
def ver_grabacion(request: Request, evento_id: str):
    ev, p = eventos.por_id(evento_id), cuentas.persona(request)
    partes = eventos.grabaciones_de(ev) if ev else []
    if not partes:
        raise HTTPException(404)
    ac = eventos.acceso(ev, p, eventos.eventos_de(p))
    if not eventos.se_anuncia(ev, ac):
        raise HTTPException(404)
    return _pagina(request, "grabacion.html", "grabaciones",
                   ev={"_id": ev["_id"], "titulo": ev.get("titulo", ""), "fecha": ev.get("fecha", ""),
                       "flyer": eventos.flyer(ev), "desde": ev.get("grabacion_desde", "")},
                   ac=ac, niveles=eventos.NIVELES,
                   partes=partes if ac["puede"] else [])   # sin permiso, el video no llega al navegador


# ── Cuenta ────────────────────────────────────────────────────────────────────

@router.get("/ingresar")
def ingresar(request: Request, next: str = ""):
    destino, p = _siguiente(next), cuentas.persona(request)
    if p:
        if p["registrada"]:
            return RedirectResponse(destino, status_code=303)
        return RedirectResponse(f"{BASE}/completar?next={quote(destino, safe='')}", status_code=303)
    return _pagina(request, "ingresar.html", "cuenta", siguiente=destino, minutos=cuentas.MINUTOS_CODIGO)


@router.get("/completar")
def completar(request: Request, next: str = ""):
    p = cuentas.persona(request)
    if not p:
        return _a_ingresar(request)
    if p["registrada"]:
        return RedirectResponse(_siguiente(next), status_code=303)
    return _pagina(request, "completar.html", "cuenta", siguiente=_siguiente(next))


def _comprobantes_de(p: dict, mis_ordenes: list[dict]) -> list[dict]:
    ids = {o["comprobante_id"] for o in mis_ordenes if o.get("comprobante_id")}
    if p.get("member_id"):
        pagos_socio = list(pdb.payments_col.find({"member_id": p["member_id"]}))
        for entradas in pdb.detalle_comprobantes_de_pagos(pagos_socio).values():
            ids |= {str(e["comprobante"]["_id"]) for e in entradas if e.get("comprobante")}
    docs = pdb.comprobantes_col.find(
        {"_id": {"$in": [ObjectId(i) for i in ids if ObjectId.is_valid(i)]},
         "estado": {"$nin": ["anulado", "pre_registrado"]}, "numero": {"$nin": [None, ""]}},
        {"numero": 1, "tipo": 1, "fecha_emision": 1, "monto_total": 1, "socios": 1, "producto_nombre": 1,
         "anulado_por_nc": 1, "items": 1})
    propias = {o["_id"] for o in mis_ordenes}
    filas = []
    for c in docs:
        lineas = c.get("items") or []
        de_orden = bool(lineas) and all(i.get("orden_id") in propias for i in lineas)
        boleta_propia = bool(p.get("member_id")) and c.get("tipo") == "boleta" and c.get("socios") == [p["member_id"]]
        completo = de_orden or boleta_propia
        # Una factura de empresa puede cubrir a varios socios: ni su total ni su PDF son de uno solo.
        mias = [i for i in lineas if i.get("orden_id") in propias
                or (p.get("member_id") and i.get("member_id") == p["member_id"])]
        filas.append({"_id": str(c["_id"]), "numero": c["numero"], "tipo": c.get("tipo", ""),
                      "fecha": c.get("fecha_emision", ""), "descargar": completo,
                      "detalle": c.get("producto_nombre", "") if completo
                      else ", ".join(i.get("producto_nombre", "") for i in mias),
                      "monto": c.get("monto_total") if completo else sum(float(i.get("monto") or 0) for i in mias),
                      "anulado_nc": bool(c.get("anulado_por_nc"))})
    return sorted(filas, key=lambda f: f["fecha"] or "", reverse=True)


def _eventos_cuenta(p: dict, mis_ordenes: list[dict]) -> list[dict]:
    por_evento: dict[str, dict] = {}
    for o in mis_ordenes:
        if o["tipo"] == "evento":
            previo = por_evento.get(o["evento_id"])
            if not previo or (o.get("activa") and not previo.get("activa")):
                por_evento[o["evento_id"]] = o
    inscritos = eventos.eventos_de(p)
    asistio = {}
    if p.get("member_id"):
        asistio = {e["evento_id"]: e["asistio"] for e in pdb.eventos_de_socio(p["member_id"])}
    ids = set(por_evento) | inscritos
    filas = []
    for ev in pdb.eventos_col.find({"_id": {"$in": [ObjectId(i) for i in ids if ObjectId.is_valid(i)]}},
                                   {"titulo": 1, "fecha": 1, "hora": 1, "estado": 1,
                                    "link_grabacion": 1, "grabaciones_extra": 1}):
        eid = str(ev["_id"])
        o = por_evento.get(eid)
        filas.append({"_id": eid, "titulo": ev.get("titulo", ""), "fecha": ev.get("fecha", ""),
                      "hora": ev.get("hora", ""), "cancelado": ev.get("estado") == "cancelado",
                      "inscrito": eid in inscritos, "orden": o,
                      "asistio": o.get("asistio") if o and o.get("asistio") is not None else asistio.get(eid),
                      "grabacion": bool(eventos.grabaciones_de(ev)) and eid in inscritos})
    return sorted(filas, key=lambda f: f["fecha"], reverse=True)


@router.get("/cuenta")
def cuenta(request: Request):
    p = cuentas.persona(request)
    if not p:
        return _a_ingresar(request)
    if not p["registrada"]:
        return RedirectResponse(f"{BASE}/completar", status_code=303)
    mias = ordenes.de_persona(p)
    periodos, por_pagar = [], []
    if p.get("member_id"):
        periodos = [{"periodo": x.get("periodo", ""), "estado": x.get("estado", ""),
                     "etiqueta": catalogos.ESTADOS_PAGO.get(x.get("estado"), (x.get("estado", ""),))[0],
                     "fecha_pago": x.get("fecha_pago") or "", "empresa": x.get("empresa_pagadora") or ""}
                    for x in pdb.payments_col.find({"member_id": p["member_id"]}).sort("periodo", -1)]
        por_pagar = ordenes.cuotas_por_pagar(p["member_id"])
        for f in por_pagar:
            clave = f"cuota:{f['payment_id']}:{f['cuota_numero'] or 0}"
            f["orden"] = next((o for o in mias if o.get("activa") and o.get("clave") == clave), None)
    return _pagina(request, "cuenta.html", "cuenta", periodos=periodos, por_pagar=por_pagar,
                   tramites=[o for o in mias if o.get("activa") and o.get("estado") != "confirmada"],
                   mis_eventos=_eventos_cuenta(p, mias), comprobantes=_comprobantes_de(p, mias),
                   estados=ordenes.ESTADOS)


@router.get("/cuenta/foto")
def mi_foto(request: Request):
    p = cuentas.persona(request)
    if not p or not p.get("member_id"):
        raise HTTPException(404)
    return fotos.respuesta(p["member_id"], "private, max-age=600")


@router.post("/api/foto")
def api_foto(request: Request, archivo: UploadFile | None = File(None)):
    """El socio sube su foto de perfil. Se vuelve a dibujar como JPEG: no queda nada más que la imagen."""
    p = _con_cuenta(request)
    if not p.get("member_id"):
        raise HTTPException(403, "La foto de perfil es para asociados.")
    _limite(f"foto:{p['id']}", 10)
    crudo = archivo.file.read(fotos.MAX_ARCHIVO + 1) if archivo and archivo.filename else b""
    try:
        fotos.guardar(p["member_id"], fotos.procesar(crudo), "web", p["email"])
    except ValueError as e:
        return _error(str(e))
    return _bien(ir=f"{BASE}/cuenta")


@router.post("/api/foto/quitar")
def api_foto_quitar(request: Request):
    p = _con_cuenta(request)
    if not p.get("member_id"):
        raise HTTPException(403, "La foto de perfil es para asociados.")
    fotos.quitar(p["member_id"])
    return _bien(ir=f"{BASE}/cuenta")


@router.get("/pagar/{orden_id}")
def pagar(request: Request, orden_id: str):
    p = cuentas.persona(request)
    if not p:
        return _a_ingresar(request)
    o = ordenes.por_id(orden_id)
    if not ordenes.es_de(o, p):
        raise HTTPException(404)
    f = o.get("facturacion") or {}
    igv = float(tributos.desglosar(o["monto"], o.get("tipo_afectacion") or "", o.get("tasa_igv") or 0)[1])
    return _pagina(request, "pagar.html", "cuenta", orden=o, igv=igv, estados=ordenes.ESTADOS,
                   metodos=pagos.disponibles("cuotas" if o["tipo"] == "cuota" else "eventos"),
                   fact={**f, "dni": f.get("dni") or ((p.get("socio") or {}).get("dni") or ""),
                         "nombre": f.get("nombre") or p["nombre_completo"]},
                   dni_desde=ordenes.BOLETA_CON_DNI_DESDE)


@router.get("/comprobante/{comp_id}")
def descargar_comprobante(request: Request, comp_id: str):
    p = cuentas.persona(request)
    if not p or not p["registrada"]:
        return _a_ingresar(request)
    fila = next((c for c in _comprobantes_de(p, ordenes.de_persona(p))
                 if c["_id"] == comp_id and c["descargar"]), None)
    if not fila:
        raise HTTPException(404)
    from webapp.pdf_builder import build_comprobante_pdf
    info = pdb.get_comprobante_email_info(comp_id) or {}
    pdf = build_comprobante_pdf(pdb.get_comprobante_by_id(comp_id), info.get("socios_info") or [])
    nombre = re.sub(r"[^A-Za-z0-9-]", "-", fila["numero"])
    return Response(bytes(pdf), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{nombre}.pdf"'})


# ── API: ingreso ──────────────────────────────────────────────────────────────

@router.post("/api/codigo")
def api_codigo(request: Request, tareas: BackgroundTasks, datos: dict = Body(...)):
    email = str(datos.get("email") or "").strip().lower()[:120]
    if not email_valido(email):
        return _error("Revisa tu correo electrónico.")
    _limite(f"codigo-ip:{_ip(request)}", 20)
    _limite(f"codigo-correo:{email}", 3, 900)
    _limite(f"codigo-correo-dia:{email}", 10, 86400)
    # Tope diario: los códigos salen de la misma cuota de Brevo que las comunicaciones.
    tope = int(config.cargar().get("max_codigos_dia") or 150)
    # Dos cupos separados: quien inunde con correos inventados no deja fuera a socios ni a cuentas existentes.
    conocido = cuentas.cuentas_col.count_documents({"email": email}, limit=1) or cuentas.socio_de(email)
    grupo = "conocidos" if conocido else "nuevos"
    reto, numero = cuentas.crear_codigo(email)
    if cuentas.permitido(f"codigos-dia:{grupo}", tope if conocido else max(1, tope // 2), 86400):
        tareas.add_task(avisos.codigo, email, numero, cuentas.MINUTOS_CODIGO)
    elif cuentas.permitido(f"aviso-tope:{grupo}", 1, 86400):
        tareas.add_task(avisos.equipo, f"⚠️ La web llegó al tope diario de códigos de ingreso ({grupo}). "
                                       "Puede ser un ataque: revisa Sitio web → Cuentas.")
    # Misma respuesta y mismo tiempo exista o no la cuenta: no revela quién es socio.
    return JSONResponse({"ok": True, "reto": reto}, background=tareas)


@router.post("/api/entrar")
def api_entrar(request: Request, datos: dict = Body(...)):
    _limite(f"entrar-ip:{_ip(request)}", 80)
    email = cuentas.verificar_codigo(str(datos.get("reto") or "")[:80], re.sub(r"\D", "", str(datos.get("codigo") or "")))
    if not email:
        return _error("El código no es correcto o ya venció. Puedes pedir otro.")
    c = cuentas.cuenta_de_email(email)
    if c.get("bloqueada"):
        return _error("Esta cuenta está suspendida. Escríbenos para revisarla.", 403)
    destino = _siguiente(datos.get("next"))
    if not c.get("perfil_completo"):
        destino = f"{BASE}/completar?next={quote(destino, safe='')}"
    resp = _bien(ir=destino)
    resp.set_cookie(cuentas.COOKIE, cuentas.abrir_sesion(c["_id"]), max_age=cuentas.DIAS_SESION * 86400,
                    path=BASE, secure=True, httponly=True, samesite="lax")
    return resp


@router.post("/api/salir")
def api_salir(request: Request):
    cuentas.cerrar_sesion(request.cookies.get(cuentas.COOKIE, ""))
    resp = _bien(ir=BASE)
    resp.delete_cookie(cuentas.COOKIE, path=BASE)
    return resp


@router.post("/api/perfil")
def api_perfil(request: Request, datos: dict = Body(...)):
    p = cuentas.persona(request)
    if not p:
        raise HTTPException(401, "Ingresa a tu cuenta para continuar.")
    if not p["registrada"] and not datos.get("acepta"):
        return _error("Para crear tu cuenta necesitas aceptar los términos y la política de privacidad.")
    campos = {k: dato_persona(datos.get(k), n) for k, n in (("nombre", 80), ("apellido", 80),
                                                          ("empresa", 120), ("ciudad", 80))}
    campos["celular"] = re.sub(r"[^0-9+() -]", "", str(datos.get("celular") or ""))[:30].strip()
    if not campos["nombre"] or not campos["apellido"]:
        return _error("Escribe tu nombre y tus apellidos.")
    avisar = bool(datos.get("acepta_avisos"))
    cambios = {**campos, "acepta_avisos": avisar, "updated_at": config.ahora()}
    if not p["registrada"]:
        cambios.update(perfil_completo=True, acepto_at=config.ahora())
    tenia_ficha = bool(pdb.publico_col.find_one({"email": p["email"]}, {"_id": 1}))
    pid = _ficha_publico(p["email"], campos, "web-cuenta", "Cuenta de la web")
    cambios["prospecto_id"] = pid
    cuentas.cuentas_col.update_one({"_id": ObjectId(p["id"])}, {"$set": cambios})
    if avisar:
        pdb.publico_col.update_one({"_id": ObjectId(pid)},
                                   {"$set": {"sin_avisos": False}, "$addToSet": {"tags": "boletin"}})
    elif not tenia_ficha or p["acepta_avisos"]:
        # No autorizó o retiró su autorización: sale de los envíos masivos a Público.
        pdb.publico_col.update_one({"_id": ObjectId(pid)},
                                   {"$set": {"sin_avisos": True}, "$pull": {"tags": "boletin"}})
    return _bien(ir=_siguiente(datos.get("next")), mensaje="Tus datos quedaron guardados.")


# ── API: inscripciones y pagos ────────────────────────────────────────────────

@router.post("/api/eventos/{evento_id}/inscribir")
def api_inscribir(request: Request, evento_id: str, tareas: BackgroundTasks):
    p = _con_cuenta(request)
    _limite(f"inscribir:{p['id']}", 20)
    o, creada = ordenes.inscribir(evento_id, p, p["email"])
    if creada:
        tareas.add_task(avisos.de_orden, o)
        tareas.add_task(avisos.equipo, f"🎟️ Inscripción web · {o['descripcion']}\n{o['nombre']} <{o['email']}>"
                                       f"{' · socio ' + o['member_id'] if o.get('member_id') else ' · no socio'}\n"
                                       + (f"Por pagar {config.soles(o['monto'])}" if o["monto"] else "Confirmada (sin costo)"))
    ir = f"{BASE}/eventos/{evento_id}" if o["estado"] == "confirmada" else f"{BASE}/pagar/{o['_id']}"
    return JSONResponse({"ok": True, "ir": ir}, background=tareas)


@router.post("/api/cuotas/pagar")
def api_pagar_cuota(request: Request, datos: dict = Body(...)):
    p = _con_cuenta(request)
    _limite(f"cuota:{p['id']}", 20)
    o, _ = ordenes.pagar_cuota(str(datos.get("payment_id") or ""), datos.get("cuota_numero"), p)
    return _bien(ir=f"{BASE}/pagar/{o['_id']}")


@router.post("/api/ordenes/{orden_id}/informar")
def api_informar(request: Request, orden_id: str, tareas: BackgroundTasks,
                 metodo_id: str = Form(""), fecha_pago: str = Form(""), operacion: str = Form(""),
                 comp_tipo: str = Form("boleta"), comp_nombre: str = Form(""), dni: str = Form(""),
                 ruc: str = Form(""), razon_social: str = Form(""), direccion: str = Form(""),
                 archivo: UploadFile | None = File(None)):
    p = _con_cuenta(request)
    o = ordenes.por_id(orden_id)
    if not ordenes.es_de(o, p):
        raise HTTPException(404)
    _limite(f"informar:{p['id']}", 12)
    _limite("informar:todos", 300, 86400)
    datos = {"metodo_id": metodo_id, "fecha_pago": fecha_pago, "operacion": operacion, "comp_tipo": comp_tipo,
             "comp_nombre": comp_nombre, "dni": dni, "ruc": ruc, "razon_social": razon_social, "direccion": direccion}
    crudo = archivo.file.read(ordenes.MAX_ARCHIVO + 1) if archivo and archivo.filename else b""
    o = ordenes.informar_pago(o, datos, crudo, p["email"])
    tareas.add_task(avisos.de_orden, o)
    tareas.add_task(avisos.equipo, f"💰 Pago por verificar · {o['descripcion']}\n{o['nombre']} <{o['email']}>\n"
                                   f"{config.soles(o['monto'])} · {o['metodo']['nombre']} · op. {o.get('operacion') or 's/n'}\n"
                                   "Revísalo en Sitio web → Pagos. No lo des por pagado sin ver el abono en el banco.")
    return JSONResponse({"ok": True, "ir": f"{BASE}/pagar/{orden_id}"}, background=tareas)


@router.post("/api/ordenes/{orden_id}/anular")
def api_anular(request: Request, orden_id: str, tareas: BackgroundTasks):
    p = _con_cuenta(request)
    if not ordenes.es_de(ordenes.por_id(orden_id), p):
        raise HTTPException(404)
    o = ordenes.anular(orden_id, p["email"], por_persona=True)
    tareas.add_task(avisos.equipo, f"↩️ Anulado desde la web · {o['descripcion']}\n{o['nombre']} <{o['email']}>")
    return JSONResponse({"ok": True, "ir": f"{BASE}/cuenta"}, background=tareas)


# ── API: trámites ─────────────────────────────────────────────────────────────

@router.post("/api/postular")
def api_postular(request: Request, tareas: BackgroundTasks):
    p = _con_cuenta(request)
    if p.get("socio"):
        return _error("Ya eres socio de IPIDET.")
    _limite(f"postular:{p['id']}", 3, 86400)
    pid = _ficha_publico(p["email"], {k: p[k] for k in ("nombre", "apellido", "celular", "empresa", "ciudad")},
                         "web-admision", "Quiero asociarme (web)")
    try:
        _, creada = pdb.crear_postulacion(pid, origen="web", usuario="web",
                                          detalle="Pidió asociarse desde la web")
    except ValueError:
        # El correo figura en el padrón pero sin acceso de socio (retiro, dos fichas…): lo ve una persona.
        return _error("Tu correo ya figura en nuestro padrón. Escríbenos y revisamos tu situación.")
    if creada:
        tareas.add_task(avisos.equipo, f"📝 Quiere asociarse (web)\n{p['nombre_completo']} <{p['email']}>")
    return JSONResponse({"ok": True, "mensaje": "Recibimos tu pedido. El equipo te escribirá con los siguientes pasos."},
                        background=tareas)


@router.post("/api/constancia")
def api_constancia(request: Request, tareas: BackgroundTasks):
    p = _con_cuenta(request)
    if not p.get("socio"):
        return _error("La constancia es solo para socios activos.")
    titulo = "Web · Constancia de membresía"
    if not pdb.pendientes_col.find_one({"member_id": p["member_id"], "titulo": titulo, "estado": {"$ne": "resuelto"}}):
        pdb.create_pendiente(titulo=titulo, member_id=p["member_id"], nombre_miembro=p["nombre_completo"],
                             descripcion=f"La pide desde la web con {p['email']}. "
                                         f"Se genera y envía en /members/{p['member_id']}.")
        tareas.add_task(avisos.equipo, f"📄 Pide constancia (web) · {p['nombre_completo']} · {p['member_id']}")
    return JSONResponse({"ok": True, "mensaje": "Pedido recibido. Te la enviaremos por correo."}, background=tareas)
