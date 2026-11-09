"""
Contexto que lee el agente de correos para responder.

El conocimiento se arma en dos niveles:
- FUENTES AUTORIZADAS (`fuentes_autorizadas()`): FAQs marcadas «puede responderse sola», eventos
  con «usar en el agente» y los datos de contacto de la organización. Cada una lleva un id
  (faq:<id>, evento:<id>, org). Una respuesta solo puede salir sin revisión si se apoya
  únicamente en estas fuentes (lo verifica agente_correo, no el modelo).
- CONTEXTO DE APOYO: FAQs sin autorizar, cuotas, comunicaciones, campañas, cronograma e
  información adicional. Sirve para que el borrador sea útil, pero nunca para enviar solo.

Los eventos sin «usar en el agente» no se incluyen en ningún nivel: están a medio cargar.
`remitente(...)` arma lo que se sabe de quien escribe (siempre es contexto de apoyo).
"""
import json
import re
from datetime import datetime, timedelta, timezone

from config import catalogos
from config.mongo_client import db
from gmail.client import html_a_texto, sin_citas
from webapp import agente_db

_LIMA = timezone(timedelta(hours=-5))
_DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


def hoy_lima() -> datetime:
    return datetime.now(_LIMA)


def fecha_lima(valor, formato: str = "%Y-%m-%d") -> str:
    """Fecha de MongoDB (sin zona = UTC) o de Gmail, en hora de Lima."""
    if not isinstance(valor, datetime):
        return str(valor or "")[:10]
    if valor.tzinfo is None:
        valor = valor.replace(tzinfo=timezone.utc)
    return valor.astimezone(_LIMA).strftime(formato)


def _texto(valor, limite: int = 1500) -> str:
    texto = str(valor or "").strip()
    if "<" in texto and ">" in texto:
        texto = html_a_texto(texto)
    texto = re.sub(r"\s+\n", "\n", texto)
    return texto if len(texto) <= limite else texto[:limite].rstrip() + "…"


def _sin_vacios(d: dict) -> dict:
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


# ── Fuentes autorizadas ────────────────────────────────────────────────────────

def _evento_como_fuente(e: dict, hoy: str) -> dict:
    cupo = e.get("cupo_max")
    inscritos = len(e.get("inscritos") or []) + len(e.get("zoom_registrants") or [])
    proximo = (e.get("fecha") or "") >= hoy
    return _sin_vacios({
        "titulo": e.get("titulo"),
        "fecha": e.get("fecha"),
        "hora": e.get("hora"),
        "ya_se_realizo": not proximo,
        "modalidad": e.get("modalidad"),
        "lugar": e.get("lugar"),
        "precio": e.get("precio"),
        "inscripcion_hasta": e.get("fecha_limite_inscripcion"),
        "link_inscripcion": e.get("link_registro") if proximo else None,
        "link_grabacion": e.get("link_grabacion"),
        "cupos_libres": max(0, int(cupo) - inscritos) if str(cupo or "").isdigit() and proximo else None,
        "descripcion": _texto(e.get("descripcion"), 1500),
        "expositores": [
            _sin_vacios({"nombre": x.get("nombre"), "cargo": x.get("titulo"), "institucion": x.get("empresa")})
            for x in e.get("expositores") or []
        ],
        "informacion_para_consultas": _texto(e.get("agente_info"), 3000),
        "preguntas_y_respuestas": [{"pregunta": f["pregunta"], "respuesta": f["respuesta"]}
                                   for f in e.get("agente_faqs") or []],
        "invitacion_enviada": _texto(e.get("email_cuerpo"), 1500) if proximo else None,
    })


def fuentes_autorizadas() -> dict[str, dict]:
    """{id: {"titulo", "datos"}} — lo único en que puede basarse una respuesta que sale sola."""
    fuentes: dict[str, dict] = {}
    org = catalogos.get_organizacion()
    fuentes["org"] = {"titulo": "Datos de contacto de IPIDET",
                      "datos": _sin_vacios({k: org.get(k) for k in ("email", "web", "telefono", "ciudad")})}
    for f in db.faqs.find({"active": True, "autorizada": True}):
        fuentes[f"faq:{f['_id']}"] = {"titulo": f.get("question", ""),
                                     "datos": {"categoria": f.get("category"), "pregunta": f.get("question"),
                                               "respuesta": _texto(f.get("answer"), 3000)}}
    hoy = hoy_lima().strftime("%Y-%m-%d")
    hace_90 = (hoy_lima() - timedelta(days=90)).strftime("%Y-%m-%d")
    for e in db.eventos_ipidet.find({"usar_en_agente": True, "estado": {"$ne": "cancelado"},
                                     "fecha": {"$gte": hace_90}}).sort("fecha", 1):
        fuentes[f"evento:{e['_id']}"] = {"titulo": e.get("titulo", ""), "datos": _evento_como_fuente(e, hoy)}
    return fuentes


# ── Contexto de apoyo (solo borradores) ────────────────────────────────────────

