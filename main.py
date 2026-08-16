import io
import os
import time
import sys
import threading
from datetime import datetime, timezone, timedelta


def _stdin_watchdog():
    """Muere cuando el launcher cierra el pipe de stdin (ventana cerrada)."""
    try:
        sys.stdin.read()
    except Exception:
        pass
    os._exit(0)

threading.Thread(target=_stdin_watchdog, daemon=True).start()

# Fuerza UTF-8 en la consola de Windows
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

from gmail.client import GmailClient
from workflows.incoming import process_email
from workflows.learning import check_learning_opportunities
from knowledge_base.faq_loader import sync_faqs_to_db
from config.settings import CHECK_INTERVAL_SECONDS, BILLING_SHEET_ID, BILLING_CHECK_INTERVAL_HOURS


def _billing_scheduler(gmail: GmailClient):
    """Hilo independiente: revisa recordatorios y morosos cada BILLING_CHECK_INTERVAL_HOURS horas."""
    if not BILLING_SHEET_ID:
        print("  [COBRO] BILLING_SHEET_ID no configurado, scheduler inactivo.")
        return
    from billing.reminders import check_and_send_reminders, check_and_alert_overdue
    interval = BILLING_CHECK_INTERVAL_HOURS * 3600
    while True:
        try:
            check_and_send_reminders(gmail)
            check_and_alert_overdue(gmail)
        except Exception as e:
            print(f"  [COBRO] Error en scheduler: {e}")
        time.sleep(interval)


def run():
    print("=" * 50)
    print("  IPIDET Email Agent")
    print("=" * 50)

    n = sync_faqs_to_db()
    print(f"  FAQs sincronizados desde YAML: {n}")

    from config.settings import GMAIL_ADDRESS, GMAIL_APP_PASSWORD
    gmail = GmailClient(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)

    # Reintenta hasta obtener el UID actual del inbox
    current_max = 0
    for attempt in range(10):
        current_max = gmail.get_inbox_max_uid()
        if current_max > 0:
            break
        print(f"  Conectando a Gmail... intento {attempt + 1}/10")
        time.sleep(5)

    if current_max == 0:
        print("  ERROR: No se pudo conectar a Gmail. Revisa tu conexión.")
        sys.exit(1)

    # Miramos los últimos 20 UIDs para capturar emails que llegaron
    # en los minutos previos al arranque del agente
    lookback = 20
    last_uid = max(0, current_max - lookback)
    print(f"Agente activo. Ciclo cada {CHECK_INTERVAL_SECONDS}s. UID inicial: {last_uid} (max inbox: {current_max})\n")

    threading.Thread(target=_billing_scheduler, args=(gmail,), daemon=True).start()

    last_check = datetime.now(timezone.utc)

    while True:
        now = datetime.now(timezone.utc)
        print(f"[{now.strftime('%H:%M:%S')}] Revisando correos...")

        try:
            new_emails, new_uid = gmail.get_new_messages(last_uid + 1)
            if new_uid > last_uid:
                last_uid = new_uid
            if new_emails:
                print(f"  {len(new_emails)} correo(s) nuevo(s)")
                for email_data in new_emails:
                    try:
                        process_email(email_data, gmail)
                    except Exception as email_err:
                        print(f"  ERROR procesando email: {email_err}")
            else:
                print("  Sin correos nuevos.")

            check_learning_opportunities(gmail, last_check)

        except Exception as e:
            print(f"  ERROR: {e}")

        last_check = now
        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    run()
