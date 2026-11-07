"""
Bot de Telegram en la computadora (solo para desarrollo).

En producción Telegram envía cada mensaje y botón a la web (POST /webhook/telegram), que se
conecta desde /inbox/configuracion. Mientras ese webhook esté activo, Telegram no entrega
mensajes por polling, así que este script se detiene con un aviso.
"""
import io
import os
import sys
import threading
import time


def _stdin_watchdog():
    try:
        sys.stdin.read()
    except Exception:
        pass
    os._exit(0)


if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

POLL_INTERVAL = 3
POLL_MAX_BACKOFF = 60


def run():
    from telegram_bot import notifications
    from webapp import agente_correo

    info = agente_correo.estado_webhook()
    if info.get("url"):
        print(f"El bot ya está conectado a la web ({info['url']}). No hace falta este proceso.")
        while True:  # el launcher reinicia procesos que terminan; se queda en espera
            time.sleep(3600)

    print("Bot de Telegram activo en local (polling).")
    backoff = POLL_INTERVAL
    while True:
        try:
            for update in notifications.get_updates():
                agente_correo.procesar_update_telegram(update)
            backoff = POLL_INTERVAL
        except Exception as e:
            backoff = min(backoff * 2, POLL_MAX_BACKOFF)
            print(f"  [TG] Error: {e}; reintento en {backoff}s")
        time.sleep(backoff)


if __name__ == "__main__":
    if not sys.stdin.isatty():
        threading.Thread(target=_stdin_watchdog, daemon=True).start()
    run()
