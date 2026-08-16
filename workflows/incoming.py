import re
from datetime import datetime, timezone
from gmail.client import GmailClient
from classifier.engine import classify_email, generate_faq_response, generate_clarification_email, generate_event_response
from knowledge_base import db
from telegram_bot import notifications
from config.settings import AUTO_APPROVE_CONFIDENCE, ADMIN_FORWARD_EMAIL

MIN_CONFIDENCE_TO_CLARIFY = 0.5  # por debajo de esto, ignorar aunque el evento tenga auto_approve


def _get_auto_approve_target(kind: str, classification: dict, confidence: float):
    """Retorna el objeto (event/faq) si este email califica para auto-respuesta, o None."""
    if confidence < AUTO_APPROVE_CONFIDENCE:
        return None
    if kind == "event_inquiry":
        event_id = classification.get("event_id")
        event = db.get_event(event_id) if event_id else None
        if event and event.get("auto_approve"):
            return ("event", event)
    if kind == "faq":
        faq_id = classification.get("faq_id")
        faqs = db.get_active_faqs()
        faq = next((f for f in faqs if str(f["_id"]) == faq_id), None)
        if faq and faq.get("auto_approve"):
            return ("faq", faq)
    return None


def _get_low_confidence_auto_event(kind: str, classification: dict, confidence: float):
    """Confianza baja pero el evento tiene auto_approve: pedir confirmación al remitente."""
    if kind != "event_inquiry":
        return None
    if confidence >= AUTO_APPROVE_CONFIDENCE or confidence < MIN_CONFIDENCE_TO_CLARIFY:
        return None
    event_id = classification.get("event_id")
    event = db.get_event(event_id) if event_id else None
    if event and event.get("auto_approve"):
        return event
    return None


