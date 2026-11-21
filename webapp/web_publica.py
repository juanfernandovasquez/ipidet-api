"""API pública para la web ipidet.org (/api/web/...).

Solo expone lo que ya es público y lo que el equipo carga en la plataforma, para que la web se
actualice sola:
- GET  /api/web/sitio      contacto y redes, consejo directivo vigente (nombre, cargo, foto), eventos
                           publicables, cuotas vigentes, aliadas y dos cifras institucionales.
- POST /api/web/contacto   formulario de contacto → ficha en Público + Pendiente + aviso por Telegram.
- POST /api/web/suscribir  boletín → ficha en Público con la etiqueta «boletin».

Nunca devuelve correos, teléfonos ni datos de socios o de pagos. Los formularios tienen campo trampa
(honeypot), límites de tamaño y de envíos por IP. La web los llama desde ipidet.org (CORS en app.py).
"""
import re
import time
from collections import defaultdict, deque
from datetime import date, datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from config import catalogos

router = APIRouter()

_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[a-z]{2,}$", re.I)
_TEMAS_CONTACTO = {
    "admision": "Información sobre afiliación",
    "pago": "Consulta sobre pago de cuota",
    "evento": "Inscripción a un evento",
    "comprobante": "Facturación y comprobantes",
    "institucional": "Convenios institucionales",
    "otro": "Otro",
}

# ── Límite de envíos por IP (en memoria: suficiente para una sola instancia) ──
_envios: dict[str, deque] = defaultdict(deque)
_MAX_POR_HORA = 5


def _ip(request: Request) -> str:
    reenviada = request.headers.get("x-forwarded-for", "")
    return (reenviada.split(",")[0].strip() or (request.client.host if request.client else "")) or "?"


def _permitido(ip: str) -> bool:
    ahora = time.monotonic()
    cola = _envios[ip]
    while cola and ahora - cola[0] > 3600:
        cola.popleft()
    if len(cola) >= _MAX_POR_HORA:
        return False
    cola.append(ahora)
    return True


def _texto(v, n: int) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:n]


# ── Datos del sitio ────────────────────────────────────────────────────────────
_cache: dict = {}
_TTL = 300  # 5 minutos: la web no consulta la base en cada visita


def _flyer(url: str) -> str:
    m = re.search(r"drive\.google\.com/(?:file/d/|uc\?.*?[?&]id=)([a-zA-Z0-9_-]+)", url or "")
    return f"https://drive.google.com/thumbnail?id={m.group(1)}&sz=w1200" if m else (url or "")


def _eventos(pdb) -> list[dict]:
    """Eventos que se muestran en la web: no cancelados, de los últimos 18 meses o próximos, y que el
    equipo marcó para publicar (publicar_web) o que tienen flyer (lo que se promociona). Con
    publicar_web = false nunca salen."""
    desde = date.today().replace(year=date.today().year - 1, month=1, day=1).isoformat()
    filas = []
    for e in pdb.eventos_col.find(
        {"estado": {"$ne": "cancelado"}, "fecha": {"$gte": desde}, "publicar_web": {"$ne": False},
         "$or": [{"publicar_web": True}, {"flyer_url": {"$nin": [None, ""]}}]},
        {"titulo": 1, "fecha": 1, "hora": 1, "modalidad": 1, "lugar": 1, "flyer_url": 1, "link_registro": 1,
         "descripcion": 1, "precio": 1},
    ).sort("fecha", -1).limit(40):
        filas.append({
            "id": str(e["_id"]), "titulo": e.get("titulo", ""), "fecha": e.get("fecha", ""), "hora": e.get("hora", ""),
            "modalidad": e.get("modalidad", ""), "lugar": e.get("lugar", ""), "flyer": _flyer(e.get("flyer_url", "")),
            "registro": e.get("link_registro", "") if (e.get("fecha") or "") >= date.today().isoformat() else "",
            "descripcion": _texto(e.get("descripcion"), 600), "precio": e.get("precio", ""),
        })
    return filas


def _sitio() -> dict:
    from webapp import db as pdb, organizacion as org
    o = catalogos.get_organizacion()
    con = org.consejo(incluir_anteriores=False)
    hoy = date.today().isoformat()
    cuotas = [{"nombre": p.get("nombre", ""), "tipo": p.get("tipo", ""), "precio": p.get("precio"), "periodo": p.get("periodo", "")}
              for p in pdb.productos_col.find({"activo": True, "tipo": {"$in": list(catalogos.TIPOS_CUOTA)},
                                               "periodo": catalogos.get_periodo_actual()},
                                              {"nombre": 1, "tipo": 1, "precio": 1, "periodo": 1})]
    return {
        "actualizado": datetime.now(timezone.utc).isoformat(),
        "contacto": {k: o.get(k, "") for k in ("email", "web", "telefono", "ciudad", "linkedin", "instagram",
                                                "facebook", "youtube")},
        "consejo": [{"nombre": d["nombre"], "cargo": d.get("cargo", ""), "foto": d.get("foto_url", ""),
                     "desde": d.get("desde", ""), "hasta": d.get("hasta", "")} for d in con["vigente"]],
        "eventos": _eventos(pdb),
        "cuotas": cuotas,
        "aliadas": [{"nombre": a.get("nombre", ""), "siglas": a.get("siglas", ""), "web": a.get("web", "")}
                    for a in pdb.alianzas_col.find({"estado": "aliada"}, {"nombre": 1, "siglas": 1, "web": 1}).limit(20)],
        "cifras": {
            "socios_activos": pdb.members_col.count_documents({"estado": "activo"}),
            "eventos_realizados": pdb.eventos_col.count_documents({"estado": {"$ne": "cancelado"}, "fecha": {"$lt": hoy}}),
        },
    }


