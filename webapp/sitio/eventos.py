"""Reglas de eventos y grabaciones del sitio. Todo sale de la ficha del evento (/eventos/{id}).

Campos que agrega la sección «Web» de la ficha:
  inscripcion_web      bool   la inscripción se hace en nuestra página
  cobro                "gratuito" | "pago"
  precio_socio, precio_publico  float (en soles; el socio puede pagar 0)
  grabacion_nivel      NIVELES; sin valor, el predeterminado de /sitio (socios)
  grabacion_desde      fecha desde la que se puede ver
  grabacion_apertura   {fecha, nivel}: desde esa fecha se abre también a ese nivel
  grabaciones_extra    [{titulo, url}]: además de link_grabacion (eventos de varios días)

Un socio activo ve todas las grabaciones salvo las ocultas o las que todavía no llegan a su fecha.
Este módulo no importa webapp.db: también lo usa el agente de correos.
"""
import re
from datetime import date, timedelta
from urllib.parse import parse_qs, urlsplit

from bson import ObjectId

from config import catalogos
from config.mongo_client import db
from webapp.sitio import config

eventos_col = db.eventos_ipidet
ordenes_col = db.web_ordenes

# De más abierto a más cerrado. Es lógica de permisos, no un catálogo editable.
NIVELES = {
    "publico":     "Todos, sin cuenta",
    "registrados": "Personas con cuenta",
    "inscritos":   "Inscritos a este evento",
    "socios":      "Solo socios activos",
    "oculta":      "Oculta: no sale en la web",
}
NIVELES_APERTURA = ("publico", "registrados", "inscritos")
_APERTURA = {n: i for i, n in enumerate(NIVELES)}
HORAS_RESERVA = 48
COBROS = {"gratuito": "Gratuito", "pago": "De pago"}
_RE_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_RE_LISTA = re.compile(r"[A-Za-z0-9_-]{12,64}")


def por_id(evento_id: str) -> dict | None:
    if not config.id_valido(evento_id):
        return None
    ev = eventos_col.find_one({"_id": ObjectId(evento_id)})
    if ev:
        ev["_id"] = str(ev["_id"])
    return ev


def flyer(ev: dict) -> str:
    url = ev.get("flyer_url") or ""
    m = re.search(r"drive\.google\.com/(?:file/d/|uc\?(?:.*&)?id=|open\?id=)([a-zA-Z0-9_-]+)", url)
    if m:
        return f"https://drive.google.com/thumbnail?id={m.group(1)}&sz=w1200"
    return config.url_segura(url, imagen=True)


def publicable(ev: dict) -> bool:
    if ev.get("estado") == "cancelado" or ev.get("publicar_web") is False:
        return False
    return ev.get("publicar_web") is True or bool(ev.get("flyer_url"))


def tipo(titulo: str) -> tuple[str, str]:
    s = (titulo or "").lower()
    if s.startswith("conversatorio"):
        return "conversatorio", "Conversatorio"
    if s.startswith(("presentación", "presentacion")):
        return "presentacion", "Presentación"
    return "evento", "Foro" if "foro" in s else "Evento"


def agenda() -> dict:
    """Eventos publicados de los últimos 18 meses y próximos."""
    desde = (date.fromisoformat(config.hoy()) - timedelta(days=548)).isoformat()
    filas = [e for e in eventos_col.find(
        {"estado": {"$ne": "cancelado"}, "publicar_web": {"$ne": False}, "fecha": {"$gte": desde}},
        {"zoom_registrants": 0, "email_cuerpo": 0, "agente_faqs": 0, "agente_info": 0},
    ).sort("fecha", 1) if publicable(e)]
    hoy = config.hoy()
    for e in filas:
        e["_id"] = str(e["_id"])
        e["flyer"] = flyer(e)
        e["tipo_web"], e["tipo_texto"] = tipo(e.get("titulo", ""))
        e["proximo"] = (e.get("fecha") or "") >= hoy
    return {"proximos": [e for e in filas if e["proximo"]],
            "pasados": [e for e in reversed(filas) if not e["proximo"]]}


# ── Inscripción y precio ──────────────────────────────────────────────────────

