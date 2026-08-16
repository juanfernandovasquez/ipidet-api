import requests
from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


def _send(text: str, reply_markup: dict = None) -> dict | None:
    import json
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        r = requests.post(f"{_BASE}/sendMessage", data=payload, timeout=10)
        result = r.json()
        if result.get("ok"):
            return result
        # Markdown falló — reintentar sin formato para no perder la notificación
        print(f"  [TG] Markdown error: {result.get('description')} — reintentando sin formato")
        payload.pop("parse_mode")
        # Limpiar símbolos de markdown del texto para que se lea bien en plain text
        plain = text.replace("*", "").replace("_", "").replace("`", "").replace("[", "").replace("]", "")
        payload["text"] = plain
        r2 = requests.post(f"{_BASE}/sendMessage", data=payload, timeout=10)
        result2 = r2.json()
        if not result2.get("ok"):
            print(f"  [TG] Error al enviar incluso sin formato: {result2.get('description')}")
        return result2
    except Exception as e:
        print(f"  [TG] Error de conexión: {e}")
        return None


def resolve_approval_message(message_id: int, status_line: str):
    """Reemplaza los botones ✅/❌ por una línea de estado fija. Impide re-presionar."""
    import json
    try:
        requests.post(f"{_BASE}/editMessageReplyMarkup", data={
            "chat_id": TELEGRAM_CHAT_ID,
            "message_id": message_id,
            "reply_markup": json.dumps({"inline_keyboard": [[
                {"text": status_line, "callback_data": "noop"}
            ]]}),
        }, timeout=10)
    except Exception:
        pass


def answer_callback(callback_query_id: str, text: str = ""):
    try:
        requests.post(f"{_BASE}/answerCallbackQuery", data={
            "callback_query_id": callback_query_id,
            "text": text,
        }, timeout=10)
    except Exception:
        pass


def get_updates() -> list:
    from knowledge_base.db import get_telegram_offset, save_telegram_offset
    last_id = get_telegram_offset()
    params = {"timeout": 0, "allowed_updates": ["callback_query", "message"]}
    if last_id is not None:
        params["offset"] = last_id + 1
    try:
        r = requests.get(f"{_BASE}/getUpdates", params=params, timeout=15)
        data = r.json()
        updates = data.get("result", [])
        if updates:
            save_telegram_offset(updates[-1]["update_id"])
        return updates
    except Exception as e:
        print(f"Telegram polling error: {e}")
        return []


def _send_images(images: list):
    """Envía hasta 3 imágenes al chat antes del mensaje de aprobación."""
    import base64
    for img in images[:3]:
        try:
            img_bytes = base64.b64decode(img["data"])
            requests.post(
                f"{_BASE}/sendPhoto",
                files={"photo": ("adjunto.jpg", img_bytes, img["media_type"])},
                data={"chat_id": TELEGRAM_CHAT_ID},
                timeout=30,
            )
        except Exception as e:
            print(f"Telegram image error: {e}")


def _escape(text: str) -> str:
    """Escapa caracteres especiales de Markdown v1 para Telegram."""
    for ch in ["*", "_", "`", "["]:
        text = text.replace(ch, f"\\{ch}")
    return text


