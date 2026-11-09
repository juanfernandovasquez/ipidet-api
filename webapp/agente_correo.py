"""
Agente de correos: corre dentro de la web (scheduler.run_agente_correo_scheduler).

Lee dos buzones (GMAIL_ADDRESS, la principal, y GMAIL_ADDRESS_2, administración). Cada correo
guarda la cuenta a la que llegó (`cuenta`) y la cuenta desde la que se responde
(`cuenta_respuesta`), que se elige por categoría: lo delicado (config `categorias_cuenta_2`)
sale desde administración y nunca solo; el resto, desde la principal.

Ciclo (`ciclo()`):
  1. Lee los correos nuevos del INBOX de cada cuenta desde el último UID guardado en la base.
  2. Filtra sin gastar API: remitentes bloqueados, correos automáticos y remitentes internos.
  3. Analiza el resto con Claude (agente_ia) usando el conocimiento de la plataforma y el hilo.
  4. Deja el borrador en la cola (/inbox). En modo automático envía solo lo que cumple
     todas las condiciones de `_motivos_para_no_enviar_solo`.
  5. Reintenta los análisis fallidos y marca los pendientes que el equipo ya respondió en Gmail.

Un candado en la base (agente_db.reservar_ciclo) garantiza un solo ciclo a la vez, aunque
corran la web y main.py en paralelo.
"""
import hashlib
import html as _html
import json
import logging
import os
import re
import urllib.error
import urllib.request
import base64
import threading
from datetime import datetime, timedelta, timezone
from email.utils import formataddr

import requests

from config import catalogos
from config.settings import (
    ADMIN_FORWARD_EMAIL, BREVO_API_KEY, BREVO_FROM_EMAIL, BREVO_FROM_NAME,
    GMAIL_ADDRESS, GMAIL_ADDRESS_2, GMAIL_APP_PASSWORD, GMAIL_APP_PASSWORD_2, PORTAL_API_BASE, SECRET_KEY,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
)
from gmail.client import GmailClient, detectar_automatico, email_de, parsear
from webapp import agente_contexto, agente_db, agente_ia

logger = logging.getLogger("agente_correo")

_PRIORIDAD_ORDEN = {"alta": 0, "media": 1, "baja": 2}
_MARCADOR = re.compile(r"\[\s*COMPLETAR", re.I)
_LIMA = timezone(timedelta(hours=-5))


# ── Cuentas ────────────────────────────────────────────────────────────────────

def cuentas() -> dict[str, str]:
    """{correo: contraseña de aplicación} de los buzones que lee el agente; el primero es el principal."""
    pares = [(GMAIL_ADDRESS, GMAIL_APP_PASSWORD), (GMAIL_ADDRESS_2, GMAIL_APP_PASSWORD_2)]
    return {e.strip().lower(): p for e, p in pares if e and p}


def cuenta_principal() -> str:
    return (GMAIL_ADDRESS or "").strip().lower()


def cuenta_2() -> str:
    """La cuenta de administración (temas delicados), si está configurada."""
    c = (GMAIL_ADDRESS_2 or "").strip().lower()
    return c if c in cuentas() else ""


def cuenta_de(doc: dict) -> str:
    """Buzón al que llegó el correo (los anteriores a la multicuenta llegaron a la principal)."""
    return (doc.get("cuenta") or cuenta_principal()).lower()


def cuenta_respuesta(doc: dict) -> str:
    """Cuenta desde la que sale la respuesta; si no está configurada, la que lo recibió."""
    c = (doc.get("cuenta_respuesta") or "").lower()
    return c if c in cuentas() else cuenta_de(doc)


def elegir_cuenta_respuesta(cfg: dict, categoria: str, recibida: str) -> str:
    if cuenta_2() and categoria in (cfg.get("categorias_cuenta_2") or []):
        return cuenta_2()
    if categoria in ("interno", "automatico"):
        return recibida
    return cuenta_principal() or recibida


def _gmail(cuenta: str = "") -> GmailClient:
    todas = cuentas()
    if not todas:
        raise RuntimeError("Faltan GMAIL_ADDRESS y GMAIL_APP_PASSWORD en el entorno del servidor.")
    cuenta = (cuenta or cuenta_principal()).lower()
    if cuenta not in todas:
        raise RuntimeError(f"La cuenta {cuenta} no está configurada en el servidor.")
    return GmailClient(cuenta, todas[cuenta])


def direcciones_propias() -> set[str]:
    org = catalogos.get_organizacion()
    return {e.strip().lower() for e in (*cuentas(), GMAIL_ADDRESS, GMAIL_ADDRESS_2, BREVO_FROM_EMAIL,
                                        org.get("email"), ADMIN_FORWARD_EMAIL) if e}


def retroceder(recientes: int) -> None:
    """«Probar con correos recientes»: vuelve a leer los últimos N de cada buzón (los ya registrados no se repiten)."""
    for cuenta, clave in cuentas().items():
        max_uid, validez = GmailClient(cuenta, clave).max_uid()
        agente_db.set_estado_cuenta(cuenta, ultimo_uid=max(0, max_uid - min(recientes, 50)), uidvalidity=validez)


def url_plataforma() -> str:
    return (os.getenv("RENDER_EXTERNAL_URL") or PORTAL_API_BASE or "").rstrip("/")


# ── Ciclo ──────────────────────────────────────────────────────────────────────