def precio(ev: dict, es_socio: bool) -> float:
    if ev.get("cobro") != "pago":
        return 0.0
    return round(float(ev.get("precio_socio" if es_socio else "precio_publico") or 0), 2)


def afectacion(ev: dict) -> str:
    """IGV de la entrada: lo elegido en el evento o, si no, lo configurado para eventos. "" = por definir."""
    valor = ev.get("tipo_afectacion") or catalogos.get_facturacion()["afectacion_por_tipo"].get("evento", "")
    return valor if valor in ("10", "20", "30") else ""


def texto_precio(ev: dict) -> str:
    if ev.get("cobro") == "gratuito":
        return "Gratuito"
    if ev.get("cobro") == "pago":
        f = lambda v: config.soles(v) if v else "gratuito"  # noqa: E731
        s, p = precio(ev, True), precio(ev, False)
        return f(p) if s == p else f"Socios: {f(s)} · No socios: {f(p)}"
    return ev.get("precio") or ""


def ocupados(ev: dict) -> int:
    """Socios inscritos más las inscripciones web que no están en esa lista (no socios y pagos en trámite)."""
    miembros = {i.get("member_id") for i in ev.get("inscritos") or []}
    # Un pago sin informar retiene el cupo 48 horas: si no, cualquiera llena un evento sin pagar.
    corte = config.ahora() - timedelta(hours=HORAS_RESERVA)
    otras = ordenes_col.find({"tipo": "evento", "evento_id": str(ev["_id"]), "activa": True,
                              "$or": [{"estado": {"$ne": "por_pagar"}}, {"created_at": {"$gt": corte}}]},
                             {"member_id": 1})
    return len(miembros) + sum(1 for o in otras if not o.get("member_id") or o["member_id"] not in miembros)


def estado_inscripcion(ev: dict) -> str:
    """cancelado · pasado · externa · pronto · cerrada · llena · abierta"""
    if ev.get("estado") == "cancelado":
        return "cancelado"
    hoy = config.hoy()
    if (ev.get("fecha") or "") < hoy or ev.get("estado") == "finalizado":
        return "pasado"
    limite = ev.get("fecha_limite_inscripcion") or ""
    if limite and limite < hoy:
        return "cerrada"
    if not ev.get("inscripcion_web"):
        return "externa" if config.url_segura(ev.get("link_registro", "")) else "pronto"
    cupo = ev.get("cupo_max")
    if cupo and ocupados(ev) >= int(cupo):
        return "llena"
    return "abierta"


def eventos_de(persona: dict | None) -> set[str]:
    """Eventos en los que la persona está inscrita: por la web (confirmada), la plataforma o Zoom."""
    if not persona:
        return set()
    de_orden: list[dict] = [{"cuenta_id": persona["id"]}, {"email": persona["email"]}]
    de_evento: list[dict] = [{"zoom_registrants.email": persona["email"]}]
    if persona.get("member_id"):
        de_orden.append({"member_id": persona["member_id"]})
        de_evento += [{"inscritos.member_id": persona["member_id"]},
                      {"zoom_registrants.member_id": persona["member_id"]}]
    ids = {o["evento_id"] for o in ordenes_col.find(
        {"tipo": "evento", "estado": "confirmada", "$or": de_orden}, {"evento_id": 1})}
    ids |= {str(e["_id"]) for e in eventos_col.find({"$or": de_evento}, {"_id": 1})}
    return ids


# ── Grabaciones ───────────────────────────────────────────────────────────────

def youtube(url: str) -> str:
    """Dirección del reproductor, o "" si el enlace no es un video o lista de YouTube."""
    try:
        p = urlsplit((url or "").strip())
    except ValueError:
        return ""
    host = (p.hostname or "").removeprefix("www.").removeprefix("m.")
    if p.scheme not in ("https", "http") or host not in ("youtube.com", "youtu.be", "youtube-nocookie.com"):
        return ""
    q, partes = parse_qs(p.query), [x for x in p.path.split("/") if x]
    vid = ""
    if host == "youtu.be" and partes:
        vid = partes[0]
    elif partes[:1] == ["watch"]:
        vid = (q.get("v") or [""])[0]
    elif len(partes) >= 2 and partes[0] in ("live", "shorts", "embed"):
        vid = partes[1]
    if vid != "videoseries" and _RE_ID.fullmatch(vid):
        return f"https://www.youtube-nocookie.com/embed/{vid}?rel=0"
    lista = (q.get("list") or [""])[0]
    if _RE_LISTA.fullmatch(lista):
        return f"https://www.youtube-nocookie.com/embed/videoseries?list={lista}&rel=0"
    return ""


