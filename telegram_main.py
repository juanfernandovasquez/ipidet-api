import io
import os
import sys
import time
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

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

from gmail.client import GmailClient
from workflows.approvals import check_pending_approvals
from config.settings import GMAIL_ADDRESS, GMAIL_APP_PASSWORD

POLL_INTERVAL = 3      # segundos en condiciones normales
POLL_MAX_BACKOFF = 60  # máximo backoff ante errores de red


def run():
    print("=" * 50)
    print("  IPIDET Telegram Bot")
    print("=" * 50)

    from knowledge_base.db import pending_approvals
    gmail = GmailClient(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=2)
    stuck = pending_approvals.update_many(
        {"status": "sending", "sending_started_at": {"$lt": cutoff}},
        {"$set": {"status": "pending"}},
    )
    stuck2 = pending_approvals.update_many(
        {"status": "regenerating", "sending_started_at": {"$lt": cutoff}},
        {"$set": {"status": "pending"}},
    )
    total = stuck.modified_count + stuck2.modified_count
    if total:
        print(f"  Reseteo {total} approval(s) bloqueadas (> 2 min).")

    print(f"Bot activo. Polling cada {POLL_INTERVAL}s.\n")

    backoff = POLL_INTERVAL
    while True:
        try:
            check_pending_approvals(gmail)
            backoff = POLL_INTERVAL  # éxito → resetear backoff
        except Exception as e:
            error_str = str(e)
            if "timeout" in error_str.lower() or "connection" in error_str.lower():
                backoff = min(backoff * 2, POLL_MAX_BACKOFF)
                print(f"  [TG] Sin conexión, reintentando en {backoff}s...")
            else:
                backoff = POLL_INTERVAL
                print(f"  [TG] ERROR: {e}")
        time.sleep(backoff)


if __name__ == "__main__":
    run()