@router.get("/api/web/sitio")
async def sitio():
    hit = _cache.get("sitio")
    if not hit or time.monotonic() - hit[0] > _TTL:
        from fastapi.concurrency import run_in_threadpool
        hit = (time.monotonic(), await run_in_threadpool(_sitio))
        _cache["sitio"] = hit
    return JSONResponse(hit[1], headers={"Cache-Control": "public, max-age=300"})


# ── Formularios ────────────────────────────────────────────────────────────────

def _ficha_publico(email: str, datos: dict, etiqueta: str, fuente_detalle: str) -> str:
    """Crea o completa la ficha en Público (sin pisar lo que ya tiene) y devuelve su id."""
    from webapp import db as pdb
    ahora = datetime.now(timezone.utc)
    socio = pdb.socios_por_email([email]).get(email)
    existente = pdb.publico_col.find_one({"email": email})
    if existente:
        cambios = {k: v for k, v in datos.items() if v and not existente.get(k)}
        if socio and not existente.get("es_socio"):
            cambios.update(es_socio=True, member_id=socio)
        pdb.publico_col.update_one({"_id": existente["_id"]},
                                   {"$set": {**cambios, "updated_at": ahora}, "$addToSet": {"tags": etiqueta}})
        return str(existente["_id"])
    return str(pdb.publico_col.insert_one({
        "nombre": "", "apellido": "", "celular": "", "titulo": "", "empresa": "", "ciudad": "", "notas": "",
        **datos, "email": email, "fuente": "portal", "fuente_detalle": fuente_detalle, "estado": "nuevo",
        "tags": [etiqueta], "es_socio": bool(socio), "member_id": socio, "comunicaciones": [],
        "created_at": ahora, "updated_at": ahora,
    }).inserted_id)


def _rechazo(msg: str, code: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": msg}, status_code=code)


@router.post("/api/web/contacto")
async def contacto(request: Request):
    try:
        data = await request.json()
    except Exception:
        return _rechazo("Datos inválidos.")
    if _texto(data.get("web"), 10):          # campo trampa: una persona no lo ve ni lo llena
        return {"ok": True}
    email = _texto(data.get("email"), 120).lower()
    mensaje = str(data.get("mensaje") or "").strip()[:3000]
    if not _EMAIL.match(email):
        return _rechazo("Revisa tu correo electrónico.")
    if len(mensaje) < 5:
        return _rechazo("Cuéntanos en qué podemos ayudarte.")
    if not data.get("acepta"):
        return _rechazo("Necesitamos tu autorización para responderte.")
    if not _permitido(_ip(request)):
        return _rechazo("Recibimos varios mensajes seguidos. Inténtalo más tarde o escríbenos por WhatsApp.", 429)
    from webapp import db as pdb
    tema = data.get("tema") if data.get("tema") in _TEMAS_CONTACTO else "otro"
    nombre, apellido = _texto(data.get("nombre"), 80), _texto(data.get("apellido"), 80)
    celular = _texto(data.get("celular"), 30)
    pid = _ficha_publico(email, {"nombre": nombre, "apellido": apellido, "celular": celular}, "web-contacto",
                         "Formulario de contacto de la web")
    pdb.create_pendiente(
        titulo=f"Web · {_TEMAS_CONTACTO[tema]}: {nombre} {apellido}".strip()[:140],
        descripcion=f"De: {nombre} {apellido} <{email}> {celular}\nFicha: /publico/{pid}\n\n{mensaje}",
        prioridad="media",
    )
    try:
        from webapp import agente_correo
        agente_correo._tg("sendMessage", chat_id=agente_correo.TELEGRAM_CHAT_ID,
                          text=f"🌐 Mensaje desde la web · {_TEMAS_CONTACTO[tema]}\nDe: {nombre} {apellido} <{email}>\n\n{mensaje[:1500]}",
                          disable_web_page_preview="true")
    except Exception:
        pass
    return {"ok": True}


@router.post("/api/web/suscribir")
async def suscribir(request: Request):
    try:
        data = await request.json()
    except Exception:
        return _rechazo("Datos inválidos.")
    if _texto(data.get("web"), 10):
        return {"ok": True}
    email = _texto(data.get("email"), 120).lower()
    if not _EMAIL.match(email):
        return _rechazo("Revisa tu correo electrónico.")
    if not _permitido(_ip(request)):
        return _rechazo("Inténtalo de nuevo más tarde.", 429)
    _ficha_publico(email, {}, "boletin", "Suscripción al boletín desde la web")
    return {"ok": True}
