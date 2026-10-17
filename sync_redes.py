"""
sync_redes.py — Sincroniza métricas de redes sociales usando Playwright.

== Setup (ejecutar una vez) ==

    pip install playwright
    playwright install chromium

    Al correr por primera vez ejecuta el modo setup:

        python sync_redes.py --setup

    Se abrirá Brave en modo visible. Inicia sesión en Instagram, LinkedIn,
    YouTube y TikTok manualmente, luego presiona Enter. Las sesiones quedan
    guardadas en chrome_profile_redes/ y se reutilizan en las siguientes
    ejecuciones.

== Uso ==

    python sync_redes.py --setup   # primera vez: inicia sesión
    python sync_redes.py           # sincroniza métricas

== Programación automática (Windows Task Scheduler) ==

    Ejecuta setup_tarea_redes.bat para crear la tarea.
    O en PowerShell como administrador:

    $action  = New-ScheduledTaskAction -Execute "python" -Argument "C:\\ruta\\sync_redes.py" -WorkingDirectory "C:\\ruta"
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Thursday -At "09:00"
    Register-ScheduledTask -TaskName "SyncRedesIPIDET" -Action $action -Trigger $trigger -RunLevel Highest
"""

import asyncio
import re
import sys
from pathlib import Path

# Agrega el directorio raíz al path para importar webapp.db
sys.path.insert(0, str(Path(__file__).parent))

PROFILE_DIR = Path(__file__).parent / "chrome_profile_redes"
PROFILE_DIR.mkdir(exist_ok=True)