def grabaciones_de(ev: dict) -> list[dict]:
    filas = [{"titulo": "Grabación", "url": ev.get("link_grabacion") or ""}, *(ev.get("grabaciones_extra") or [])]
    res = []
    for f in filas:
        url = config.url_segura((f.get("url") or "").strip())
        if url.startswith("http"):
            res.append({"titulo": f.get("titulo") or f"Parte {len(res) + 1}", "url": url, "embed": youtube(url)})
    if len(res) > 1 and res[0]["titulo"] == "Grabación":
        res[0]["titulo"] = "Parte 1"
    return res


def nivel_vigente(ev: dict, hoy: str | None = None) -> str:
    hoy = hoy or config.hoy()
    nivel = ev.get("grabacion_nivel")
    if nivel not in NIVELES:
        nivel = config.cargar().get("grabacion_nivel_defecto")
        nivel = nivel if nivel in NIVELES else "socios"
    if nivel == "oculta":
        return nivel
    ap = ev.get("grabacion_apertura") or {}
    if ap.get("fecha") and ap["fecha"] <= hoy and ap.get("nivel") in NIVELES_APERTURA:
        nivel = min(nivel, ap["nivel"], key=_APERTURA.__getitem__)
    return nivel


def acceso(ev: dict, persona: dict | None, inscrito_en: set[str]) -> dict:
    """{puede, nivel, motivo, proxima}. Única puerta de las grabaciones."""
    hoy, nivel = config.hoy(), nivel_vigente(ev)
    ap = ev.get("grabacion_apertura") or {}
    proxima = ap if (ap.get("fecha") or "") > hoy and nivel != "oculta" else {}
    res = lambda puede, motivo="": {"puede": puede, "nivel": nivel, "motivo": motivo, "proxima": proxima}  # noqa: E731
    if nivel == "oculta" or ev.get("estado") == "cancelado" or ev.get("publicar_web") is False:
        return res(False, "oculta")
    if (ev.get("grabacion_desde") or "") > hoy:
        return res(False, "fecha")
    if nivel == "publico":
        return res(True)
    if not persona:
        return res(False, "ingresar")
    if not persona.get("registrada"):
        return res(False, "completar")
    if persona.get("socio") or nivel == "registrados":
        return res(True)
    if nivel == "inscritos" and str(ev["_id"]) in inscrito_en:
        return res(True)
    return res(False, nivel)


def se_anuncia(ev: dict, ac: dict) -> bool:
    """Si la grabación aparece en el listado. Una reunión sin publicar no muestra su título a extraños."""
    if ac["motivo"] == "oculta":
        return False
    abierta_a_propio = ev.get("grabacion_nivel") in NIVELES_APERTURA
    return bool(ac["puede"] or publicable(ev) or abierta_a_propio)


def biblioteca(persona: dict | None) -> list[dict]:
    """Listado de grabaciones. No lleva ni el enlace ni la miniatura de YouTube (revelaría el video)."""
    inscrito_en = eventos_de(persona) if persona and not persona.get("socio") else set()
    filas = []
    for ev in eventos_col.find(
        {"estado": {"$ne": "cancelado"},
         "$or": [{"link_grabacion": {"$nin": [None, ""]}}, {"grabaciones_extra.0": {"$exists": True}}]},
        {"zoom_registrants": 0, "inscritos": 0, "email_cuerpo": 0},
    ).sort("fecha", -1):
        partes = grabaciones_de(ev)
        if not partes:
            continue
        ac = acceso(ev, persona, inscrito_en)
        if not se_anuncia(ev, ac):
            continue
        filas.append({"_id": str(ev["_id"]), "titulo": ev.get("titulo", ""), "fecha": ev.get("fecha", ""),
                      "flyer": flyer(ev), "partes": len(partes), "acceso": ac,
                      "desde": ev.get("grabacion_desde", "")})
    return filas