def process_email(email_data: dict, gmail: GmailClient):
    email_id = email_data["id"]

    # Filtro 1: remitente bloqueado — sin análisis, sin API call
    if db.is_blocked_sender(email_data.get("from", "")):
        print(f"  [SPAM] Bloqueado: {email_data['from'][:50]}")
        db.mark_processed(email_id, "blocked_sender")
        return

    if db.is_processed(email_id):
        return

    # Filtro 2: respuesta a una repregunta de aclaración — usar evento ya conocido
    pending_thread = db.get_pending_clarification_thread(email_data.get("thread_id", ""))
    if pending_thread:
        _handle_clarification_reply(email_data, pending_thread, gmail)
        return

    # Clasificar
    classification = classify_email(email_data)
    kind = classification["type"]
    confidence = classification.get("confidence", 0)
    conf_pct = int(confidence * 100)
    print(f"  [{kind.upper()} {conf_pct}%] {email_data['from'][:40]} - {email_data['subject'][:50]}")

    # Spam: descartar siempre, sin notificar
    if kind == "spam":
        if db.mark_processed(email_id, "spam"):
            gmail.mark_as_read(email_id)
        return

    # Administrativo: notificar a Telegram para que decidas si reenviar
    if kind == "administrative":
        if not db.mark_processed(email_id, "pending_admin_review"):
            return
        approval_id = db.save_pending_approval(email_data=email_data, response_text="", faq_id=None)
        db.pending_approvals.update_one({"approval_id": approval_id}, {"$set": {"kind": "admin_forward"}})
        msg_id = notifications.notify_pending_admin_forward(email_data, classification, approval_id)
        if msg_id:
            db.pending_approvals.update_one({"approval_id": approval_id}, {"$set": {"telegram_msg_id": msg_id}})
        print(f"  [ADM] Pendiente decision: {email_data['from'][:40]}")
        return

    # ¿Califica para auto-respuesta? (ignora TEST_SENDERS)
    auto_target = _get_auto_approve_target(kind, classification, confidence)
    if auto_target:
        _handle_auto(email_data, classification, gmail, auto_target)
        return

    # Evento con auto_approve pero confianza baja: preguntar al remitente para confirmar
    low_conf_event = _get_low_confidence_auto_event(kind, classification, confidence)
    if low_conf_event:
        _handle_low_confidence_inquiry(email_data, classification, gmail, low_conf_event)
        return

    # Flujo normal: esperar aprobación en Telegram
    if kind == "critical":
        if db.mark_processed(email_id, "critical_escalated"):
            notifications.notify_critical(email_data, classification)
            db.save_interaction(email_data, classification)

    elif kind == "faq":
        active_faqs = db.get_active_faqs()
        faq = next((f for f in active_faqs if str(f["_id"]) == classification.get("faq_id")), None)
        if faq:
            if not db.mark_processed(email_id, "pending_approval"):
                print(f"  [skip] Carrera detectada {email_id[:30]}")
                return
            response_text = generate_faq_response(email_data, faq)
            approval_id = db.save_pending_approval(email_data=email_data, response_text=response_text, faq_id=str(faq["_id"]))
            msg_id = notifications.notify_pending_approval(email_data, response_text, approval_id)
            if msg_id:
                db.pending_approvals.update_one({"approval_id": approval_id}, {"$set": {"telegram_msg_id": msg_id}})
            print(f"  [>>] Pendiente aprobacion #{approval_id}")
        else:
            _escalate(email_data, classification, gmail)

    elif kind == "clarification_needed":
        question = classification.get("clarification_question", "")
        if question:
            if not db.mark_processed(email_id, "pending_approval"):
                return
            clarification_text = generate_clarification_email(email_data, question)
            approval_id = db.save_pending_approval(email_data=email_data, response_text=clarification_text)
            msg_id = notifications.notify_pending_approval(email_data, clarification_text, approval_id)
            if msg_id:
                db.pending_approvals.update_one({"approval_id": approval_id}, {"$set": {"telegram_msg_id": msg_id, "is_clarification": True}})
            print(f"  [??] Aclaracion pendiente #{approval_id}")

    elif kind == "event_inquiry":
        event_id = classification.get("event_id")
        event = db.get_event(event_id) if event_id else None
        if event:
            if not db.mark_processed(email_id, "pending_approval"):
                return
            response_text = generate_event_response(email_data, event)
            approval_id = db.save_pending_approval(email_data=email_data, response_text=response_text)
            db.pending_approvals.update_one({"approval_id": approval_id}, {"$set": {"event_id": event_id, "flyer_path": event.get("flyer_path")}})
            msg_id = notifications.notify_pending_approval(email_data, response_text, approval_id, flyer_path=event.get("flyer_path"))
            if msg_id:
                db.pending_approvals.update_one({"approval_id": approval_id}, {"$set": {"telegram_msg_id": msg_id}})
            print(f"  [🗓] Evento pendiente aprobacion #{approval_id}: {event['name']}")
        else:
            _escalate(email_data, classification, gmail)

    elif kind == "payment_proof":
        _handle_payment_proof(email_data)

    else:
        _escalate(email_data, classification, gmail)


def _handle_auto(email_data: dict, classification: dict, gmail: GmailClient, auto_target: tuple):
    """Envía el email directamente sin pasar por aprobación de Telegram."""
    email_id = email_data["id"]
    kind, target = auto_target

    if not db.mark_processed(email_id, "auto_responded"):
        print(f"  [skip] Carrera detectada en auto-respuesta {email_id[:30]}")
        return

    if kind == "event":
        response_text = generate_event_response(email_data, target)
        flyer_path = target.get("flyer_path")
        sent = gmail.send_reply(
            thread_id=email_data["thread_id"],
            to=email_data["from"],
            subject=email_data["subject"],
            body=response_text,
            inline_images=[flyer_path] if flyer_path else None,
        )
    else:  # faq
        response_text = generate_faq_response(email_data, target)
        sent = gmail.send_reply(
            thread_id=email_data["thread_id"],
            to=email_data["from"],
            subject=email_data["subject"],
            body=response_text,
        )

    if sent:
        try:
            gmail.mark_as_read(email_data["imap_uid"])
        except Exception as e:
            print(f"  [!] mark_as_read error: {e}")
        try:
            db.save_interaction(email_data, classification)
        except Exception as e:
            print(f"  [!] save_interaction error: {e}")
        notifications.notify_auto_responded(email_data, classification)
        print(f"  [AUTO] Respondido automaticamente: {email_data['from'][:40]}")
    else:
        db.processed_emails.update_one({"email_id": email_id}, {"$set": {"action": "auto_respond_failed"}})
        print(f"  [XX] Fallo SMTP en auto-respuesta: {email_data['from'][:40]}")


