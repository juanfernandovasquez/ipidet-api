from datetime import datetime, timezone
from gmail.client import GmailClient
from billing import sheets, db
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


def check_and_send_reminders(gmail: GmailClient):
    upcoming = sheets.get_upcoming_due_cuotas(BILLING_REMINDER_DAYS)
    if not upcoming:
        return

    socios_map = {s["id_socio"]: s for s in sheets.get_socios()}
    sent_count = 0

    for cuota in upcoming:
        id_cuota = cuota.get("id_cuota", "")
        id_socio = cuota.get("id_socio", "")

        if db.reminder_already_sent(id_cuota):
            continue

        socio = socios_map.get(id_socio)
        if not socio or not socio.get("email"):
            continue
        if socio.get("estado", "").lower() != "activo":
            continue

        nombre = socio["nombre"]
        email = socio["email"]
        nro = cuota.get("nro_cuota", 1)
        total = cuota.get("total_cuotas", 1)
        monto = cuota.get("monto", "")
        vencimiento = cuota.get("vencimiento", "")

        subject = f"IPIDET - Cuota {nro}/{total} pendiente | Ref: {id_cuota}"
        body = _reminder_body(nombre, nro, total, str(monto), vencimiento, id_cuota)

        message_id = gmail.send_email(to=email, subject=subject, body=body)
        if message_id:
            db.save_sent_reminder(id_cuota, id_socio, email, message_id)
            print(f"  [COBRO] Recordatorio enviado a {email} — {id_cuota}")
            sent_count += 1

    if sent_count:
        notifications._send(
            f"\U0001f4b0 *Recordatorios de cobro enviados*\n\n"
            f"{sent_count} email(s) de cobranza enviados."
        )


def check_and_alert_overdue(gmail: GmailClient):
    overdue = sheets.get_overdue_cuotas()
    if not overdue:
        return

    for cuota in overdue:
        sheets.mark_cuota_overdue(cuota["id_cuota"])

    last_alert = db.last_overdue_alert()
    if last_alert:
        hours_since = (datetime.now(timezone.utc) - last_alert).total_seconds() / 3600
        if hours_since < BILLING_OVERDUE_ALERT_HOURS:
            return

    socios_map = {s["id_socio"]: s for s in sheets.get_socios()}
    notifications.notify_overdue_members(overdue, socios_map)
    db.save_overdue_alert_time()