def _comunicaciones() -> dict:
    desde = datetime.now(timezone.utc) - timedelta(days=45)
    enviadas = [
        _sin_vacios({"fecha": fecha_lima(d.get("fecha")), "asunto": d.get("asunto"), "campania": d.get("campania"),
                     "texto": _texto(d.get("cuerpo"), 900)})
        for d in db.comunicaciones.find({"fecha": {"$gte": desde}}, {"fecha": 1, "asunto": 1, "campania": 1, "cuerpo": 1})
        .sort("fecha", -1).limit(10)
    ]
    campanas = [
        _sin_vacios({"fecha": fecha_lima(d.get("created_at")), "asunto": d.get("asunto"), "texto": _texto(d.get("cuerpo"), 900)})
        for d in db.campanas.find({"created_at": {"$gte": desde}}, {"created_at": 1, "asunto": 1, "cuerpo": 1})
        .sort("created_at", -1).limit(5)
    ]
    programadas = [
        _sin_vacios({"fecha_envio": str(d.get("fecha_envio") or "")[:16], "asunto": d.get("asunto"),
                     "texto": _texto(d.get("cuerpo"), 600)})
        for d in db.comunicaciones_programadas.find({"estado": "pendiente"}).sort("fecha_envio", 1).limit(5)
    ]
    return {"enviadas_ultimos_45_dias": enviadas, "campanas": campanas, "programadas": programadas}


def _cronograma() -> list:
    hoy = hoy_lima().strftime("%Y-%m-%d")
    en_30 = (hoy_lima() + timedelta(days=30)).strftime("%Y-%m-%d")
    return [
        _sin_vacios({"fecha": d.get("fecha_planificada"), "plataforma": d.get("plataforma"),
                     "titulo": d.get("titulo"), "descripcion": _texto(d.get("descripcion"), 300)})
        for d in db.redes_cronograma.find({"fecha_planificada": {"$gte": hoy, "$lte": en_30},
                                           "estado": {"$nin": ["cancelado", "borrador"]}}).sort("fecha_planificada", 1).limit(15)
    ]


def _apoyo() -> dict:
    cfg = catalogos.get_agente_config()
    return {
        "descripcion_de_ipidet": catalogos.get_alianzas_config().get("perfil", ""),
        "informacion_del_equipo": cfg.get("informacion_adicional") or "",
        "faqs_sin_autorizar": [{"pregunta": f.get("question"), "respuesta": _texto(f.get("answer"), 1200)}
                               for f in db.faqs.find({"active": True, "autorizada": {"$ne": True}}).limit(60)],
        "comunicaciones": _comunicaciones(),
        "cronograma_de_redes": _cronograma(),
    }


def conocimiento() -> tuple[str, dict[str, dict]]:
    """(texto para el prompt de sistema, fuentes autorizadas). Igual para todos los correos del día."""
    fuentes = fuentes_autorizadas()
    texto = (
        f"Fecha de hoy: {hoy_lima():%Y-%m-%d} ({_DIAS[hoy_lima().weekday()]}).\n\n"
        "FUENTES AUTORIZADAS (cada una con su id; cítalas en fuentes_usadas):\n"
        + json.dumps({k: v["datos"] for k, v in fuentes.items()}, ensure_ascii=False, indent=1, default=str)
        + "\n\nCONTEXTO DE APOYO (no autorizado: si lo usas, la respuesta irá a revisión del equipo):\n"
        + json.dumps(_apoyo(), ensure_ascii=False, indent=1, default=str)
    )
    return texto, fuentes


# ── Remitente ──────────────────────────────────────────────────────────────────

def remitente(correo: dict, hilo: list[dict]) -> tuple[dict, dict]:
    """(contexto para Claude, vínculos para la plataforma: member_id / prospecto_id).

    Claude no recibe datos de socios ni de cobranzas: solo sabe si el remitente es socio, para el
    trato. Los vínculos (socio, ficha en Público) son para que el equipo los vea en la cola.
    """
    email = correo.get("de_email", "")
    vinculos: dict = {}
    socio = agente_db.buscar_socio_por_email(email)
    contexto: dict = {"email": email, "nombre_visible": correo.get("de_nombre", ""),
                      "es_socio_de_ipidet": bool(socio)}
    if socio:
        vinculos["member_id"] = socio["member_id"]
    else:
        posible = agente_db.buscar_socio_por_nombre(correo.get("de_nombre", ""))
        if posible:
            vinculos["posible_member_id"] = posible["member_id"]

    prospecto = agente_db.buscar_prospecto(email)
    if prospecto:
        vinculos["prospecto_id"] = prospecto["prospecto_id"]

    previos = agente_db.historial_remitente(email, excluir_id=correo.get("_id"))
    if previos:
        contexto["correos_previos_a_la_cola"] = [
            _sin_vacios({"fecha": fecha_lima(p.get("fecha")), "asunto": p.get("asunto"), "resumen": p.get("resumen"),
                         "estado": p.get("estado"), "respuesta_enviada": _texto(p.get("respuesta_enviada"), 500)})
            for p in previos
        ]

    if hilo:
        contexto["mensajes_anteriores_del_hilo"] = [
            {"fecha": fecha_lima(m.get("fecha"), "%Y-%m-%d %H:%M"), "de": m.get("de"),
             "texto": _texto(sin_citas(m.get("cuerpo") or ""), 1500)}
            for m in hilo
        ]
    return contexto, vinculos
