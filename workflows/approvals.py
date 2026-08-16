import requests
from datetime import datetime, timezone
from gmail.client import GmailClient
from knowledge_base import db
from telegram_bot import notifications
from classifier.engine import regenerate_response
from workflows.event_manager import handle_command


def _download_photo(file_id: str) -> bytes | None:
    """Descarga una foto enviada al bot de Telegram."""
    token = notifications._BASE.split("bot")[-1]
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getFile",
                         params={"file_id": file_id}, timeout=10)
        file_path = r.json()["result"]["file_path"]
        r2 = requests.get(f"https://api.telegram.org/file/bot{token}/{file_path}", timeout=30)
        return r2.content
    except Exception as e:
        print(f"Error descargando foto: {e}")
        return None


def check_pending_approvals(gmail: GmailClient):
    updates = notifications.get_updates()
    for update in updates:
        msg = update.get("message")
        if msg:
            # Foto enviada al bot (flyer de evento, con o sin caption)
            if msg.get("photo"):
                largest = msg["photo"][-1]
                photo_data = _download_photo(largest["file_id"])
                caption = msg.get("caption", "")
                handle_command(caption, photo_data=photo_data)
                continue  # fotos nunca se procesan como texto

            text = msg.get("text", "")

            # Comandos de gestión de remitentes bloqueados
            if _handle_block_command(text):
                continue

            # Comandos de gestión de eventos
            if handle_command(text):
                continue

            # Reply a un mensaje de aprobación → regenerar con esa instrucción
            if text and msg.get("reply_to_message"):
                _handle_edit_instruction(msg, gmail)
                continue

        callback = update.get("callback_query")
        if not callback:
            continue

        data = callback.get("data", "")
        callback_id = callback["id"]

        if data == "noop":
            notifications.answer_callback(callback_id, "")
            continue

        if data.startswith("approve_"):
            approval_id = data[len("approve_"):]
            msg_id = callback.get("message", {}).get("message_id")
            _handle_approve(approval_id, callback_id, gmail, from_msg_id=msg_id)

        elif data.startswith("reject_"):
            approval_id = data[len("reject_"):]
            _handle_reject(approval_id, callback_id)

        elif data.startswith("admin_fwd_"):
            approval_id = data[len("admin_fwd_"):]
            msg_id = callback.get("message", {}).get("message_id")
            _handle_admin_forward(approval_id, callback_id, gmail, msg_id)

        elif data.startswith("admin_ign_"):
            approval_id = data[len("admin_ign_"):]
            msg_id = callback.get("message", {}).get("message_id")
            _handle_admin_ignore(approval_id, callback_id, msg_id)

        elif data.startswith("billing_pay_"):
            # formato: billing_pay_{approval_id}_{id_cuota}
            rest = data[len("billing_pay_"):]
            parts = rest.split("_", 1)
            approval_id, id_cuota = parts[0], parts[1] if len(parts) > 1 else None
            msg_id = callback.get("message", {}).get("message_id")
            _handle_billing_confirm(approval_id, id_cuota, callback_id, gmail, msg_id)

        elif data.startswith("billing_reject_"):
            approval_id = data[len("billing_reject_"):]
            msg_id = callback.get("message", {}).get("message_id")
            _handle_billing_reject(approval_id, callback_id, msg_id)

        elif data.startswith("billing_manual_"):
            approval_id = data[len("billing_manual_"):]
            msg_id = callback.get("message", {}).get("message_id")
            notifications.answer_callback(callback_id, "Actualizá el padrón manualmente.")
            notifications.resolve_approval_message(msg_id, "📋 Actualización manual pendiente")
            from billing.db import update_proof_status
            update_proof_status(approval_id, "manual")


def _handle_block_command(text: str) -> bool:
    parts = text.strip().split(None, 1)
    cmd = parts[0].lower() if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd == "/bloquear":
        if not arg:
            notifications._send("Uso: /bloquear <email o dominio>\nEj: /bloquear spam@ejemplo.com\nEj: /bloquear @promo.com")
            return True
        db.block_sender(arg)
        notifications._send(f"Bloqueado: `{arg}`\nFuturos correos de este remitente se ignoraran sin analisis.")
        return True

    if cmd == "/desbloquear":
        if not arg:
            notifications._send("Uso: /desbloquear <email o dominio>")
            return True
        removed = db.unblock_sender(arg)
        if removed:
            notifications._send(f"Desbloqueado: `{arg}`")
        else:
            notifications._send(f"No encontrado en la lista: `{arg}`")
        return True

    if cmd == "/lista_bloqueados":
        entries = db.list_blocked_senders()
        if not entries:
            notifications._send("No hay remitentes bloqueados.")
        else:
            lines = ["*Remitentes bloqueados:*\n"] + [f"  - `{e['pattern']}`" for e in entries]
            notifications._send("\n".join(lines))
        return True

    return False