def _handle_low_confidence_inquiry(email_data: dict, classification: dict, gmail: GmailClient, event: dict):
    """Confianza insuficiente para responder: pregunta al remitente si consulta sobre este evento."""
    email_id = email_data["id"]

    if not db.mark_processed(email_id, "clarification_sent"):
        return

    event_name = event.get("name", "el evento")
    date_str = event["date"].strftime("%d/%m/%Y") if event.get("date") else ""
    date_note = f" del {date_str}" if date_str else ""

    clarification = (
        f"Hola, gracias por escribirnos.\n\n"
        f"¿Estás consultando sobre {event_name}{date_note}? "
        f"Con gusto te respondemos en cuanto confirmes.\n\n"
        f"Saludos,\nIPIDET"
    )

    sent = gmail.send_reply(
        thread_id=email_data["thread_id"],
        to=email_data["from"],
        subject=email_data["subject"],
        body=clarification,
    )

    if sent:
        db.save_pending_clarification_thread(email_data["thread_id"], str(event["_id"]), email_data["from"])
        notifications.notify_low_confidence_clarification(email_data, event, classification)
        print(f"  [?] Repregunta enviada ({int(classification.get('confidence',0)*100)}%): {email_data['from'][:40]}")
    else:
        db.processed_emails.update_one({"email_id": email_id}, {"$set": {"action": "clarification_failed"}})
        print(f"  [XX] Fallo al enviar repregunta: {email_data['from'][:40]}")


def _handle_clarification_reply(email_data: dict, pending_thread: dict, gmail: GmailClient):
    """El remitente confirmó su consulta — responder con la info del evento."""
    email_id = email_data["id"]

    if not db.mark_processed(email_id, "clarification_reply"):
        return

    event = db.get_event(pending_thread["event_id"])
    if not event:
        print(f"  [!] Evento no encontrado para hilo de aclaración: {pending_thread['event_id']}")
        return

    db.delete_pending_clarification_thread(email_data["thread_id"])

    classification = {"type": "event_inquiry", "confidence": 1.0, "event_id": pending_thread["event_id"]}
    response_text = generate_event_response(email_data, event)
    flyer_path = event.get("flyer_path")

    sent = gmail.send_reply(
        thread_id=email_data["thread_id"],
        to=email_data["from"],
        subject=email_data["subject"],
        body=response_text,
        inline_images=[flyer_path] if flyer_path else None,
    )

    if sent:
        try:
            gmail.mark_as_read(email_data["imap_uid"])
            db.save_interaction(email_data, classification)
        except Exception as e:
            print(f"  [!] Post-send cleanup error: {e}")
        notifications.notify_auto_responded(email_data, classification)
        print(f"  [AUTO] Respondido tras confirmacion: {email_data['from'][:40]}")
    else:
        db.processed_emails.update_one({"email_id": email_id}, {"$set": {"action": "clarification_reply_failed"}})
        print(f"  [XX] Fallo SMTP en respuesta de aclaración: {email_data['from'][:40]}")


def _handle_payment_proof(email_data: dict):
    """Constancia de pago recibida: identificar socio/cuota y notificar al admin."""
    from billing import db as billing_db
    from billing import sheets

    email_id = email_data["id"]
    if not db.mark_processed(email_id, "payment_proof_received"):
        return

    sender = email_data.get("from", "")
    match = re.search(r"<(.+?)>", sender)
    clean_email = match.group(1).strip() if match else sender.strip()

    socio = sheets.get_socio_by_email(clean_email)
    id_socio = socio["id_socio"] if socio else None

    # Intentar identificar la cuota por el hilo de conversación
    thread_id = email_data.get("thread_id", "")
    reminder = billing_db.get_reminder_by_thread(thread_id)
    id_cuota = reminder["id_cuota"] if reminder else None

    pending_cuotas = sheets.get_pending_cuotas(id_socio) if id_socio else []

    approval_id = billing_db.save_pending_proof(email_data, id_socio, id_cuota)
    notifications.notify_payment_proof(email_data, approval_id, socio, pending_cuotas, id_cuota)
    print(f"  [COBRO] Constancia recibida de {clean_email[:40]} — #{approval_id}")


def _escalate(email_data: dict, classification: dict, gmail: GmailClient):
    if db.mark_processed(email_data["id"], "escalated"):
        notifications.notify_escalation(email_data, classification)
        db.save_interaction(email_data, classification)
