"""
Herramientas comunes de las investigaciones de alianzas y ponentes.

La investigación no corre en el servidor ni usa la API: la hace Claude Code en la
computadora del equipo, con la suscripción (comandos /buscar-ponentes y /buscar-alianzas).
Este módulo valida lo que Claude Code entrega antes de guardarlo: cada fuente se descarga
y debe mencionar a la persona o institución. Así se descartan enlaces inventados.
"""
import re
import unicodedata
from urllib.parse import urlsplit

import httpx

LARGO_MAXIMO = 3000
DOMINIOS_NO_OFICIALES = ("linkedin.com", "facebook.com", "instagram.com", "x.com", "twitter.com",
                         "youtube.com", "wikipedia.org", "tiktok.com")
_NAVEGADOR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/126.0 Safari/537.36",
              "Accept-Language": "es-PE,es;q=0.9,en;q=0.8"}


def url_clave(url: str) -> str:
    try:
        p = urlsplit(str(url or "").strip())
    except ValueError:
        return ""
    if p.scheme not in ("http", "https") or not p.hostname:
        return ""
    return f"{p.hostname.lower().removeprefix('www.')}{p.path.rstrip('/')}"


def dominio(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.") if url_clave(url) else ""


def es_red_social(host: str) -> bool:
    return any(host == d or host.endswith("." + d) for d in DOMINIOS_NO_OFICIALES)


def normalizar(texto_: str) -> str:
    sin_acentos = "".join(c for c in unicodedata.normalize("NFD", texto_ or "") if unicodedata.category(c) != "Mn")
    return " " + re.sub(r"[^a-z0-9]+", " ", sin_acentos.lower()).strip() + " "


def texto(valor, largo: int = LARGO_MAXIMO) -> str:
    return re.sub(r"[ \t]+", " ", str(valor or "")).strip()[:largo]


def puntaje(valor) -> int:
    return valor if isinstance(valor, int) and 1 <= valor <= 5 else 1


class Lector:
    """Descarga páginas una sola vez por importación."""

    def __init__(self):
        self._cliente = httpx.Client(headers=_NAVEGADOR, follow_redirects=True, timeout=20.0)
        self._cache: dict[str, tuple[str, str]] = {}

    def leer(self, url: str) -> tuple[str, str]:
        """Devuelve (html, motivo del fallo). El html va vacío si no se pudo leer."""
        clave = url_clave(url)
        if not clave:
            return "", "no es un enlace web"
        if clave not in self._cache:
            try:
                r = self._cliente.get(url)
                if r.status_code >= 400:
                    self._cache[clave] = ("", f"la página respondió {r.status_code}")
                else:
                    self._cache[clave] = (r.text[:3_000_000], "")
            except httpx.HTTPError as exc:
                self._cache[clave] = ("", f"no se pudo abrir ({type(exc).__name__})")
        return self._cache[clave]

    def cerrar(self):
        self._cliente.close()


def fuentes_verificadas(fuentes, lector: Lector, menciona, maximo: int = 10) -> tuple[list, list, str]:
    """Fuentes que se pudieron abrir y mencionan a quien corresponde.

    Devuelve (fuentes válidas, html de esas páginas, motivos de las rechazadas).
    """
    validas, paginas, motivos = [], [], []
    for f in (fuentes or [])[:maximo * 2]:
        url = str(f.get("url") or "").strip()
        html, fallo = lector.leer(url)
        if fallo:
            motivos.append(f"{url}: {fallo}")
            continue
        if not menciona(normalizar(re.sub(r"<[^>]+>", " ", html))):
            motivos.append(f"{url}: la página no lo menciona")
            continue
        validas.append({"titulo": texto(f.get("titulo"), 200) or url, "url": url})
        paginas.append(html)
        if len(validas) == maximo:
            break
    return validas, paginas, "; ".join(motivos)
