"""
Contexto que lee el agente de correos para responder.

Dos partes:
- `conocimiento()`: lo que sabe IPIDET (organización, eventos, comunicaciones, campañas,
  cronograma de redes, productos, FAQs). Es igual para todos los correos del día, así que
  va en el prompt de sistema y se cachea.
- `remitente(...)`: quién escribe (socio, prospecto, correos previos, hilo).

Todo sale de la base de datos: lo que el equipo edita en la plataforma es lo que el agente sabe.
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


# ── Conocimiento institucional ─────────────────────────────────────────────────

def _eventos() -> dict:
    hoy = hoy_lima().strftime("%Y-%m-%d")
    hace_60 = (hoy_lima() - timedelta(days=60)).strftime("%Y-%m-%d")
    proximos, recientes = [], []
    for e in db.eventos_ipidet.find({"estado": {"$ne": "cancelado"}, "fecha": {"$gte": hace_60}}).sort("fecha", 1):
        cupo = e.get("cupo_max")
        inscritos = len(e.get("inscritos") or []) + len(e.get("zoom_registrants") or [])
        base = {
            "evento_id": str(e["_id"]),
            "titulo": e.get("titulo"),
            "fecha": e.get("fecha"),
            "hora": e.get("hora"),
        }
        if (e.get("fecha") or "") >= hoy:
            proximos.append(_sin_vacios({
                **base,
                "modalidad": e.get("modalidad"),
                "lugar": e.get("lugar"),
                "precio": e.get("precio"),
                "inscripcion_hasta": e.get("fecha_limite_inscripcion"),
                "link_inscripcion": e.get("link_registro"),
                "link_zoom_solo_para_inscritos": e.get("link_zoom"),
                "cupos_libres": max(0, int(cupo) - inscritos) if str(cupo or "").isdigit() else None,
                "descripcion": _texto(e.get("descripcion"), 1200),
                "expositores": [
                    _sin_vacios({"nombre": x.get("nombre"), "cargo": x.get("titulo"), "institucion": x.get("empresa")})
                    for x in e.get("expositores") or []
                ],
                "invitacion_enviada": _sin_vacios({
                    "asunto": e.get("email_asunto"),
                    "texto": _texto(e.get("email_cuerpo"), 1800),
                }),
                "flyer": e.get("flyer_url"),
            }))
        else:
            recientes.append(_sin_vacios({**base, "link_grabacion": e.get("link_grabacion")}))
    return {"proximos": proximos, "recientes_ultimos_60_dias": recientes}


def _comunicaciones() -> dict:
    desde = datetime.now(timezone.utc) - timedelta(days=45)
    enviadas = [
        _sin_vacios({
            "fecha": fecha_lima(d.get("fecha")),
            "asunto": d.get("asunto"),
            "campania": d.get("campania"),
            "enviados": d.get("total_enviados"),
            "texto": _texto(d.get("cuerpo"), 900),
        })
        for d in db.comunicaciones.find({"fecha": {"$gte": desde}},
                                        {"fecha": 1, "asunto": 1, "campania": 1, "total_enviados": 1, "cuerpo": 1})
        .sort("fecha", -1).limit(10)
    ]
    campanas = [
        _sin_vacios({
            "fecha": fecha_lima(d.get("created_at")),
            "asunto": d.get("asunto"),
            "estado": d.get("estado"),
            "texto": _texto(d.get("cuerpo"), 900),
        })
        for d in db.campanas.find({"created_at": {"$gte": desde}}, {"created_at": 1, "asunto": 1, "estado": 1, "cuerpo": 1})
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
                     "titulo": d.get("titulo"), "estado": d.get("estado"), "descripcion": _texto(d.get("descripcion"), 300)})
        for d in db.redes_cronograma.find({"fecha_planificada": {"$gte": hoy, "$lte": en_30},
                                           "estado": {"$nin": ["cancelado", "borrador"]}}).sort("fecha_planificada", 1).limit(15)
    ]


def _productos() -> list:
    return [
        _sin_vacios({"producto": p.get("nombre"), "tipo": p.get("tipo"), "periodo": p.get("periodo"),
                     "precio_soles": p.get("precio")})
        for p in db.productos.find({"activo": True}, {"nombre": 1, "tipo": 1, "periodo": 1, "precio": 1}).sort("periodo", -1)
    ]


def _faqs() -> list:
    return [{"pregunta": f.get("question"), "respuesta": _texto(f.get("answer"), 1200)}
            for f in db.faqs.find({"active": True}, {"question": 1, "answer": 1}).limit(60)]


def conocimiento() -> str:
    """Bloque estable del prompt: cambia cuando el equipo cambia datos, no con cada correo."""
    org = catalogos.get_organizacion()
    cfg = catalogos.get_agente_config()
    perfil = catalogos.get_alianzas_config().get("perfil", "")
    datos = {
        "fecha_de_hoy": f"{hoy_lima():%Y-%m-%d} ({_DIAS[hoy_lima().weekday()]})",
        "periodo_de_cobro_actual": catalogos.get_periodo_actual(),
        "organizacion": _sin_vacios({**org, "descripcion": perfil}),
        "informacion_del_equipo_para_responder": cfg.get("informacion_adicional") or "",
        "medios_de_pago_aceptados": catalogos.get_catalogo("medios_pago"),
        "cuotas_y_productos": _productos(),
        "eventos": _eventos(),
        "comunicaciones": _comunicaciones(),
        "cronograma_de_redes_proximos_30_dias": _cronograma(),
        "preguntas_frecuentes": _faqs(),
    }
    return json.dumps(datos, ensure_ascii=False, indent=1, default=str)


# ── Remitente ──────────────────────────────────────────────────────────────────

def remitente(correo: dict, hilo: list[dict]) -> tuple[dict, dict]:
    """(contexto para Claude, vínculos para la plataforma: member_id / prospecto_id)."""
    email = correo.get("de_email", "")
    vinculos: dict = {}
    contexto: dict = {"email": email, "nombre_visible": correo.get("de_nombre", "")}

    socio = agente_db.buscar_socio_por_email(email)
    if socio:
        contexto["socio_verificado_por_email"] = agente_db.resumen_socio(socio)
        vinculos["member_id"] = socio["member_id"]
    else:
        posible = agente_db.buscar_socio_por_nombre(correo.get("de_nombre", ""))
        if posible:
            contexto["posible_socio_por_nombre_NO_verificado"] = {
                "member_id": posible["member_id"],
                "nombre": f"{posible.get('nombres', '')} {posible.get('apellidos', '')}".strip(),
            }
            vinculos["posible_member_id"] = posible["member_id"]

    prospecto = agente_db.buscar_prospecto(email)
    if prospecto:
        contexto["registro_en_publico"] = prospecto
        vinculos["prospecto_id"] = prospecto["prospecto_id"]

    previos = agente_db.historial_remitente(email, excluir_id=correo.get("_id"))
    if previos:
        contexto["correos_previos_a_la_cola"] = [
            _sin_vacios({"fecha": fecha_lima(p.get("fecha")),
                         "asunto": p.get("asunto"), "resumen": p.get("resumen"), "estado": p.get("estado"),
                         "respuesta_enviada": _texto(p.get("respuesta_enviada"), 500)})
            for p in previos
        ]

    if hilo:
        contexto["mensajes_anteriores_del_hilo"] = [
            {"fecha": fecha_lima(m.get("fecha"), "%Y-%m-%d %H:%M"),
             "de": m.get("de"),
             "texto": _texto(sin_citas(m.get("cuerpo") or ""), 1500)}
            for m in hilo
        ]
    return contexto, vinculos