def ciclo(forzar: bool = False) -> dict:
    cfg = catalogos.get_agente_config()
    if not cfg.get("activo") and not forzar:
        return {"omitido": "El agente está apagado."}
    if not agente_db.reservar_ciclo():
        return {"omitido": "Ya hay una revisión en curso."}

    resumen = {"nuevos": 0, "analizados": 0, "sin_respuesta": 0, "enviados_solos": 0, "errores": 0}
    ahora = datetime.now(timezone.utc)
    try:
        agente_db.asegurar_indices()
        agente_db.liberar_envios_trabados()
        agente_db.liberar_analisis_trabados()
        if not cuentas():
            raise RuntimeError("Faltan GMAIL_ADDRESS y GMAIL_APP_PASSWORD en el entorno del servidor.")
        clientes = {c: GmailClient(c, p) for c, p in cuentas().items()}
        cache: dict = {}
        errores, pendientes = [], 0
        for cuenta, gmail in clientes.items():
            try:
                pendientes += _leer_cuenta(gmail, cfg, cache, resumen)
            except Exception as e:
                # Un buzón caído (contraseña cambiada, Gmail no responde) no detiene al otro.
                logger.exception("Agente de correos: no se pudo leer %s", cuenta)
                agente_db.set_estado_cuenta(cuenta, ultimo_error=str(e)[:300])
                errores.append(f"{cuenta}: {e}")

        for doc in agente_db.para_reintentar():
            if cache.get("api_no_disponible"):
                break
            gmail = clientes.get(cuenta_de(doc))
            if gmail:
                resultado = reprocesar(str(doc["_id"]), gmail=gmail, cache=cache)
                resumen[resultado] = resumen.get(resultado, 0) + 1

        for cuenta, gmail in clientes.items():
            _verificar_respondidos_en_gmail(gmail, cuenta == cuenta_principal())
        error = "; ".join(filter(None, [cache.get("api_no_disponible"), *errores]))[:500] or None
        agente_db.liberar_ciclo(ultima_revision=ahora, ultimo_error=error, ultimo_resumen=resumen,
                                pendientes_en_inbox=pendientes)
    except Exception as e:
        logger.exception("Agente de correos: ciclo fallido")
        agente_db.liberar_ciclo(ultima_revision=ahora, ultimo_error=str(e)[:500], ultimo_resumen=resumen)
        resumen["error"] = str(e)
    return resumen


def _leer_cuenta(gmail: GmailClient, cfg: dict, cache: dict, resumen: dict) -> int:
    """Procesa los correos nuevos de un buzón. Devuelve cuántos quedan sin leer (tope por ciclo)."""
    cuenta = gmail.address
    estado = agente_db.get_estado_cuenta(cuenta, principal=cuenta == cuenta_principal())
    max_uid, validez = gmail.max_uid()
    ultimo = estado.get("ultimo_uid")
    if ultimo is None or (estado.get("uidvalidity") and estado.get("uidvalidity") != validez):
        # Primera vez (o Gmail renumeró): se empieza desde ahora, sin procesar el histórico.
        ultimo = max_uid
        agente_db.set_estado_cuenta(cuenta, ultimo_uid=max_uid, uidvalidity=validez)

    mensajes, _ = gmail.nuevos(int(ultimo), int(cfg.get("max_por_ciclo") or 15))
    agente_db.set_estado_cuenta(cuenta, ultimo_uid=int(ultimo))
    for msg in mensajes:
        msg["cuenta"] = cuenta
        resultado = procesar_mensaje(gmail, msg, cfg, cache)
        if resultado:
            resumen["nuevos"] += 1
            resumen[resultado] = resumen.get(resultado, 0) + 1
        agente_db.set_estado_cuenta(cuenta, ultimo_uid=msg["uid"])
    agente_db.set_estado_cuenta(cuenta, uidvalidity=validez, ultima_revision=datetime.now(timezone.utc), ultimo_error=None)
    return max(0, max_uid - int(ultimo) - len(mensajes))


def _filtro_previo(msg: dict, cfg: dict) -> tuple[str, str] | None:
    """(categoría, motivo) si el correo no necesita análisis ni respuesta."""
    de = msg.get("de_email", "")
    if agente_db.coincide(de, agente_db.patrones_bloqueados() + list(cfg.get("remitentes_ignorar") or [])):
        return "automatico", "Remitente bloqueado o en la lista para ignorar"
    if not agente_db.coincide(de, list(cfg.get("remitentes_permitidos") or [])):
        motivo = detectar_automatico(msg)
        if motivo:
            return "automatico", motivo
    if agente_db.coincide(de, list(cfg.get("remitentes_internos") or [])):
        return "interno", "Remitente interno: el agente no responde conversaciones del equipo"
    return None


def procesar_mensaje(gmail: GmailClient, msg: dict, cfg: dict, cache: dict) -> str | None:
    if msg.get("de_email") in direcciones_propias():
        return None
    correo_id = agente_db.registrar_recibido(msg)
    if not correo_id:
        return None  # ya estaba en la cola

    filtrado = _filtro_previo(msg, cfg)
    if filtrado:
        categoria, motivo = filtrado
        agente_db.guardar_analisis(correo_id, {
            "categoria": categoria, "prioridad": "baja", "prioridad_orden": 2,
            "requiere_respuesta": False, "accion": "ignorar", "resumen": motivo, "motivo": motivo,
            "filtrado_sin_ia": True,
        }, "ignorado", detalle=motivo)
        return "sin_respuesta"
    try:
        return _analizar(gmail, agente_db.get_correo(correo_id), msg.get("adjuntos", []), cfg, cache)
    except Exception as e:
        logger.exception("Agente de correos: error analizando %s", msg.get("asunto"))
        agente_db.marcar_error(correo_id, f"Error inesperado: {e}")
        return "errores"


def _conocimiento(cache: dict) -> tuple[str, dict]:
    """(texto del prompt, fuentes autorizadas). Se arma una vez por ciclo."""
    if "conocimiento" not in cache:
        cache["conocimiento"] = agente_contexto.conocimiento()
    return cache["conocimiento"]


def _hilo(gmail: GmailClient, doc: dict) -> list[dict]:
    try:
        return gmail.hilo(doc.get("gm_thrid", ""), excluir_msgid=doc.get("gm_msgid", ""))
    except Exception as e:
        logger.warning("No se pudo leer el hilo de %s: %s", doc.get("asunto"), e)
        return []


