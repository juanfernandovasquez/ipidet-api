import subprocess
import sys
import io
import time
import threading
import os
from pathlib import Path
from datetime import datetime, timezone

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
os.environ["PYTHONIOENCODING"] = "utf-8"

from rich.console import Console
from rich.text import Text

console = Console(file=sys.stdout, highlight=False)

LOGO = r"""
  ___ ____  ___ ____  ___ _____
 |_ _|  _ \|_ _|  _ \| __|_   _|
  | || |_) || || | | | _|  | |
 |___|____/|___|_| |_|___| |_|
"""

SKIP = ["DeprecationWarning", "utcnow", "scheduled for removal", "Use timezone"]
SEP = "-" * 50


def colorize(line: str, prefix: str = "") -> Text:
    t = Text()
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    t.append(f" {ts} ", style="dim white")
    if prefix:
        t.append(f"{prefix} ", style="dim cyan")
    lo = line.lower()

    if "auto" in lo and ("respond" in lo or "faq" in lo):
        t.append(" [OK] ", style="bold bright_green"); t.append(line.strip(), style="bright_green")
    elif "critical" in lo or "critico" in lo:
        t.append(" [!!] ", style="bold bright_red"); t.append(line.strip(), style="bold bright_red")
    elif "escalat" in lo or "revisar" in lo:
        t.append(" [>>] ", style="bright_yellow"); t.append(line.strip(), style="bright_yellow")
    elif "spam" in lo:
        t.append(" [--] ", style="dim"); t.append(line.strip(), style="dim")
    elif "learn" in lo or "aprendizaje" in lo:
        t.append(" [AI] ", style="bright_cyan"); t.append(line.strip(), style="bright_cyan")
    elif "clarif" in lo:
        t.append(" [??] ", style="magenta"); t.append(line.strip(), style="magenta")
    elif "error" in lo:
        t.append(" [XX] ", style="bold bright_red"); t.append(line.strip(), style="bright_red")
    elif "revisando" in lo:
        t.append(" [..] ", style="bright_blue"); t.append(line.strip(), style="blue")
    elif "sin correos" in lo:
        t.append(" [zz] ", style="dim"); t.append(line.strip(), style="dim")
    elif "nuevo" in lo and "correo" in lo:
        t.append(" [>>] ", style="bright_green"); t.append(line.strip(), style="white")
    elif "aprobado" in lo or "enviado" in lo:
        t.append(" [OK] ", style="bold bright_green"); t.append(line.strip(), style="bright_green")
    elif "rechazado" in lo or "cancelado" in lo:
        t.append(" [--] ", style="dim"); t.append(line.strip(), style="dim")
    else:
        t.append("       "); t.append(line.strip(), style="white")
    return t


def get_max_mtime() -> float:
    files = [f for f in Path(".").rglob("*.py") if f.name != "launcher.py"]
    return max((f.stat().st_mtime for f in files), default=0.0)


def start_proc(script: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-u", script],
        stdin=subprocess.PIPE,   # el hijo muere cuando cerramos este pipe
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )


def stream_proc(proc: subprocess.Popen, prefix: str):
    for line in proc.stdout:
        line = line.rstrip()
        if not line or any(s in line for s in SKIP):
            continue
        console.print(colorize(line, prefix))


def kill_proc(proc: subprocess.Popen):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def print_header():
    console.clear()
    for line in LOGO.strip().splitlines():
        console.print(line, style="bold bright_cyan")
    console.print()
    console.print("  Email Intelligence Agent  v1.0", style="dim white")
    console.print("  administracion@ipidet.org", style="dim cyan")
    console.print()
    console.print(SEP, style="cyan")
    console.print()


def reload_banner():
    console.print()
    console.print("  [ CODIGO ACTUALIZADO — reiniciando ]", style="bold bright_yellow")
    bar = ["[     ]", "[=    ]", "[==   ]", "[===  ]", "[==== ]", "[=====]"]
    for b in bar:
        console.print(f"  {b}", style="bright_yellow", end="\r")
        time.sleep(0.12)
    console.print()
    console.print()


def run():
    print_header()
    last_mtime = get_max_mtime()

    while True:
        console.print("  Iniciando agente de correos...", style="bright_cyan")
        console.print("  Iniciando bot de Telegram...", style="bright_cyan")
        console.print()

        email_proc = start_proc("main.py")
        tg_proc = start_proc("telegram_main.py")
        _active_procs.clear()
        _active_procs.extend([email_proc, tg_proc])

        threading.Thread(target=stream_proc, args=(email_proc, "[EMAIL]"), daemon=True).start()
        threading.Thread(target=stream_proc, args=(tg_proc,   "[TG]"),    daemon=True).start()

        reloaded = False
        while email_proc.poll() is None and tg_proc.poll() is None:
            time.sleep(2)
            mtime = get_max_mtime()
            if mtime > last_mtime:
                last_mtime = mtime
                kill_proc(email_proc)
                kill_proc(tg_proc)
                reload_banner()
                print_header()
                reloaded = True
                break

        if not reloaded:
            # Uno o ambos procesos terminaron inesperadamente
            kill_proc(email_proc)
            kill_proc(tg_proc)
            console.print()
            console.print("  Un proceso termino. Reiniciando ambos en 5s...", style="red")
            time.sleep(5)
            print_header()


_active_procs: list = []

if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        for p in _active_procs:
            kill_proc(p)
        console.print()
        console.print(SEP, style="dim")
        console.print("  Agente detenido.", style="dim")
        console.print()