# Ruta del ejecutable de Brave en Windows
BRAVE_PATH = Path(r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe")
if not BRAVE_PATH.exists():
    BRAVE_PATH = Path(r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe")

SETUP_MODE = "--setup" in sys.argv


# ── Parseo de números ─────────────────────────────────────────────────────────

def _parse_num(txt: str) -> int | None:
    """Convierte '1.2M', '23.500', '1,234', '1.2K' etc. a int."""
    if not txt:
        return None
    txt = txt.strip()
    upper = txt.upper()
    m = re.match(r"^([\d][,.\d]*)\s*([KMB]?)$", upper)
    if not m:
        digits = re.search(r"\d+", txt)
        return int(digits.group()) if digits else None
    num_str, suffix = m.group(1), m.group(2)
    # Detectar si el punto/coma es separador de miles o decimal
    # Si termina en 3 dígitos después del último separador → miles sep
    num_str = re.sub(r"[,.](?=\d{3}(?!\d))", "", num_str)
    num_str = num_str.replace(",", ".").strip(".")
    try:
        num = float(num_str)
    except ValueError:
        return None
    mult = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get(suffix, 1)
    return int(num * mult)


# ── Scrapers por plataforma ───────────────────────────────────────────────────

async def scrape_instagram(page, cuenta: dict) -> dict | None:
    try:
        await page.goto(cuenta["url"], wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(3_000)

        # Intentar extraer seguidores del meta og:description
        meta = await page.evaluate(
            "() => document.querySelector('meta[name=description]')?.content || "
            "document.querySelector('meta[property=\"og:description\"]')?.content || ''"
        )
        m = re.search(r"([\d,\.]+[KkMmBb]?)\s*(?:Followers|seguidores)", meta, re.I)
        if m:
            seg = _parse_num(m.group(1))
            if seg is not None:
                return {"seguidores": seg}

        # Fallback: buscar en los spans de estadísticas del perfil
        await page.wait_for_selector("ul li", timeout=5_000)
        stats = page.locator("ul li")
        counts = []
        for i in range(min(await stats.count(), 3)):
            txt = await stats.nth(i).inner_text()
            num = _parse_num(txt.split()[0])
            if num is not None:
                counts.append(num)
        if len(counts) >= 2:
            return {"posts_total": counts[0], "seguidores": counts[1],
                    "siguiendo": counts[2] if len(counts) > 2 else None}
    except Exception as e:
        print(f"    Instagram error: {e}")
    return None


async def scrape_tiktok(page, cuenta: dict) -> dict | None:
    try:
        await page.goto(cuenta["url"], wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(3_000)

        result: dict = {}
        for attr, key in [
            ("followers-count", "seguidores"),
            ("following-count", "siguiendo"),
            ("likes-count",     "likes_total"),
        ]:
            el = page.locator(f'[data-e2e="{attr}"]').first
            if await el.count():
                val = _parse_num(await el.inner_text())
                if val is not None:
                    result[key] = val

        # Total de posts (videos)
        posts_el = page.locator('[data-e2e="user-post-item"]')
        if await posts_el.count():
            result["posts_total"] = await posts_el.count()

        return result if result.get("seguidores") is not None else None
    except Exception as e:
        print(f"    TikTok error: {e}")
    return None


async def scrape_youtube(page, cuenta: dict) -> dict | None:
    try:
        await page.goto("https://studio.youtube.com", wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(5_000)

        result: dict = {}

        # Suscriptores en el panel de estudio
        for sel in [
            "#subscriber-count",
            "yt-formatted-string.style-scope.ytcp-channel-info-bar",
            ".ytcp-channel-info-bar__subscriber-count",
            "#ytcp-channel-header #subscriber-count",
        ]:
            el = page.locator(sel).first
            if await el.count():
                txt = await el.inner_text()
                val = _parse_num(txt.split()[0])
                if val is not None:
                    result["seguidores"] = val
                    break

        # Si no encontramos en studio, intentar el canal público
        if "seguidores" not in result:
            await page.goto(cuenta["url"], wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(3_000)
            sub_el = page.locator("yt-formatted-string#subscriber-count").first
            if await sub_el.count():
                txt = await sub_el.inner_text()
                val = _parse_num(txt.split()[0])
                if val is not None:
                    result["seguidores"] = val

        return result if result.get("seguidores") is not None else None
    except Exception as e:
        print(f"    YouTube error: {e}")
    return None


async def scrape_linkedin(page, cuenta: dict) -> dict | None:
    try:
        # Ir a la página admin de followers
        base = cuenta["url"].rstrip("/")
        url  = base + "/admin/analytics/followers/"
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(4_000)

        result: dict = {}
        for sel in [
            ".org-followers-page__headline-number",
            "h2[data-test-id*='follower']",
            ".t-36.t-black.t-bold",
            "[data-test='follower-count']",
        ]:
            el = page.locator(sel).first
            if await el.count():
                val = _parse_num(await el.inner_text())
                if val is not None:
                    result["seguidores"] = val
                    break

        # Fallback: leer del perfil público
        if "seguidores" not in result:
            await page.goto(base, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(3_000)
            el = page.locator(".org-top-card-summary__follower-count").first
            if await el.count():
                txt = await el.inner_text()
                m = re.search(r"([\d,\.]+[KkMm]?)", txt)
                if m:
                    val = _parse_num(m.group(1))
                    if val is not None:
                        result["seguidores"] = val

        return result if result.get("seguidores") is not None else None
    except Exception as e:
        print(f"    LinkedIn error: {e}")
    return None


SCRAPERS = {
    "instagram": scrape_instagram,
    "tiktok":    scrape_tiktok,
    "youtube":   scrape_youtube,
    "linkedin":  scrape_linkedin,
}


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    from playwright.async_api import async_playwright
    import webapp.db as rdb
    from datetime import datetime

    cuentas = rdb.get_redes_cuentas(solo_activas=True)
    if not cuentas:
        print("No hay cuentas configuradas en /redes → Cuentas.")
        return

    print(f"[{datetime.now():%Y-%m-%d %H:%M}] Sincronizando {len(cuentas)} cuenta(s)…")

    async with async_playwright() as p:
        launch_kwargs = {}
        if BRAVE_PATH.exists():
            launch_kwargs["executable_path"] = str(BRAVE_PATH)
        else:
            launch_kwargs["channel"] = "chrome"

        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            args=["--start-maximized"],
            no_viewport=True,
            **launch_kwargs,
        )
        page = await ctx.new_page()

        if SETUP_MODE:
            print("\n=== MODO SETUP ===")
            print("Inicia sesión en Instagram, LinkedIn, YouTube y TikTok en el navegador.")
            print("Cuando hayas terminado, vuelve aquí y presiona Enter para cerrar.\n")
            await page.goto("https://www.linkedin.com/", wait_until="domcontentloaded", timeout=30_000)
            input("Presiona Enter cuando hayas iniciado sesión en todas las plataformas…")
            await ctx.close()
            print("Sesión guardada. Ahora puedes correr: python sync_redes.py")
            return

        for cuenta in cuentas:
            plat    = cuenta.get("plataforma", "")
            nombre  = cuenta.get("nombre", plat)
            scraper = SCRAPERS.get(plat)

            if not scraper:
                print(f"  [{plat}] Sin scraper configurado — saltando")
                continue

            print(f"  Scrapeando {nombre} ({plat})…")
            data = await scraper(page, cuenta)

            if data and data.get("seguidores") is not None:
                rdb.save_red_metrica(
                    cuenta_id  = cuenta["_id"],
                    plataforma = plat,
                    **data,
                )
                print(f"    Seguidores: {data['seguidores']:,}")
                if data.get("posts_total"):
                    print(f"    Posts totales: {data['posts_total']:,}")
            else:
                print(f"    Sin datos — verifica que la sesión esté activa (corre: python sync_redes.py --setup)")
                print(f"    URL: {cuenta.get('url', '(no configurada)')}")

        await ctx.close()

    print(f"\nSincronización completada a las {datetime.now():%H:%M}.")


if __name__ == "__main__":
    asyncio.run(main())