def _analizar(gmail: GmailClient, doc: dict, adjuntos: list[dict], cfg: dict, cache: dict) -> str:
    correo_id = str(doc["_id"])
    hilo = _hilo(gmail, doc)
    contexto, vinculos = agente_contexto.remitente(doc, hilo)
    texto_conocimiento, fuentes = _conocimiento(cache)
    try:
        analisis, uso = agente_ia.analizar(doc, contexto, adjuntos, texto_conocimiento, cuenta_de(doc))
    except agente_ia.AgenteIAError as e:
        agente_db.marcar_error(correo_id, str(e), contar_intento=not e.transitorio)
        if e.transitorio:
            cache["api_no_disponible"] = str(e)
        return "errores"

    respuesta = (analisis.get("respuesta") or "").strip()
    estado = "pendiente" if analisis.get("requiere_respuesta") and analisis.get("accion") != "ignorar" else "ignorado"
    evento_id = analisis.get("evento_id")
    if evento_id and not re.fullmatch(r"[0-9a-f]{24}", evento_id):
        evento_id = None
    propias = direcciones_propias()
    campos = {
        **{k: analisis.get(k) for k in ("categoria", "prioridad", "requiere_respuesta", "accion",
                                         "fuentes_usadas", "solo_fuentes_autorizadas", "datos_sensibles",
                                         "puede_enviarse_sin_revision", "confianza", "resumen", "motivo",
                                         "datos_faltantes")},
        "fuentes_titulos": {f: fuentes[f]["titulo"] for f in analisis.get("fuentes_usadas") or [] if f in fuentes},
        "prioridad_orden": _PRIORIDAD_ORDEN.get(analisis.get("prioridad"), 1),
        "respuesta_borrador": respuesta,
        "borrador_original": respuesta,
        "evento_id": evento_id,
        "member_id": vinculos.get("member_id"),
        "posible_member_id": vinculos.get("posible_member_id"),
        "prospecto_id": vinculos.get("prospecto_id"),
        "contacto": {k: (v or "").strip()[:120] for k, v in (analisis.get("contacto") or {}).items()},
        "hilo_previo": [{"de": m.get("de"), "fecha": m.get("fecha"), "texto": (m.get("cuerpo") or "")[:1500]} for m in hilo],
        "hilo_con_equipo": any(email_de(m.get("de", "")) in propias for m in hilo),
        "uso": uso,
        "analizado_at": datetime.now(timezone.utc),
        "error": None,
    }
    if not doc.get("cuenta_respuesta_manual"):
        campos["cuenta_respuesta"] = elegir_cuenta_respuesta(cfg, analisis.get("categoria"), cuenta_de(doc))
    motivos: list[str] = []
    if estado == "pendiente":
        motivos, verificacion, uso_verificacion = evaluar_envio_auto(cfg, {**doc, **campos}, fuentes)
        campos["verificacion"] = verificacion
        if uso_verificacion:
            uso["costo_usd"] = round(uso["costo_usd"] + uso_verificacion["costo_usd"], 5)
    campos["envio_auto_bloqueado_por"] = motivos
    agente_db.guardar_analisis(correo_id, campos, estado, detalle=analisis.get("resumen") or "")

    if estado == "pendiente" and not motivos:
        ok, _ = enviar_respuesta(correo_id, respuesta, usuario="agente", automatico=True, gmail=gmail)
        if ok:
            avisar_telegram_enviado_solo(agente_db.get_correo(correo_id))
            return "enviados_solos"
    if estado == "pendiente":
        avisar_telegram(agente_db.get_correo(correo_id))
    return "analizados" if estado == "pendiente" else "sin_respuesta"


def _motivos_para_no_enviar_solo(cfg: dict, doc: dict, fuentes: dict) -> list[str]:
    """Condiciones del envío automático, sin costo. Lista vacía = pasa a la verificación final.

    La regla de fondo: solo sale sola una respuesta que se apoya únicamente en FAQs autorizadas,
    eventos habilitados o los datos de contacto. Todo lo demás queda en borrador.
    """
    motivos = []
    if cfg.get("modo") != "automatico":
        motivos.append("El agente está en modo solo borradores")
    if doc.get("categoria") not in catalogos.CATEGORIAS_CORREO_AUTO:
        etiqueta = catalogos.CATEGORIAS_CORREO.get(doc.get("categoria"), (doc.get("categoria"),))[0]
        motivos.append(f"Los correos de tipo «{etiqueta}» siempre los revisa el equipo")
    desde = doc.get("cuenta_respuesta") or cuenta_de(doc)
    if desde != cuenta_principal():
        motivos.append(f"Se responde desde {desde}: lo de esa cuenta siempre lo revisa el equipo")
    if doc.get("accion") != "responder":
        motivos.append("El agente sugiere revisarlo antes")
    usadas = doc.get("fuentes_usadas") or []
    sin_autorizar = [f for f in usadas if f not in fuentes]
    if not [f for f in usadas if f != "org"]:
        motivos.append("No se apoya en ninguna FAQ ni evento autorizado")
    if sin_autorizar:
        motivos.append("Cita fuentes que no están autorizadas")
    if not doc.get("solo_fuentes_autorizadas"):
        motivos.append("Usa información que no está en FAQs ni eventos autorizados")
    if doc.get("datos_sensibles"):
        motivos.append("Toca datos personales, dinero o temas internos")
    if not doc.get("puede_enviarse_sin_revision"):
        motivos.append("El agente indicó que necesita revisión")
    if float(doc.get("confianza") or 0) < float(cfg.get("umbral_auto") or 0.9):
        motivos.append(f"Confianza {int(float(doc.get('confianza') or 0) * 100)} % por debajo del mínimo")
    if doc.get("datos_faltantes") or _MARCADOR.search(doc.get("respuesta_borrador") or ""):
        motivos.append("Faltan datos por completar")
    if not (doc.get("respuesta_borrador") or "").strip():
        motivos.append("No hay borrador")
    if doc.get("adjuntos"):
        motivos.append("El correo trae adjuntos")
    if doc.get("prioridad") == "alta":
        motivos.append("Prioridad alta")
    if doc.get("hilo_con_equipo"):
        motivos.append("El equipo ya conversa con esta persona en el hilo")
    if agente_db.auto_enviados_recientes(doc.get("de_email", "")) >= int(cfg.get("max_auto_por_remitente_dia") or 2):
        motivos.append("Ya se le respondió automáticamente varias veces hoy")
    if agente_db.auto_enviados_hoy() >= int(cfg.get("max_auto_dia") or 30):
        motivos.append("Se alcanzó el tope diario de respuestas automáticas")
    return motivos