def _handle_edit_instruction(msg: dict, gmail: GmailClient):
    """Reply a un mensaje de aprobación → regenerar con esa instrucción."""
    text = msg.get("text", "")
    reply_to_id = msg.get("reply_to_message", {}).get("message_id")

    # Claim atómico: solo una instancia regenera aunque dos reciban el mismo mensaje
    pending = db.claim_for_regeneration(reply_to_id)
    if not pending:
        return  # Otra instancia ya lo tomó, o no existe, ignorar

    approval_id = pending["approval_id"]
    email_data = pending["email_data"]
    current_response = pending["response_text"]

    print(f"  [✏️] Regenerando #{approval_id} con instrucción: {text[:60]}")
    try:
        new_response = regenerate_response(email_data, current_response, text)
        new_msg_id = notifications.notify_regenerated(email_data, new_response, approval_id)
        db.update_approval_response(approval_id, new_response, new_telegram_msg_id=new_msg_id)
        print(f"  [✏️] Nueva versión lista #{approval_id}")
    except Exception as e:
        # Liberar el claim para que se pueda reintentar
        db.update_approval_response(approval_id, current_response)
        print(f"  [XX] Error regenerando #{approval_id}: {e}")


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M UTC")


def _handle_approve(approval_id: str, callback_id: str, gmail: GmailClient, from_msg_id: int = None):
    # claim_approval es atómico y valida que el botón sea del mensaje actual (no de una versión stale)
    pending = db.claim_approval(approval_id, from_telegram_msg_id=from_msg_id)
    if not pending:
        notifications.answer_callback(callback_id, "Ya procesado.")
        return

    email_data = pending["email_data"]
    response_text = pending["response_text"]
    msg_id = pending.get("telegram_msg_id")

    if not response_text.strip():
        db.update_approval_status(approval_id, "error_empty")
        notifications.answer_callback(callback_id, "Error: respuesta vacía, no se envió.")
        print(f"  [XX] Respuesta vacía en #{approval_id}, cancelado.")
        return

    faq_id = pending.get("faq_id")
    is_clarification = pending.get("is_clarification", False)
    flyer_path = pending.get("flyer_path")

    sent = gmail.send_reply(
        thread_id=email_data["thread_id"],
        to=email_data["from"],
        subject=email_data["subject"],
        body=response_text,
        inline_images=[flyer_path] if flyer_path else None,
    )

    if sent:
        db.update_approval_status(approval_id, "approved")  # primero, para no re-proponer si crashea
        if faq_id:
            db.increment_faq_usage(faq_id)
        try:
            db.save_interaction(email_data, {"type": "faq" if faq_id else "clarification", "approved": True})
            gmail.mark_as_read(email_data["imap_uid"])
        except Exception as e:
            print(f"  [!] Post-send cleanup error: {e}")
        notifications.answer_callback(callback_id, "✅ Enviado.")
        if msg_id:
            notifications.resolve_approval_message(msg_id, f"✅ Enviado — {_now_str()}")
        notifications.notify_approval_sent(email_data)
        action = "aclaracion enviada" if is_clarification else "respondido"
        print(f"  [OK] Aprobado y {action}: {email_data['from'][:40]}")
    else:
        db.update_approval_status(approval_id, "error_smtp")
        notifications.answer_callback(callback_id, "Error al enviar, intenta de nuevo.")
        print(f"  [XX] Fallo SMTP para #{approval_id}: {email_data['from'][:40]}")


def _handle_admin_forward(approval_id: str, callback_id: str, gmail: GmailClient, msg_id: int):
    from config.settings import ADMIN_FORWARD_EMAIL
    pending = db.claim_approval(approval_id, from_telegram_msg_id=msg_id)
    if not pending:
        notifications.answer_callback(callback_id, "Ya procesado.")
        return
    email_data = pending["email_data"]
    sent = gmail.forward_email(ADMIN_FORWARD_EMAIL, email_data)
    if sent:
        db.update_approval_status(approval_id, "admin_forwarded")
        notifications.answer_callback(callback_id, "📤 Reenviado.")
        notifications.resolve_approval_message(msg_id, f"📤 Reenviado — {_now_str()}")
        notifications.notify_admin_forwarded(email_data)
        print(f"  [ADM] Reenviado a {ADMIN_FORWARD_EMAIL}: {email_data['from'][:40]}")
    else:
        db.update_approval_status(approval_id, "pending")
        notifications.answer_callback(callback_id, "Error al reenviar, intenta de nuevo.")