def _chunk(text: str, limit: int = 3800) -> list[str]:
    """Divide texto largo en partes respetando el límite de Telegram (4096)."""
    if len(text) <= limit:
        return [text]
    parts = []
    while text:
        cut = text[:limit].rfind("\n", limit // 2) or limit
        parts.append(text[:cut].strip())
        text = text[cut:].strip()
    return parts


def _send_flyer(flyer_path: str):
    """Envía el flyer guardado en disco como imagen al canal de Telegram."""
    try:
        with open(flyer_path, "rb") as f:
            data = f.read()
        requests.post(
            f"{_BASE}/sendPhoto",
            files={"photo": ("flyer.jpg", data, "image/jpeg")},
            data={"chat_id": TELEGRAM_CHAT_ID, "caption": "📎 Flyer adjunto al evento"},
            timeout=30,
        )
    except Exception as e:
        print(f"Telegram flyer error: {e}")


def notify_pending_approval(email_data: dict, response_text: str, approval_id: str,
                            flyer_path: str = None):
    images = email_data.get("images", [])
    if images:
        _send_images(images)
    if flyer_path:
        _send_flyer(flyer_path)

    from_addr = email_data['from']
    subj = email_data['subject']
    body = email_data.get("body", "").strip()
    img_note = f"📎 {len(images)} imagen(es) arriba\n" if images else ""

    # Mensaje 1: correo recibido completo (sin botones)
    header = f"📨 *Correo recibido*\n{img_note}De: {_escape(from_addr)}\nAsunto: {_escape(subj)}\n\n"
    for chunk in _chunk(body):
        _send(header + _escape(chunk))
        header = ""  # solo en el primero

    # Mensaje 2: respuesta propuesta + botones (este es el que se trackea)
    resp_header = (
        f"💬 *Respuesta propuesta*\n"
        f"Para: {_escape(from_addr)}\n"
        f"Asunto: {_escape(subj)}\n\n"
    )
    resp_chunks = _chunk(response_text)

    # Si la respuesta cabe en un solo mensaje, va con los botones
    if len(resp_chunks) == 1:
        text = resp_header + _escape(resp_chunks[0]) + "\n\n_Reply a este mensaje para pedir cambios_"
        keyboard = {"inline_keyboard": [[
            {"text": "✅ Enviar", "callback_data": f"approve_{approval_id}"},
            {"text": "❌ Cancelar", "callback_data": f"reject_{approval_id}"},
        ]]}
        result = _send(text, reply_markup=keyboard)
    else:
        # Respuesta muy larga: primero los chunks sin botones, luego los botones solos
        for chunk in resp_chunks[:-1]:
            _send(resp_header + _escape(chunk))
            resp_header = ""
        last = resp_header + _escape(resp_chunks[-1]) + "\n\n_Reply a este mensaje para pedir cambios_"
        keyboard = {"inline_keyboard": [[
            {"text": "✅ Enviar", "callback_data": f"approve_{approval_id}"},
            {"text": "❌ Cancelar", "callback_data": f"reject_{approval_id}"},
        ]]}
        result = _send(last, reply_markup=keyboard)

    if result and result.get("ok"):
        return result["result"]["message_id"]
    return None


def notify_regenerated(email_data: dict, response_text: str, approval_id: str) -> int | None:
    """Envía la nueva versión regenerada con los botones de decisión."""
    subj = _escape(email_data['subject'])
    resp_chunks = _chunk(response_text)
    header = f"🔄 *Versión actualizada* — {subj}\n\n"

    if len(resp_chunks) == 1:
        text = header + _escape(resp_chunks[0]) + "\n\n_Reply para otro cambio_"
        keyboard = {"inline_keyboard": [[
            {"text": "✅ Enviar", "callback_data": f"approve_{approval_id}"},
            {"text": "❌ Cancelar", "callback_data": f"reject_{approval_id}"},
        ]]}
        result = _send(text, reply_markup=keyboard)
    else:
        for chunk in resp_chunks[:-1]:
            _send(header + _escape(chunk))
            header = ""
        last = _escape(resp_chunks[-1]) + "\n\n_Reply para otro cambio_"
        keyboard = {"inline_keyboard": [[
            {"text": "✅ Enviar", "callback_data": f"approve_{approval_id}"},
            {"text": "❌ Cancelar", "callback_data": f"reject_{approval_id}"},
        ]]}
        result = _send(last, reply_markup=keyboard)

    if result and result.get("ok"):
        return result["result"]["message_id"]
    return None


def notify_auto_responded(email_data: dict, classification: dict):
    kind = classification.get("type", classification.get("category", "?"))
    conf = int(classification.get("confidence", 0) * 100)
    _send(
        f"✅ *Respondido automaticamente*\n\n"
        f"De: {_escape(email_data['from'][:50])}\n"
        f"Asunto: {_escape(email_data['subject'][:60])}\n"
        f"Tipo: {kind} | Confianza: {conf}%"
    )


def notify_pending_admin_forward(email_data: dict, classification: dict, approval_id: str) -> int | None:
    from_addr = email_data["from"]
    subj = email_data["subject"]
    summary = classification.get("summary", "")
    body = email_data.get("body", "").strip()

    header = (
        f"📋 *Correo administrativo*\n"
        f"De: {_escape(from_addr)}\n"
        f"Asunto: {_escape(subj)}\n"
        f"_{_escape(summary)}_\n\n"
    )
    for chunk in _chunk(body):
        _send(header + _escape(chunk))
        header = ""

    keyboard = {"inline_keyboard": [[
        {"text": "📤 Reenviar a administración", "callback_data": f"admin_fwd_{approval_id}"},
        {"text": "❌ Ignorar", "callback_data": f"admin_ign_{approval_id}"},
    ]]}
    result = _send("¿Qué hacemos con este correo?", reply_markup=keyboard)
    if result and result.get("ok"):
        return result["result"]["message_id"]
    return None


def notify_admin_forwarded(email_data: dict):
    _send(
        f"📤 *Reenviado a administración*\n\n"
        f"De: `{email_data['from'][:40]}`\n"
        f"Asunto: {_escape(email_data['subject'][:50])}"
    )


def notify_low_confidence_clarification(email_data: dict, event: dict, classification: dict):
    conf = int(classification.get("confidence", 0) * 100)
    event_name = event.get("name", "?")
    _send(
        f"❓ *Le pregunté si consulta sobre el evento*\n\n"
        f"De: `{email_data['from'][:40]}`\n"
        f"Asunto: {_escape(email_data['subject'][:50])}\n"
        f"Evento detectado: {_escape(event_name)}\n"
        f"Confianza: {conf}% (bajo el umbral de auto-respuesta)\n\n"
        f"_Esperando confirmacion del remitente._"
    )


def notify_critical(email_data: dict, classification: dict):
    _send(
        f"🔴 *CRÍTICO — Atención inmediata*\n\n"
        f"De: `{email_data['from'][:40]}`\n"
        f"Asunto: {email_data['subject'][:50]}\n\n"
        f"_{classification['summary']}_\n\n"
        f"⚠️ NO respondí nada. Requiere tu atención."
    )


def notify_escalation(email_data: dict, classification: dict):
    _send(
        f"📨 *Correo para revisar*\n\n"
        f"De: `{email_data['from'][:40]}`\n"
        f"Asunto: {email_data['subject'][:50]}\n\n"
        f"_{classification['summary']}_\n"
        f"Razón: {classification['reason']}"
    )


def notify_clarification_sent(email_data: dict):
    _send(
        f"❓ *Pedí aclaración al remitente*\n\n"
        f"De: `{email_data['from'][:40]}`\n"
        f"Asunto: {email_data['subject'][:50]}"
    )


def notify_payment_proof(email_data: dict, approval_id: str, socio: dict | None,
                         pending_cuotas: list, id_cuota: str | None):
    """Notifica al admin que llegó una constancia de pago, con botones por cuota."""
    sender = email_data["from"]
    subj = email_data["subject"]
    images = email_data.get("images", [])

    if images:
        _send_images(images)

    if socio:
        nombre = _escape(socio.get("nombre", "?"))
        id_socio = socio.get("id_socio", "?")
        header = (
            f"\U0001f4b3 *Constancia de pago recibida*\n\n"
            f"Socio: {nombre} (`{id_socio}`)\n"
            f"Email: `{_escape(sender[:50])}`\n"
            f"Asunto: {_escape(subj[:60])}\n"
        )
    else:
        header = (
            f"\U0001f4b3 *Constancia de pago recibida*\n\n"
            f"⚠️ Remitente no encontrado en el padrón\n"
            f"Email: `{_escape(sender[:50])}`\n"
            f"Asunto: {_escape(subj[:60])}\n"
        )

    if images:
        header += f"\U0001f4ce {len(images)} imagen(es) arriba\n"

    _send(header)

    # Botones: uno por cuota pendiente (o genérico si cuota ya identificada)
    keyboard_rows = []
    if id_cuota:
        keyboard_rows.append([{
            "text": f"✅ Confirmar pago ({id_cuota})",
            "callback_data": f"billing_pay_{approval_id}_{id_cuota}",
        }])
    elif pending_cuotas:
        for c in pending_cuotas[:3]:  # máximo 3 botones
            cid = c.get("id_cuota", "?")
            nro = c.get("nro_cuota", "?")
            total = c.get("total_cuotas", "?")
            monto = c.get("monto", "?")
            venc = c.get("vencimiento", "?")
            keyboard_rows.append([{
                "text": f"✅ Cuota {nro}/{total} — ${monto} — vence {venc}",
                "callback_data": f"billing_pay_{approval_id}_{cid}",
            }])
    else:
        keyboard_rows.append([{
            "text": "✅ Marcar pago manualmente en el padrón",
            "callback_data": f"billing_manual_{approval_id}",
        }])

    keyboard_rows.append([{
        "text": "❌ Rechazar constancia",
        "callback_data": f"billing_reject_{approval_id}",
    }])

    result = _send(
        "❓ *¿Qué cuota querés confirmar?*",
        reply_markup={"inline_keyboard": keyboard_rows},
    )
    if result and result.get("ok"):
        return result["result"]["message_id"]
    return None


def notify_overdue_members(overdue: list, socios_map: dict):
    if not overdue:
        return
    lines = ["\U0001f534 *Cuotas vencidas sin pagar*\n"]
    for c in overdue:
        socio = socios_map.get(c.get("id_socio", ""), {})
        nombre = socio.get("nombre", "Desconocido")
        email = socio.get("email", "?")
        lines.append(
            f"• {_escape(nombre)} (`{email}`)\n"
            f"  Cuota {c.get('nro_cuota')}/{c.get('total_cuotas')} — "
            f"${c.get('monto')} — vencía {c.get('vencimiento')}"
        )
    _send("\n".join(lines))


def notify_payment_confirmed(socio: dict, id_cuota: str, medio_pago: str):
    nombre = _escape(socio.get("nombre", "?"))
    email = socio.get("email", "?")
    _send(
        f"✅ *Pago confirmado en el padrón*\n\n"
        f"Socio: {nombre} (`{email}`)\n"
        f"Cuota: `{id_cuota}`\n"
        f"Medio de pago: {_escape(medio_pago)}"
    )


def notify_payment_rejected(email_data: dict):
    _send(
        f"❌ *Constancia rechazada*\n\n"
        f"De: `{email_data['from'][:40]}`\n"
        f"Asunto: {_escape(email_data['subject'][:50])}"
    )


def notify_learning_opportunity(from_addr: str, subject: str):
    _send(
        f"🧠 *Nueva oportunidad de aprendizaje*\n\n"
        f"Respondiste manualmente a: `{from_addr}`\n"
        f"Asunto: {subject}\n\n"
        f"Guardé tu respuesta. Usa el panel de administración "
        f"para convertirla en FAQ automática."
    )


def notify_approval_sent(email_data: dict):
    _send(
        f"✅ *Aprobado y enviado*\n\n"
        f"De: `{email_data['from'][:40]}`\n"
        f"Asunto: {email_data['subject'][:50]}"
    )


def notify_approval_rejected(email_data: dict):
    _send(
        f"❌ *Respuesta cancelada*\n\n"
        f"De: `{email_data['from'][:40]}`\n"
        f"Asunto: {email_data['subject'][:50]}"
    )