def evaluar_envio_auto(cfg: dict, doc: dict, fuentes: dict) -> tuple[list[str], dict | None, dict | None]:
    """(motivos para no enviar solo, verificación, uso de la verificación).

    Si pasan todas las reglas, una segunda llamada revisa la respuesta contra el texto de las
    fuentes citadas y nada más. Si encuentra algo sin respaldo, queda en borrador.
    """
    motivos = _motivos_para_no_enviar_solo(cfg, doc, fuentes)
    if motivos:
        return motivos, None, None
    citadas = {f: fuentes[f] for f in doc.get("fuentes_usadas") or [] if f in fuentes}
    try:
        verificacion, uso = agente_ia.verificar(doc.get("respuesta_borrador") or "", doc.get("cuerpo") or "", citadas)
    except agente_ia.AgenteIAError as e:
        return [f"No se pudo hacer la verificación final: {e}"], None, None
    if not verificacion.get("respaldada"):
        problemas = "; ".join((verificacion.get("problemas") or [])[:3]) or "sin detalle"
        return [f"La verificación encontró datos sin respaldo: {problemas}"], verificacion, uso
    return [], verificacion, uso


def probar(texto: str, asunto: str = "", de_email: str = "") -> dict:
    """Simula un correo y muestra qué haría el agente, sin guardar ni enviar nada (página de FAQs)."""
    doc = {
        "de": de_email or "prueba@ejemplo.com", "de_email": (de_email or "prueba@ejemplo.com").lower(),
        "de_nombre": "", "asunto": asunto or "Consulta", "cuerpo": texto, "fecha": datetime.now(timezone.utc),
        "para": [GMAIL_ADDRESS], "adjuntos": [],
    }
    contexto, _ = agente_contexto.remitente(doc, [])
    texto_conocimiento, fuentes = agente_contexto.conocimiento()
    analisis, uso = agente_ia.analizar(doc, contexto, [], texto_conocimiento, GMAIL_ADDRESS)
    cfg = {**catalogos.get_agente_config(), "modo": "automatico"}
    doc.update({k: analisis.get(k) for k in analisis}, respuesta_borrador=(analisis.get("respuesta") or "").strip(),
               cuenta_respuesta=elegir_cuenta_respuesta(cfg, analisis.get("categoria"), cuenta_principal()))
    motivos, verificacion, uso_v = evaluar_envio_auto(cfg, doc, fuentes) if analisis.get("requiere_respuesta") else (
        ["El agente considera que este correo no necesita respuesta"], None, None)
    return {
        "categoria": catalogos.CATEGORIAS_CORREO.get(analisis.get("categoria"), ("",))[0],
        "respuesta": doc["respuesta_borrador"],
        "resumen": analisis.get("resumen"),
        "fuentes": [fuentes[f]["titulo"] if f in fuentes else f"{f} (no autorizada)" for f in analisis.get("fuentes_usadas") or []],
        "saldria_solo": not motivos,
        "responderia_desde": doc["cuenta_respuesta"],
        "motivos": motivos,
        "verificacion": verificacion,
        "costo_usd": round(uso["costo_usd"] + (uso_v or {}).get("costo_usd", 0), 4),
    }


def reprocesar(correo_id: str, gmail: GmailClient | None = None, cache: dict | None = None) -> str:
    """Vuelve a analizar un correo (tras un error o a pedido del equipo)."""
    doc = agente_db.get_correo(correo_id)
    if not doc:
        return "errores"
    gmail = gmail or _gmail(cuenta_de(doc))
    adjuntos = []
    if doc.get("gm_msgid"):
        try:
            raw = gmail.original(doc["gm_msgid"])
            if raw:
                adjuntos = parsear(raw)["adjuntos"]
        except Exception as e:
            logger.warning("No se pudieron releer los adjuntos: %s", e)
    agente_db.actualizar(correo_id, {"estado": "procesando"}, accion="reanálisis")
    try:
        return _analizar(gmail, agente_db.get_correo(correo_id), adjuntos, catalogos.get_agente_config(),
                         cache if cache is not None else {})
    except Exception as e:
        logger.exception("Agente de correos: error reanalizando %s", correo_id)
        agente_db.marcar_error(correo_id, f"Error inesperado: {e}")
        return "errores"


def _verificar_respondidos_en_gmail(gmail: GmailClient, principal: bool) -> None:
    propias = direcciones_propias()
    for doc in agente_db.pendientes_para_verificar(gmail.address, principal):
        try:
            if gmail.respondido_despues(doc["gm_thrid"], doc["fecha"], propias):
                agente_db.actualizar(str(doc["_id"]), {"estado": "respondido_gmail"},
                                     accion="respondido desde Gmail", detalle="El equipo respondió el hilo fuera de la plataforma")
                _cerrar_telegram(agente_db.get_correo(str(doc["_id"])), "✔ Respondido en Gmail")
        except Exception as e:
            logger.warning("No se pudo verificar el hilo %s: %s", doc.get("gm_thrid"), e)


# ── Acciones sobre un correo ───────────────────────────────────────────────────

# Marca que deja la plataforma donde va una imagen del texto: [imagen: nombre.png]
MARCA_IMAGEN = re.compile(r"\[\s*imagen\s*:\s*([^\]\n]+?)\s*\]", re.I)


def _cuerpo_html(texto: str, imagenes: dict[str, str]) -> str:
    """Texto plano a HTML; cada [imagen: nombre] pasa a <img> con la dirección pública de la imagen."""
    partes, ultimo = [], 0
    for m in MARCA_IMAGEN.finditer(texto):
        partes.append(_html.escape(texto[ultimo:m.start()]).replace("\n", "<br>\n"))
        url = imagenes.get(m.group(1).strip().lower())
        partes.append(f'<img src="{_html.escape(url)}" alt="{_html.escape(m.group(1).strip())}" '
                      'style="max-width:100%;height:auto;display:block;margin:8px 0">' if url else _html.escape(m.group(0)))
        ultimo = m.end()
    partes.append(_html.escape(texto[ultimo:]).replace("\n", "<br>\n"))
    return "".join(partes)


def imagenes_faltantes(texto: str, archivos: list[dict]) -> list[str]:
    """Marcas [imagen: …] del texto sin una imagen cargada (se borró o se escribió a mano)."""
    nombres = {a["nombre"].lower() for a in archivos if a["tipo"] == "imagen"}
    return [m.group(1).strip() for m in MARCA_IMAGEN.finditer(texto) if m.group(1).strip().lower() not in nombres]


