"""Librerías del navegador servidas desde nuestro propio dominio (/vendor/...).

Si la conexión del equipo no llega a los CDN (jsdelivr, unpkg, cdnjs), la página carga sin Alpine.js y
ningún botón funciona. Por eso el servidor descarga cada archivo una sola vez, desde una lista cerrada
de direcciones con versión fija, lo guarda en memoria y en disco, y lo entrega con caché larga.
"""
import logging
import mimetypes
import re
import tempfile
from pathlib import Path

import requests
from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

logger = logging.getLogger(__name__)
router = APIRouter()

# nombre publicado → direcciones de origen (se prueban en orden). Versiones fijas: no cambian.
_ARCHIVOS = {
    "alpine.js": [
        "https://cdn.jsdelivr.net/npm/alpinejs@3.14.9/dist/cdn.min.js",
        "https://unpkg.com/alpinejs@3.14.9/dist/cdn.min.js",
        "https://cdnjs.cloudflare.com/ajax/libs/alpinejs/3.14.9/cdn.min.js",
    ],
    "tailwind.js": ["https://cdn.tailwindcss.com/3.4.17"],
    "chart.js": [
        "https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js",
        "https://unpkg.com/chart.js@4.4.0/dist/chart.umd.min.js",
    ],
}
# Font Awesome: la hoja de estilos pide sus fuentes con rutas relativas (../webfonts/...).
_FA_BASE = "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/"
_RE_FA = re.compile(r"^fa/(css/all\.min\.css|webfonts/[a-z0-9-]+\.(woff2|ttf))$")

_TIPOS = {".js": "application/javascript", ".css": "text/css", ".woff2": "font/woff2", ".ttf": "font/ttf"}
_cache: dict[str, tuple[bytes, str]] = {}
_DIR = Path(tempfile.gettempdir()) / "ipidet_vendor"


def _origenes(nombre: str) -> list[str]:
    if nombre in _ARCHIVOS:
        return _ARCHIVOS[nombre]
    if _RE_FA.match(nombre):
        return [_FA_BASE + nombre[3:]]
    return []


def _tipo(nombre: str) -> str:
    ext = Path(nombre).suffix or ".js"
    return _TIPOS.get(ext) or mimetypes.guess_type(nombre)[0] or "application/octet-stream"


def _obtener(nombre: str) -> tuple[bytes, str] | None:
    if nombre in _cache:
        return _cache[nombre]
    disco = _DIR / nombre.replace("/", "__")
    if disco.exists():
        _cache[nombre] = (disco.read_bytes(), _tipo(nombre))
        return _cache[nombre]
    for url in _origenes(nombre):
        try:
            r = requests.get(url, timeout=20)
            if r.status_code == 200 and r.content:
                _cache[nombre] = (r.content, _tipo(nombre))
                try:
                    _DIR.mkdir(parents=True, exist_ok=True)
                    disco.write_bytes(r.content)
                except OSError:
                    pass
                return _cache[nombre]
            logger.warning("vendor: %s respondió %s", url, r.status_code)
        except requests.RequestException as e:
            logger.warning("vendor: no se pudo descargar %s: %s", url, e)
    return None


@router.get("/vendor/{nombre:path}")
async def vendor(nombre: str):
    if not _origenes(nombre):
        raise HTTPException(404)
    res = await run_in_threadpool(_obtener, nombre)
    if not res:
        raise HTTPException(502, "No se pudo obtener la librería")
    contenido, tipo = res
    return Response(contenido, media_type=tipo,
                    headers={"Cache-Control": "public, max-age=31536000, immutable"})
