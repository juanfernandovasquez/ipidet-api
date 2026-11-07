"""
Agente de correos en la computadora (opcional).

En producción el agente corre dentro de la web (webapp/scheduler.py → agente_correo.ciclo)
y se configura en /inbox/configuracion. Este script ejecuta el mismo ciclo en local, por
ejemplo para probar cambios; el candado en MongoDB impide que choque con la web.

También mantiene los recordatorios de cuotas de fraccionamiento por Gmail (billing/reminders.py),
que todavía no se movieron a la web.
"""
import io
import os
import sys
import threading
import time


def _stdin_watchdog():
    """Muere cuando el launcher cierra el pipe de stdin (ventana cerrada)."""
    try:
        sys.stdin.read()
    except Exception:
        pass
    os._exit(0)


if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

from config import catalogos
from config.settings import BILLING_CHECK_INTERVAL_HOURS, GMAIL_ADDRESS, GMAIL_APP_PASSWORD


def _billing_scheduler():
    from billing.reminders import check_and_send_reminders, check_and_alert_overdue
    from gmail.client import GmailClient
    gmail = GmailClient(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
    while True:
        try:
            check_and_send_reminders(gmail)
            check_and_alert_overdue(gmail)
        except Exception as e:
            print(f"  [COBRO] Error en recordatorios: {e}")
        time.sleep(BILLING_CHECK_INTERVAL_HOURS * 3600)


def run():
    from webapp import agente_correo
    print("=" * 50)
    print("  IPIDET — agente de correos (local)")
    print("=" * 50)
    threading.Thread(target=_billing_scheduler, daemon=True).start()
    while True:
        cfg = catalogos.get_agente_config()
        resumen = agente_correo.ciclo(forzar="--forzar" in sys.argv)
        print(f"[{time.strftime('%H:%M:%S')}] {resumen}")
        time.sleep(max(1, int(cfg.get("intervalo_min") or 3)) * 60)


if __name__ == "__main__":
    if not sys.stdin.isatty():
        threading.Thread(target=_stdin_watchdog, daemon=True).start()
    run()