def _html_respuesta(texto: str, doc: dict, imagenes: dict[str, str] | None = None) -> str:
    cuerpo = _cuerpo_html(texto, imagenes or {})
    fecha = agente_contexto.fecha_lima(doc.get("fecha"), "%d/%m/%Y %H:%M")
    original = _html.escape((doc.get("cuerpo") or "")[:4000]).replace("\n", "<br>\n")
    return (
        '<div style="font-family:Arial,sans-serif;font-size:14px;line-height:1.6;color:#1f2937">'
        f"{cuerpo}</div><br>"
        f'<div style="font-family:Arial,sans-serif;font-size:13px;color:#6b7280">El {fecha}, {_html.escape(doc.get("de", ""))} escribió:</div>'
        f'<blockquote style="margin:4px 0 0 4px;padding-left:10px;border-left:2px solid #d1d5db;color:#6b7280;'
        f'font-family:Arial,sans-serif;font-size:13px">{original}</blockquote>'
    )


def _texto_respuesta(texto: str, doc: dict) -> str:
    fecha = agente_contexto.fecha_lima(doc.get("fecha"), "%d/%m/%Y %H:%M")
    cita = "\n".join(f"> {l}" for l in (doc.get("cuerpo") or "")[:4000].splitlines())
    return f"{texto.rstrip()}\n\nEl {fecha}, {doc.get('de', '')} escribió:\n{cita}\n"


def _enviar_por_brevo(msg, html: str, texto: str, adjuntos: list[dict] | None = None) -> str:
    """Respaldo si el SMTP de Gmail no está disponible (Render bloquea el puerto 587 en planes gratuitos).

    Envía como la cuenta del mensaje (el dominio está autenticado en Brevo). Si Brevo no acepta ese
    remitente, reintenta como BREVO_FROM_EMAIL con «responder a» la cuenta original. Devuelve el
    remitente con el que salió.
    """
    if not BREVO_API_KEY:
        raise RuntimeError("Sin BREVO_API_KEY para el respaldo")
    desde = email_de(msg["From"])
    try:
        _llamar_brevo(msg, html, texto, adjuntos, desde)
        return desde
    except RuntimeError as e:
        if not BREVO_FROM_EMAIL or desde == BREVO_FROM_EMAIL.lower() or "sender" not in str(e).lower():
            raise
        logger.warning("Brevo no aceptó %s como remitente; se envía como %s", desde, BREVO_FROM_EMAIL)
        _llamar_brevo(msg, html, texto, adjuntos, BREVO_FROM_EMAIL)
        return BREVO_FROM_EMAIL.lower()


def _llamar_brevo(msg, html: str, texto: str, adjuntos: list[dict] | None, remitente: str) -> None:
    cabeceras = {k: msg[k] for k in ("In-Reply-To", "References") if msg[k]}
    payload = {
        "sender": {"name": BREVO_FROM_NAME, "email": remitente},
        "to": [{"email": email_de(msg["To"])}],
        "replyTo": {"email": email_de(msg["From"])},
        "subject": str(msg["Subject"]),
        "htmlContent": html or f"<pre>{_html.escape(texto)}</pre>",
        "textContent": texto,
    }
    if cabeceras:
        payload["headers"] = cabeceras
    if adjuntos:
        payload["attachment"] = [{"name": a["nombre"], "content": base64.b64encode(a["datos"]).decode()} for a in adjuntos]
    req = urllib.request.Request(
        "https://api.brevo.com/v3/smtp/email", data=json.dumps(payload).encode("utf-8"),
        headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"}, method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=30)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Brevo {exc.code}: {exc.read().decode('utf-8', errors='replace')[:300]}") from exc


def _despachar(gmail: GmailClient, msg, html: str, texto: str, adjuntos: list[dict] | None = None) -> tuple[str, str]:
    """Envía por Gmail; si falla, por Brevo y guarda copia en Enviados. Devuelve (canal, remitente real).

    `adjuntos` ([{nombre, datos}]) ya van dentro de `msg` para Gmail; a Brevo se le pasan aparte.
    """
    try:
        gmail.enviar(msg)
        return "gmail", gmail.address
    except Exception as error_gmail:
        logger.warning("SMTP de Gmail no disponible (%s); se usa Brevo", error_gmail)
        try:
            remitente = _enviar_por_brevo(msg, html, texto, adjuntos)
        except Exception as error_brevo:
            raise RuntimeError(f"No salió por Gmail ({error_gmail}) ni por Brevo ({error_brevo})") from error_brevo
        try:
            gmail.guardar_en_enviados(msg)
        except Exception as e:
            logger.warning("No se pudo guardar la copia en Enviados: %s", e)
        return "brevo", remitente


