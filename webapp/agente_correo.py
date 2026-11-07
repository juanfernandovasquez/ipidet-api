"""
Agente de correos: corre dentro de la web (scheduler.run_agente_correo_scheduler).

Ciclo (`ciclo()`):
  1. Lee los correos nuevos del INBOX desde el último UID guardado en la base.
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
from datetime import datetime, timedelta, timezone
from email.utils import formataddr

import requests

from config import catalogos
from config.settings import (
    ADMIN_FORWARD_EMAIL, BREVO_API_KEY, BREVO_FROM_EMAIL, BREVO_FROM_NAME,
    GMAIL_ADDRESS, GMAIL_APP_PASSWORD, PORTAL_API_BASE, SECRET_KEY,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
)
from gmail.client import GmailClient, detectar_automatico, email_de, parsear
from webapp import agente_contexto, agente_db, agente_ia

logger = logging.getLogger("agente_correo")

_PRIORIDAD_ORDEN = {"alta": 0, "media": 1, "baja": 2}
_MARCADOR = re.compile(r"\[\s*COMPLETAR", re.I)
_LIMA = timezone(timedelta(hours=-5))


def _gmail() -> GmailClient:
    if not (GMAIL_ADDRESS and GMAIL_APP_PASSWORD):
        raise RuntimeError("Faltan GMAIL_ADDRESS y GMAIL_APP_PASSWORD en el entorno del servidor.")
    return GmailClient(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)


def direcciones_propias() -> set[str]:
    org = catalogos.get_organizacion()
    return {e.lower() for e in (GMAIL_ADDRESS, BREVO_FROM_EMAIL, org.get("email"), ADMIN_FORWARD_EMAIL) if e}


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
        gmail = _gmail()
        estado = agente_db.get_estado()
        max_uid, validez = gmail.max_uid()
        ultimo = estado.get("ultimo_uid")
        if ultimo is None or (estado.get("uidvalidity") and estado.get("uidvalidity") != validez):
            # Primera vez (o Gmail renumeró): se empieza desde ahora, sin procesar el histórico.
            ultimo = max_uid
            agente_db.set_estado(ultimo_uid=max_uid, uidvalidity=validez)

        mensajes, _ = gmail.nuevos(int(ultimo), int(cfg.get("max_por_ciclo") or 15))
        cache: dict = {}
        for msg in mensajes:
            resultado = procesar_mensaje(gmail, msg, cfg, cache)
            if resultado:
                resumen["nuevos"] += 1
                resumen[resultado] = resumen.get(resultado, 0) + 1
            agente_db.set_estado(ultimo_uid=msg["uid"])

        for doc in agente_db.para_reintentar():
            if cache.get("api_no_disponible"):
                break
            resultado = reprocesar(str(doc["_id"]), gmail=gmail, cache=cache)
            resumen[resultado] = resumen.get(resultado, 0) + 1

        _verificar_respondidos_en_gmail(gmail)
        agente_db.liberar_ciclo(ultima_revision=ahora, ultimo_error=cache.get("api_no_disponible"), ultimo_resumen=resumen,
                                pendientes_en_inbox=max(0, max_uid - int(ultimo) - len(mensajes)))
    except Exception as e:
        logger.exception("Agente de correos: ciclo fallido")
        agente_db.liberar_ciclo(ultima_revision=ahora, ultimo_error=str(e)[:500], ultimo_resumen=resumen)
        resumen["error"] = str(e)
    return resumen


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


def _conocimiento(cache: dict) -> str:
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
    try:
        analisis, uso = agente_ia.analizar(doc, contexto, adjuntos, _conocimiento(cache), GMAIL_ADDRESS)
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
    campos = {
        **{k: analisis.get(k) for k in ("categoria", "prioridad", "requiere_respuesta", "accion",
                                         "puede_enviarse_sin_revision", "confianza", "resumen", "motivo",
                                         "datos_faltantes")},
        "prioridad_orden": _PRIORIDAD_ORDEN.get(analisis.get("prioridad"), 1),
        "respuesta_borrador": respuesta,
        "borrador_original": respuesta,
        "evento_id": evento_id,
        "member_id": vinculos.get("member_id"),
        "posible_member_id": vinculos.get("posible_member_id"),
        "prospecto_id": vinculos.get("prospecto_id"),
        "hilo_previo": [{"de": m.get("de"), "fecha": m.get("fecha"), "texto": (m.get("cuerpo") or "")[:1500]} for m in hilo],
        "uso": uso,
        "analizado_at": datetime.now(timezone.utc),
        "error": None,
    }
    motivos = _motivos_para_no_enviar_solo(cfg, {**doc, **campos}) if estado == "pendiente" else []
    campos["envio_auto_bloqueado_por"] = motivos
    agente_db.guardar_analisis(correo_id, campos, estado, detalle=analisis.get("resumen") or "")

    if estado == "pendiente" and cfg.get("modo") == "automatico" and not motivos:
        ok, _ = enviar_respuesta(correo_id, respuesta, usuario="agente", automatico=True, gmail=gmail)
        if ok:
            return "enviados_solos"
    if estado == "pendiente" and cfg.get("telegram_avisos"):
        avisar_telegram(agente_db.get_correo(correo_id))
    return "analizados" if estado == "pendiente" else "sin_respuesta"


def _motivos_para_no_enviar_solo(cfg: dict, doc: dict) -> list[str]:
    """Condiciones del envío automático. Lista vacía = puede salir sin revisión."""
    motivos = []
    if cfg.get("modo") != "automatico":
        motivos.append("El agente está en modo solo borradores")
    if doc.get("categoria") not in (cfg.get("categorias_auto") or []):
        etiqueta = catalogos.CATEGORIAS_CORREO.get(doc.get("categoria"), (doc.get("categoria"),))[0]
        motivos.append(f"La categoría «{etiqueta}» no está habilitada para envío automático")
    if doc.get("accion") != "responder":
        motivos.append("El agente sugiere revisarlo antes")
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
    if agente_db.auto_enviados_recientes(doc.get("de_email", "")) >= int(cfg.get("max_auto_por_remitente_dia") or 2):
        motivos.append("Ya se le respondió automáticamente varias veces hoy")
    return motivos


def reprocesar(correo_id: str, gmail: GmailClient | None = None, cache: dict | None = None) -> str:
    """Vuelve a analizar un correo (tras un error o a pedido del equipo)."""
    doc = agente_db.get_correo(correo_id)
    if not doc:
        return "errores"
    gmail = gmail or _gmail()
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


def _verificar_respondidos_en_gmail(gmail: GmailClient) -> None:
    propias = direcciones_propias()
    for doc in agente_db.pendientes_para_verificar():
        try:
            if gmail.respondido_despues(doc["gm_thrid"], doc["fecha"], propias):
                agente_db.actualizar(str(doc["_id"]), {"estado": "respondido_gmail"},
                                     accion="respondido desde Gmail", detalle="El equipo respondió el hilo fuera de la plataforma")
                _cerrar_telegram(agente_db.get_correo(str(doc["_id"])), "✔ Respondido en Gmail")
        except Exception as e:
            logger.warning("No se pudo verificar el hilo %s: %s", doc.get("gm_thrid"), e)


# ── Acciones sobre un correo ───────────────────────────────────────────────────

def _html_respuesta(texto: str, doc: dict) -> str:
    cuerpo = _html.escape(texto).replace("\n", "<br>\n")
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


def _enviar_por_brevo(msg, html: str, texto: str, adjunto_eml: bytes | None = None) -> None:
    """Respaldo si el SMTP de Gmail no está disponible (Render bloquea el puerto 587 en planes gratuitos)."""
    if not BREVO_API_KEY:
        raise RuntimeError("Sin BREVO_API_KEY para el respaldo")
    cabeceras = {k: msg[k] for k in ("In-Reply-To", "References") if msg[k]}
    payload = {
        "sender": {"name": BREVO_FROM_NAME, "email": BREVO_FROM_EMAIL},
        "to": [{"email": email_de(msg["To"])}],
        "replyTo": {"email": GMAIL_ADDRESS},
        "subject": str(msg["Subject"]),
        "htmlContent": html or f"<pre>{_html.escape(texto)}</pre>",
        "textContent": texto,
    }
    if cabeceras:
        payload["headers"] = cabeceras
    if adjunto_eml:
        payload["attachment"] = [{"name": "correo-original.eml", "content": base64.b64encode(adjunto_eml).decode()}]
    req = urllib.request.Request(
        "https://api.brevo.com/v3/smtp/email", data=json.dumps(payload).encode("utf-8"),
        headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"}, method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=30)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Brevo {exc.code}: {exc.read().decode('utf-8', errors='replace')[:300]}") from exc


def _despachar(gmail: GmailClient, msg, html: str, texto: str, adjunto_eml: bytes | None = None) -> str:
    """Envía por Gmail; si falla, por Brevo y guarda copia en Enviados. Devuelve el canal usado."""
    try:
        gmail.enviar(msg)
        return "gmail"
    except Exception as error_gmail:
        logger.warning("SMTP de Gmail no disponible (%s); se usa Brevo", error_gmail)
        try:
            _enviar_por_brevo(msg, html, texto, adjunto_eml)
        except Exception as error_brevo:
            raise RuntimeError(f"No salió por Gmail ({error_gmail}) ni por Brevo ({error_brevo})") from error_brevo
        try:
            gmail.guardar_en_enviados(msg)
        except Exception as e:
            logger.warning("No se pudo guardar la copia en Enviados: %s", e)
        return "brevo"


def enviar_respuesta(correo_id: str, texto: str, usuario: str, automatico: bool = False,
                     gmail: GmailClient | None = None) -> tuple[bool, str]:
    texto = (texto or "").strip()
    if not texto:
        return False, "La respuesta está vacía."
    if _MARCADOR.search(texto):
        return False, "La respuesta todavía tiene marcadores [COMPLETAR …]."
    doc = agente_db.reclamar_para_envio(correo_id, usuario)
    if not doc:
        return False, "Este correo ya fue atendido por otra persona."
    try:
        gmail = gmail or _gmail()
        html = _html_respuesta(texto, doc)
        plano = _texto_respuesta(texto, doc)
        msg = gmail.construir_respuesta(doc, plano, html, remitente=formataddr((BREVO_FROM_NAME, GMAIL_ADDRESS)))
        canal = _despachar(gmail, msg, html, plano)
    except Exception as e:
        agente_db.actualizar(correo_id, {"estado": "pendiente", "error": f"No se envió: {e}"[:600]},
                             accion="envío fallido", usuario=usuario, detalle=str(e))
        return False, f"No se pudo enviar: {e}"

    estado = "enviado_auto" if automatico else "enviado"
    agente_db.actualizar(correo_id, {
        "estado": estado, "respuesta_enviada": texto, "enviado_at": datetime.now(timezone.utc),
        "enviado_por": usuario, "enviado_message_id": msg["Message-ID"], "canal_envio": canal,
        "editado_por_equipo": texto != (doc.get("borrador_original") or "").strip(), "error": None,
    }, accion="enviado automáticamente" if automatico else "enviado", usuario=usuario, detalle=f"vía {canal}")
    gmail.marcar_leido(doc.get("gm_msgid", ""))
    _cerrar_telegram(agente_db.get_correo(correo_id),
                     "🤖 Respondido por el agente" if automatico else f"✅ Enviado por {usuario}")
    return True, "Respuesta enviada."


def reenviar(correo_id: str, destino: str, nota: str, usuario: str) -> tuple[bool, str]:
    destino = (destino or ADMIN_FORWARD_EMAIL).strip()
    doc = agente_db.reclamar_para_envio(correo_id, usuario)
    if not doc:
        return False, "Este correo ya fue atendido por otra persona."
    try:
        gmail = _gmail()
        raw = gmail.original(doc["gm_msgid"]) if doc.get("gm_msgid") else None
        if not raw:
            raise RuntimeError("No se encontró el correo original en Gmail")
        texto = nota or f"Reenviado desde la cola de correos por {usuario}.\n\nResumen del agente: {doc.get('resumen', '')}"
        msg = gmail.construir_reenvio(destino, raw, doc.get("asunto", ""), texto)
        canal = _despachar(gmail, msg, "", texto, adjunto_eml=raw)
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


def reescribir(correo_id: str, instruccion: str, usuario: str) -> tuple[bool, str]:
    doc = agente_db.get_correo(correo_id)
    if not doc:
        return False, "No se encontró el correo."
    try:
        contexto, _ = agente_contexto.remitente(doc, [])
        if doc.get("hilo_previo"):
            contexto["mensajes_anteriores_del_hilo"] = [
                {"de": m.get("de"), "texto": m.get("texto")} for m in doc["hilo_previo"]]
        datos, uso = agente_ia.reescribir(doc, contexto, agente_contexto.conocimiento(), GMAIL_ADDRESS,
                                          doc.get("respuesta_borrador") or "", instruccion)
    except agente_ia.AgenteIAError as e:
        return False, str(e)
    nuevo = (datos.get("respuesta") or "").strip()
    agente_db.correos_col.update_one({"_id": doc["_id"]}, {"$inc": {"uso.costo_usd": uso["costo_usd"]}})
    agente_db.actualizar(correo_id, {"respuesta_borrador": nuevo, "datos_faltantes": datos.get("datos_faltantes") or []},
                         accion="borrador reescrito", usuario=usuario, detalle=instruccion)
    return True, nuevo


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
    texto = (
        f"📨 {categoria} · prioridad {doc.get('prioridad', 'media')}\n"
        f"De: {doc.get('de')}\nAsunto: {doc.get('asunto')}\n\n"
        f"{doc.get('resumen') or ''}\n"
        + (f"\n💬 Borrador:\n{borrador[:2800]}" if borrador else "")
        + ("\n\n✏️ Responde a este mensaje para pedir cambios al borrador." if borrador else "")
    )
    r = _tg("sendMessage", chat_id=TELEGRAM_CHAT_ID, text=texto[:4000], reply_markup=_teclado(doc),
            disable_web_page_preview="true")
    if r.get("ok"):
        agente_db.actualizar(str(doc["_id"]), {"telegram_msg_id": r["result"]["message_id"]})


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
        ok, resultado = reescribir(str(doc["_id"]), texto, usuario="Telegram")
        if ok:
            _cerrar_telegram(doc, "✏️ Hay una versión nueva abajo")
            avisar_telegram(agente_db.get_correo(str(doc["_id"])))
        else:
            _tg("sendMessage", chat_id=TELEGRAM_CHAT_ID, text=f"No se pudo reescribir: {resultado}")