def _handle_admin_ignore(approval_id: str, callback_id: str, msg_id: int):
    pending = db.get_pending_approval(approval_id)
    if not pending:
        notifications.answer_callback(callback_id, "Ya procesado.")
        return
    db.update_approval_status(approval_id, "admin_ignored")
    notifications.answer_callback(callback_id, "Ignorado.")
    notifications.resolve_approval_message(msg_id, f"❌ Ignorado — {_now_str()}")
    print(f"  [ADM] Ignorado: {pending['email_data']['from'][:40]}")


def _handle_reject(approval_id: str, callback_id: str):
    pending = db.get_pending_approval(approval_id)
    if not pending:
        notifications.answer_callback(callback_id, "Ya procesado.")
        return

    msg_id = pending.get("telegram_msg_id")
    db.update_approval_status(approval_id, "rejected")
    notifications.answer_callback(callback_id, "Cancelado.")
    if msg_id:
        notifications.resolve_approval_message(msg_id, f"❌ Cancelado — {_now_str()}")
    notifications.notify_approval_rejected(pending["email_data"])
    print(f"  [--] Respuesta rechazada #{approval_id}")


def _handle_billing_confirm(approval_id: str, id_cuota: str, callback_id: str,
                            gmail: GmailClient, msg_id: int):
    from billing.db import claim_pending_proof, update_proof_status
    from billing import sheets
    from datetime import date

    pending = claim_pending_proof(approval_id)
    if not pending:
        notifications.answer_callback(callback_id, "Ya procesado.")
        return

    id_socio = pending.get("id_socio") or (
        sheets.get_socio_by_email(pending["email_data"].get("from", "")) or {}
    ).get("id_socio")

    fecha_pago = date.today().strftime("%d/%m/%Y")
    ok = sheets.mark_cuota_paid(id_cuota, fecha_pago, "a confirmar")

    if ok:
        update_proof_status(approval_id, "confirmed")
        notifications.answer_callback(callback_id, "✅ Pago registrado en el padrón.")
        notifications.resolve_approval_message(msg_id, f"✅ Pago confirmado — {_now_str()}")

        socio = sheets.get_socios()
        socio_data = next((s for s in socio if s.get("id_socio") == id_socio), {})
        notifications.notify_payment_confirmed(socio_data, id_cuota, "a confirmar")

        # Confirmar al socio por email
        email_data = pending["email_data"]
        nombre = socio_data.get("nombre", "estimado/a")
        confirmation = (
            f"Estimado/a {nombre},\n\n"
            f"Confirmamos la recepción de su comprobante de pago "
            f"(cuota {id_cuota}).\n\n"
            f"Su pago ha sido registrado correctamente. Muchas gracias.\n\n"
            f"Atentamente,\nIPIDET"
        )
        gmail.send_reply(
            thread_id=email_data["thread_id"],
            to=email_data["from"],
            subject=email_data["subject"],
            body=confirmation,
        )
        print(f"  [COBRO] Pago confirmado #{approval_id} — cuota {id_cuota}")
    else:
        update_proof_status(approval_id, "pending")
        notifications.answer_callback(callback_id, f"Error: cuota {id_cuota} no encontrada en el padrón.")
        print(f"  [XX] Cuota {id_cuota} no encontrada en Sheets")


def _handle_billing_reject(approval_id: str, callback_id: str, msg_id: int):
    from billing.db import claim_pending_proof, update_proof_status

    pending = claim_pending_proof(approval_id)
    if not pending:
        notifications.answer_callback(callback_id, "Ya procesado.")
        return

    update_proof_status(approval_id, "rejected")
    notifications.answer_callback(callback_id, "Constancia rechazada.")
    notifications.resolve_approval_message(msg_id, f"❌ Rechazado — {_now_str()}")
    notifications.notify_payment_rejected(pending["email_data"])
    print(f"  [COBRO] Constancia rechazada #{approval_id}")