def enviar_respuesta(correo_id: str, texto: str, usuario: str, automatico: bool = False,
                     gmail: GmailClient | None = None) -> tuple[bool, str]:
    texto = (texto or "").strip()
    if not texto:
        return False, "La respuesta está vacía."
    if _MARCADOR.search(texto):
        return False, "La respuesta todavía tiene marcadores [COMPLETAR …]."
    archivos = agente_db.archivos_de(correo_id, con_datos=True)
    faltan = imagenes_faltantes(texto, archivos)
    if faltan:
        return False, f"Falta cargar la imagen: {', '.join(faltan)}. Insértala de nuevo o borra la marca [imagen: …]."
    doc = agente_db.reclamar_para_envio(correo_id, usuario)
    if not doc:
        return False, "Este correo ya fue atendido por otra persona."
    # Imágenes: solo las que siguen en el texto. Adjuntos: todos los que el equipo cargó.
    usadas = {m.group(1).strip().lower() for m in MARCA_IMAGEN.finditer(texto)}
    imagenes = {a["nombre"].lower(): a["url"] for a in archivos if a["tipo"] == "imagen" and a["nombre"].lower() in usadas}
    adjuntos = [a for a in archivos if a["tipo"] == "adjunto"]
    try:
        # Se lee del buzón que lo recibió y se responde desde la cuenta elegida para el tema.
        recibida = gmail if gmail and gmail.address == cuenta_de(doc) else _gmail(cuenta_de(doc))
        desde = cuenta_respuesta(doc)
        emisor = recibida if desde == recibida.address else _gmail(desde)
        html = _html_respuesta(texto, doc, imagenes)
        plano = _texto_respuesta(texto, doc)
        msg = emisor.construir_respuesta(doc, plano, html, remitente=formataddr((BREVO_FROM_NAME, desde)))
        for a in adjuntos:
            principal, _, secundario = (a["mime"] or "application/octet-stream").partition("/")
            msg.add_attachment(a["datos"], maintype=principal, subtype=secundario or "octet-stream", filename=a["nombre"])
        canal, desde = _despachar(emisor, msg, html, plano, adjuntos)
    except Exception as e:
        agente_db.actualizar(correo_id, {"estado": "pendiente", "error": f"No se envió: {e}"[:600]},
                             accion="envío fallido", usuario=usuario, detalle=str(e))
        return False, f"No se pudo enviar: {e}"

    estado = "enviado_auto" if automatico else "enviado"
    agente_db.actualizar(correo_id, {
        "estado": estado, "respuesta_enviada": texto, "enviado_at": datetime.now(timezone.utc),
        "enviado_por": usuario, "enviado_message_id": msg["Message-ID"], "canal_envio": canal, "enviado_desde": desde,
        "editado_por_equipo": texto != (doc.get("borrador_original") or "").strip(), "error": None,
        "adjuntos_enviados": [{"nombre": a["nombre"], "tipo": a["tipo"], "tamano": a["tamano"], "url": a["url"]}
                              for a in archivos if a["tipo"] == "adjunto" or a["nombre"].lower() in imagenes],
    }, accion="enviado automáticamente" if automatico else "enviado", usuario=usuario, detalle=f"desde {desde} vía {canal}")
    recibida.marcar_leido(doc.get("gm_msgid", ""))
    agente_db.contar_uso_faqs(doc.get("fuentes_usadas") or [])
    sugerira = not automatico and bool(aportes_del_equipo(agente_db.get_correo(correo_id) or {}))
    if sugerira:
        _sugerir_faq_en_segundo_plano(correo_id)
    _cerrar_telegram(agente_db.get_correo(correo_id),
                     "🤖 Respondido por el agente" if automatico else f"✅ Enviado por {usuario}")
    if sugerira:
        return True, "Respuesta enviada. Con lo que aportaste, el agente prepara una FAQ: revísala en «FAQs por aprobar»."
    return True, "Respuesta enviada."


def reenviar(correo_id: str, destino: str, nota: str, usuario: str) -> tuple[bool, str]:
    destino = (destino or ADMIN_FORWARD_EMAIL).strip()
    doc = agente_db.reclamar_para_envio(correo_id, usuario)
    if not doc:
        return False, "Este correo ya fue atendido por otra persona."
    try:
        gmail = _gmail(cuenta_de(doc))
        raw = gmail.original(doc["gm_msgid"]) if doc.get("gm_msgid") else None
        if not raw:
            raise RuntimeError("No se encontró el correo original en Gmail")
        texto = nota or f"Reenviado desde la cola de correos por {usuario}.\n\nResumen del agente: {doc.get('resumen', '')}"
        msg = gmail.construir_reenvio(destino, raw, doc.get("asunto", ""), texto)
        canal, _ = _despachar(gmail, msg, "", texto, adjuntos=[{"nombre": "correo-original.eml", "datos": raw}])
    except Exception as e:
        agente_db.actualizar(correo_id, {"estado": "pendiente", "error": f"No se reenvió: {e}"[:600]},
                             accion="reenvío fallido", usuario=usuario, detalle=str(e))
        return False, f"No se pudo reenviar: {e}"
    agente_db.actualizar(correo_id, {"estado": "reenviado", "reenviado_a": destino, "enviado_at": datetime.now(timezone.utc),
                                     "enviado_por": usuario, "canal_envio": canal, "error": None},
                         accion="reenviado", usuario=usuario, detalle=destino)
    _cerrar_telegram(agente_db.get_correo(correo_id), f"📤 Reenviado a {destino}")
    return True, f"Reenviado a {destino}."


def descartar(correo_id: str, usuario: str, bloquear: bool = False) -> None:
    doc = agente_db.get_correo(correo_id)
    if not doc:
        return
    if bloquear:
        agente_db.bloquear_remitente(doc.get("de_email", ""))
    agente_db.actualizar(correo_id, {"estado": "descartado"},
                         accion="remitente bloqueado" if bloquear else "descartado", usuario=usuario)
    _cerrar_telegram(doc, "🚫 Remitente bloqueado" if bloquear else f"❌ Descartado por {usuario}")


def _texto_turno_equipo(instruccion: str, datos: list[dict]) -> str:
    partes = [f"{d['dato']}: {d['respuesta']}" for d in datos]
    if instruccion:
        partes.append(instruccion)
    return "\n".join(partes)


def reescribir(correo_id: str, instruccion: str, usuario: str,
               datos: list[dict] | None = None) -> tuple[bool, str, str]:
    """Un turno del chat con el agente sobre el borrador. Devuelve (ok, borrador o error, nota del agente).

    `datos`: lo que el equipo completó ([{dato, respuesta}]). Se acumula en `datos_completados` y queda
    en `chat` para los turnos siguientes y para sugerir una FAQ al enviar.
    """
    doc = agente_db.get_correo(correo_id)
    if not doc:
        return False, "No se encontró el correo.", ""
    instruccion = (instruccion or "").strip()
    datos = [{"dato": (d.get("dato") or "").strip()[:300], "respuesta": (d.get("respuesta") or "").strip()[:2000]}
             for d in (datos or []) if (d.get("respuesta") or "").strip()]
    if not instruccion and not datos:
        return False, "Escribe un dato o qué cambiar.", ""
    completados = (doc.get("datos_completados") or []) + datos
    try:
        contexto, _ = agente_contexto.remitente(doc, [])
        if doc.get("hilo_previo"):
            contexto["mensajes_anteriores_del_hilo"] = [
                {"de": m.get("de"), "texto": m.get("texto")} for m in doc["hilo_previo"]]
        respuesta, uso = agente_ia.reescribir(
            doc, contexto, agente_contexto.conocimiento()[0], cuenta_respuesta(doc),
            doc.get("respuesta_borrador") or "", instruccion, completados, (doc.get("chat") or [])[-12:])
    except agente_ia.AgenteIAError as e:
        return False, str(e), ""
    nuevo = (respuesta.get("respuesta") or "").strip()
    nota = (respuesta.get("nota") or "").strip() or "Listo, revisa el borrador."
    ahora = datetime.now(timezone.utc)
    agente_db.correos_col.update_one({"_id": doc["_id"]}, {
        "$inc": {"uso.costo_usd": uso["costo_usd"]},
        "$push": {"chat": {"$each": [
            {"rol": "equipo", "texto": _texto_turno_equipo(instruccion, datos), "usuario": usuario, "fecha": ahora},
            {"rol": "agente", "texto": nota, "fecha": ahora},
        ]}},
    })
    agente_db.actualizar(correo_id, {"respuesta_borrador": nuevo, "datos_faltantes": respuesta.get("datos_faltantes") or [],
                                     "datos_completados": completados},
                         accion="borrador reescrito", usuario=usuario, detalle=_texto_turno_equipo(instruccion, datos))
    return True, nuevo, nota


