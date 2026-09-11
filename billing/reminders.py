from datetime import datetime, timezone
from gmail.client import GmailClient
from billing import db
from telegram_bot import notifications
from config.settings import BILLING_REMINDER_DAYS, BILLING_OVERDUE_ALERT_HOURS


def _reminder_body(nombre: str, nro: int, total: int, monto: str, vencimiento: str, id_cuota: str) -> str:
    return (
        f"Estimado/a {nombre},\n\n"
        f"Le recordamos que tiene una cuota pendiente de pago:\n\n"
        f"  Cuota: {nro}/{total}\n"
        f"  Monto: {monto}\n"
        f"  Fecha de vencimiento: {vencimiento}\n"
        f"  Referencia: {id_cuota}\n\n"
        f"Formas de pago aceptadas:\n"
        f"  - Transferencia bancaria\n"
        f"  - Pago web\n"
        f"  - Efectivo en oficina\n\n"
        f"Para confirmar su pago, responda este correo adjuntando la constancia "
        f"o comprobante de pago.\n\n"
        f"Ante cualquier consulta, estamos a su disposición.\n\n"
        f"Atentamente,\n"
        f"IPIDET"
    )


def _member_nombre(member: dict) -> str:
    return f"{member.get('nombres', '')} {member.get('apellidos', '')}".strip() or "estimado/a"


def _member_email(member: dict) -> str | None:
    for e in member.get("emails", []):
        if e.get("principal") and e.get("estado") == "habilitado":
            return e["email"]
    return next((e["email"] for e in member.get("emails", []) if e.get("estado") == "habilitado"), None)


def check_and_send_reminders(gmail: GmailClient):
    upcoming = db.get_upcoming_cuotas(BILLING_REMINDER_DAYS)
    if not upcoming:
        return

    members_cache = {}
    sent_count = 0

    for item in upcoming:
        pmt = item["payment"]
        cuota = item["cuota"]
        member_id = pmt.get("member_id", "")
        numero = cuota.get("numero", 1)
        id_cuota = f"{pmt['_id']}_{numero}"

        if db.reminder_already_sent(id_cuota):
            continue

        if member_id not in members_cache:
            members_cache[member_id] = db.get_member_by_id(member_id)
        member = members_cache.get(member_id)
        if not member or member.get("estado") != "activo":
            continue

        email = _member_email(member)
        if not email:
            continue

        nombre = _member_nombre(member)
        total_cuotas = len(pmt.get("cuotas", []))
        monto = str(cuota.get("monto", ""))
        vencimiento = cuota.get("fecha_venc", "")

        subject = f"IPIDET - Cuota {numero}/{total_cuotas} pendiente | Ref: {id_cuota}"
        body = _reminder_body(nombre, numero, total_cuotas, monto, vencimiento, id_cuota)

        message_id = gmail.send_email(to=email, subject=subject, body=body)
        if message_id:
            db.save_sent_reminder(id_cuota, member_id, email, message_id)
            print(f"  [COBRO] Recordatorio enviado a {email} — {id_cuota}")
            sent_count += 1

    if sent_count:
        notifications._send(
            f"\U0001f4b0 *Recordatorios de cobro enviados*\n\n"
            f"{sent_count} email(s) de cobranza enviados."
        )


def check_and_alert_overdue(gmail: GmailClient):
    overdue = db.get_overdue_cuotas()
    if not overdue:
        return

    last_alert = db.last_overdue_alert()
    if last_alert:
        hours_since = (datetime.now(timezone.utc) - last_alert).total_seconds() / 3600
        if hours_since < BILLING_OVERDUE_ALERT_HOURS:
            return

    members_cache = {}
    overdue_normalized = []
    socios_map = {}

    for item in overdue:
        pmt = item["payment"]
        cuota = item["cuota"]
        member_id = pmt.get("member_id", "")
        numero = cuota.get("numero", 1)

        if member_id not in members_cache:
            members_cache[member_id] = db.get_member_by_id(member_id)
        member = members_cache.get(member_id) or {}

        socios_map[member_id] = {
            "nombre": _member_nombre(member),
            "email": _member_email(member) or "?",
        }
        overdue_normalized.append({
            "id_socio": member_id,
            "nro_cuota": numero,
            "total_cuotas": len(pmt.get("cuotas", [])),
            "monto": cuota.get("monto", ""),
            "vencimiento": cuota.get("fecha_venc", ""),
        })

    notifications.notify_overdue_members(overdue_normalized, socios_map)
    db.save_overdue_alert_time()