def aportes_del_equipo(doc: dict) -> list[str]:
    """Lo que el equipo agregó al borrador del agente: de ahí salen las FAQs sugeridas.

    Datos completados en los [COMPLETAR], indicaciones en el chat o una edición a mano. Si el
    equipo envió el borrador tal cual, no hay nada nuevo que aprender.
    """
    aportes = [f"{d['dato']}: {d['respuesta']}" for d in doc.get("datos_completados") or []]
    aportes += [t["texto"] for t in doc.get("chat") or [] if t.get("rol") == "equipo" and t.get("texto")]
    if doc.get("editado_por_equipo") and not aportes:
        aportes.append("El equipo editó a mano el borrador antes de enviarlo (compara con la respuesta enviada).")
    return aportes


def sugerir_faq(correo_id: str) -> bool:
    """Tras enviar una respuesta en la que el equipo aportó datos, propone mejorar las FAQs.

    Queda en `faq_sugerida` (estado «pendiente») para que el equipo la apruebe: nunca se guarda sola.
    """
    doc = agente_db.get_correo(correo_id)
    if not doc or not doc.get("respuesta_enviada"):
        return False
    aportes = aportes_del_equipo(doc)
    if not aportes:
        return False
    faqs = [{"id": str(f["_id"]), "categoria": f.get("category"), "pregunta": f.get("question"),
             "respuesta": (f.get("answer") or "")[:800], "autorizada": bool(f.get("autorizada"))}
            for f in agente_db.db.faqs.find({"active": True}).limit(80)]
    try:
        datos, uso = agente_ia.sugerir_faq(doc.get("cuerpo") or "", doc["respuesta_enviada"], aportes, faqs)
    except agente_ia.AgenteIAError as e:
        logger.warning("No se pudo sugerir una FAQ para %s: %s", correo_id, e)
        return False
    agente_db.correos_col.update_one({"_id": doc["_id"]}, {"$inc": {"uso.costo_usd": uso["costo_usd"]}})
    if not datos.get("sugerir"):
        agente_db.actualizar(correo_id, {"faq_sugerida": {"estado": "no_aplica", "motivo": datos.get("motivo", "")}})
        return False
    existente = None
    if datos.get("faq_id") and re.fullmatch(r"[0-9a-f]{24}", datos["faq_id"]):
        existente = next((f for f in faqs if f["id"] == datos["faq_id"]), None)
    agente_db.actualizar(correo_id, {"faq_sugerida": {
        "estado": "pendiente", "pregunta": datos.get("pregunta", ""), "respuesta": datos.get("respuesta", ""),
        "categoria": datos.get("categoria", "") or (existente or {}).get("categoria", ""),
        "faq_id": existente["id"] if existente else None,
        "faq_pregunta_actual": existente["pregunta"] if existente else "",
        "faq_autorizada": bool(existente and existente["autorizada"]),
        "motivo": datos.get("motivo", ""),
    }}, accion="FAQ sugerida", detalle=datos.get("pregunta", ""))
    return True


def _sugerir_faq_en_segundo_plano(correo_id: str) -> None:
    threading.Thread(target=sugerir_faq, args=(correo_id,), daemon=True).start()


# ── Telegram (avisos y aprobación desde el celular) ────────────────────────────

_TG = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


def _tg(metodo: str, **datos) -> dict:
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return {}
    try:
        for k, v in list(datos.items()):
            if isinstance(v, (dict, list)):
                datos[k] = json.dumps(v)
        return requests.post(f"{_TG}/{metodo}", data=datos, timeout=15).json()
    except Exception as e:
        logger.warning("Telegram %s falló: %s", metodo, e)
        return {}


def secreto_webhook() -> str:
    return hashlib.sha256(f"{TELEGRAM_BOT_TOKEN}:{SECRET_KEY}".encode()).hexdigest()[:48]


def _teclado(doc: dict, linea_estado: str = "") -> dict:
    filas = []
    if linea_estado:
        filas.append([{"text": linea_estado, "callback_data": "noop"}])
    else:
        botones = []
        borrador = doc.get("respuesta_borrador") or ""
        if borrador and not _MARCADOR.search(borrador):
            botones.append({"text": "✅ Enviar", "callback_data": f"ag:enviar:{doc['_id']}"})
        botones.append({"text": "❌ Descartar", "callback_data": f"ag:descartar:{doc['_id']}"})
        filas.append(botones)
    base = url_plataforma()
    if base.startswith("https://"):
        filas.append([{"text": "Abrir en la plataforma", "url": f"{base}/inbox?ver={doc['_id']}"}])
    return {"inline_keyboard": filas}


def avisar_telegram(doc: dict | None) -> None:
    if not doc:
        return
    categoria = catalogos.CATEGORIAS_CORREO.get(doc.get("categoria"), ("Correo",))[0]
    borrador = (doc.get("respuesta_borrador") or "").strip()
    quien = ("Socio" if doc.get("member_id") else "Posible socio (por nombre, sin verificar)"
             if doc.get("posible_member_id") else "No es socio")
    avisos = []
    if doc.get("datos_sensibles"):
        avisos.append("⚠️ Toca información sensible: revísalo antes de aprobar.")
    if doc.get("datos_faltantes"):
        avisos.append("✍️ Falta completar: " + "; ".join(doc["datos_faltantes"])[:300])
    buzones = (f"📥 Llegó a {cuenta_de(doc)} · responde desde {cuenta_respuesta(doc)}\n"
               if len(cuentas()) > 1 else "")
    texto = (
        f"📨 {categoria} · prioridad {doc.get('prioridad', 'media')} · {quien}\n"
        f"{buzones}De: {doc.get('de')}\nAsunto: {doc.get('asunto')}\n\n"
        f"{doc.get('resumen') or ''}\n"
        + ("\n" + "\n".join(avisos) + "\n" if avisos else "")
        + (f"\n💬 Borrador:\n{borrador[:2800]}" if borrador else "")
        + ("\n\n✏️ Responde a este mensaje para pedir cambios al borrador." if borrador else "")
    )
    r = _tg("sendMessage", chat_id=TELEGRAM_CHAT_ID, text=texto[:4000], reply_markup=_teclado(doc),
            disable_web_page_preview="true")
    if r.get("ok"):
        agente_db.actualizar(str(doc["_id"]), {"telegram_msg_id": r["result"]["message_id"]})


def avisar_telegram_enviado_solo(doc: dict | None) -> None:
    """Aviso de supervisión: qué respondió el agente sin revisión y con qué fuentes."""
    if not doc:
        return
    fuentes = ", ".join((doc.get("fuentes_titulos") or {}).values()) or "—"
    texto = (
        f"🤖 Respondido por el agente\nDe: {doc.get('de')}\nAsunto: {doc.get('asunto')}\n"
        f"Fuentes: {fuentes}\n\n{(doc.get('respuesta_enviada') or '')[:3000]}"
    )
    _tg("sendMessage", chat_id=TELEGRAM_CHAT_ID, text=texto[:4000], reply_markup=_teclado(doc, "🤖 Enviado solo"),
        disable_web_page_preview="true")


def _cerrar_telegram(doc: dict | None, linea: str) -> None:
    if doc and doc.get("telegram_msg_id"):
        _tg("editMessageReplyMarkup", chat_id=TELEGRAM_CHAT_ID, message_id=doc["telegram_msg_id"],
            reply_markup=_teclado(doc, linea))


def configurar_webhook() -> tuple[bool, str]:
    base = url_plataforma()
    if not base.startswith("https://"):
        return False, "Telegram exige una URL pública con https (RENDER_EXTERNAL_URL)."
    r = _tg("setWebhook", url=f"{base}/webhook/telegram", secret_token=secreto_webhook(),
            allowed_updates=["message", "callback_query"], drop_pending_updates="true")
    return bool(r.get("ok")), r.get("description", "Sin respuesta de Telegram")


def estado_webhook() -> dict:
    if not TELEGRAM_BOT_TOKEN:
        return {"configurado": False, "detalle": "Sin TELEGRAM_BOT_TOKEN"}
    try:
        info = requests.get(f"{_TG}/getWebhookInfo", timeout=10).json().get("result", {})
    except Exception as e:
        return {"configurado": False, "detalle": str(e)}
    url = info.get("url") or ""
    return {"configurado": bool(url) and url.startswith(url_plataforma()), "url": url,
            "ultimo_error": info.get("last_error_message") or "", "pendientes": info.get("pending_update_count", 0)}


def _descargar_foto_tg(file_id: str) -> bytes | None:
    try:
        ruta = requests.get(f"{_TG}/getFile", params={"file_id": file_id}, timeout=10).json()["result"]["file_path"]
        return requests.get(f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{ruta}", timeout=30).content
    except Exception:
        return None


def _comando_bloqueo(texto: str) -> bool:
    partes = texto.strip().split(None, 1)
    cmd, arg = (partes[0].lower() if partes else ""), (partes[1].strip() if len(partes) > 1 else "")
    if cmd == "/bloquear":
        if arg:
            agente_db.bloquear_remitente(arg)
        _tg("sendMessage", chat_id=TELEGRAM_CHAT_ID,
            text=f"Bloqueado: {arg}" if arg else "Uso: /bloquear correo@dominio.com o @dominio.com")
        return True
    if cmd == "/desbloquear":
        borrado = agente_db._blocked.delete_one({"pattern": arg.lower()}).deleted_count if arg else 0
        _tg("sendMessage", chat_id=TELEGRAM_CHAT_ID, text=f"Desbloqueado: {arg}" if borrado else "No estaba en la lista.")
        return True
    if cmd == "/lista_bloqueados":
        lista = agente_db.patrones_bloqueados()
        _tg("sendMessage", chat_id=TELEGRAM_CHAT_ID, text="\n".join(lista) if lista else "No hay remitentes bloqueados.")
        return True
    return False


def procesar_update_telegram(update: dict) -> None:
    """Webhook de Telegram: botones de la cola, pedidos de cambio y comandos del bot."""
    callback = update.get("callback_query")
    if callback:
        if str(callback.get("message", {}).get("chat", {}).get("id")) != str(TELEGRAM_CHAT_ID):
            return
        datos = callback.get("data", "")
        aviso = ""
        if datos.startswith("ag:"):
            _, accion, correo_id = (datos.split(":", 2) + ["", ""])[:3]
            usuario = f"Telegram ({callback.get('from', {}).get('first_name', 'admin')})"
            doc = agente_db.get_correo(correo_id)
            if accion == "enviar" and doc:
                _, aviso = enviar_respuesta(correo_id, doc.get("respuesta_borrador") or "", usuario)
            elif accion == "descartar" and doc:
                descartar(correo_id, usuario)
                aviso = "Descartado."
        _tg("answerCallbackQuery", callback_query_id=callback["id"], text=aviso[:190])
        return

    msg = update.get("message") or {}
    if str(msg.get("chat", {}).get("id")) != str(TELEGRAM_CHAT_ID):
        return
    texto = msg.get("text") or msg.get("caption") or ""
    from workflows.event_manager import handle_command
    if msg.get("photo"):
        handle_command(texto, photo_data=_descargar_foto_tg(msg["photo"][-1]["file_id"]))
        return
    if _comando_bloqueo(texto) or handle_command(texto):
        return
    respondido = (msg.get("reply_to_message") or {}).get("message_id")
    doc = agente_db.get_correo_por_telegram(respondido) if respondido else None
    if doc and texto:
        ok, resultado, _ = reescribir(str(doc["_id"]), texto, usuario="Telegram")
        if ok:
            _cerrar_telegram(doc, "✏️ Hay una versión nueva abajo")
            avisar_telegram(agente_db.get_correo(str(doc["_id"])))
        else:
            _tg("sendMessage", chat_id=TELEGRAM_CHAT_ID, text=f"No se pudo reescribir: {resultado}")
