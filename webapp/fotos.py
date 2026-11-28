"""Foto de perfil de cada socio.

- La sube el equipo en la ficha del socio (/members/{id}) o el propio socio en «Mi cuenta» de la web.
- También se puede traer de LinkedIn pegando el enlace de la IMAGEN (media.licdn.com). LinkedIn no deja leer
  perfiles de forma automática (pide sesión, bloquea robots y sus condiciones lo prohíben), y el enlace de la
  imagen caduca: por eso se descarga una vez y se guarda aquí.
- Toda imagen se vuelve a dibujar como JPEG cuadrado de 512 px: se pierden los metadatos (GPS, cámara) y
  cualquier contenido que no sea la imagen.
- Bytes en `members_fotos` {member_id, datos, tipo, origen, por, updated_at}; en `members` solo `foto_at`
  (para refrescar la caché) y `foto_origen`.
- Privada: en el panel se ve con sesión; en la web, el socio ve la suya y el público solo la de quien
  está hoy en el consejo directivo.
"""
import io
import re
import warnings
from datetime import datetime, timezone
from urllib.parse import urlsplit

from bson.binary import Binary
from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from urllib.parse import urlencode

from config.mongo_client import db
from webapp import seguridad

router = APIRouter()
fotos_col = db.members_fotos

MAX_ARCHIVO = 8_000_000          # lo que se acepta al subir (un celular saca fotos de 3 a 6 MB)
_MAX_PIXELES = 40_000_000
_LADO = 512
_HOST_LINKEDIN = re.compile(r"media(-exp\d+)?\.licdn\.com")


def procesar(datos: bytes) -> bytes:
    """JPEG cuadrado de 512 px o ValueError con un mensaje para la persona."""
    from PIL import Image, ImageOps
    if not datos:
        raise ValueError("Elige una foto.")
    if len(datos) > MAX_ARCHIVO:
        raise ValueError("La foto pesa más de 8 MB.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(datos)) as im:
                if im.format not in ("JPEG", "PNG", "WEBP"):
                    raise ValueError("La foto debe ser JPG, PNG o WebP.")
                if im.size[0] * im.size[1] > _MAX_PIXELES:
                    raise ValueError("La foto es demasiado grande.")
                im.load()
                im = ImageOps.exif_transpose(im)
                if im.mode in ("RGBA", "LA", "P"):
                    im = im.convert("RGBA")
                    fondo = Image.new("RGB", im.size, (255, 255, 255))
                    fondo.paste(im, mask=im.getchannel("A"))
                    im = fondo
                im = ImageOps.fit(im.convert("RGB"), (_LADO, _LADO), Image.LANCZOS, centering=(0.5, 0.4))
                salida = io.BytesIO()
                im.save(salida, "JPEG", quality=86, optimize=True)
                return salida.getvalue()
    except ValueError:
        raise
    except Exception:  # noqa: BLE001  (archivo dañado, bomba de descompresión, formato raro)
        raise ValueError("No pudimos leer la foto. Prueba con otra imagen JPG o PNG.")


def desde_enlace(url: str) -> bytes:
    """Descarga una foto de LinkedIn desde el enlace de la imagen. Solo media.licdn.com, sin redirecciones."""
    import httpx
    url = (url or "").strip()
    try:
        p = urlsplit(url)
        host, puerto = (p.hostname or "").lower(), p.port
    except ValueError:
        raise ValueError("Ese enlace no es válido.")
    if host.endswith("linkedin.com"):
        raise ValueError("Ese es el enlace del perfil y LinkedIn no deja descargar la foto desde ahí. Abre el perfil, "
                         "haz clic derecho sobre la foto y elige «Copiar dirección de imagen».")
    if p.scheme != "https" or not _HOST_LINKEDIN.fullmatch(host) or puerto not in (None, 443) or p.username or p.password:
        raise ValueError("Pega el enlace de la imagen de LinkedIn: empieza con https://media.licdn.com/…")
    try:
        with httpx.Client(timeout=12, follow_redirects=False) as cliente:
            with cliente.stream("GET", url, headers={"User-Agent": "Mozilla/5.0 (IPIDET)"}) as r:
                if r.status_code != 200:
                    raise ValueError("LinkedIn no entregó la imagen (el enlace caduca). Cópialo de nuevo del perfil.")
                buf = bytearray()
                for parte in r.iter_bytes():
                    buf += parte
                    if len(buf) > MAX_ARCHIVO:
                        raise ValueError("La imagen es demasiado grande.")
    except httpx.HTTPError:
        raise ValueError("No pudimos conectar con LinkedIn. Inténtalo de nuevo.")
    return procesar(bytes(buf))


def guardar(member_id: str, jpeg: bytes, origen: str, por: str) -> None:
    ahora = datetime.now(timezone.utc)
    fotos_col.update_one({"member_id": member_id},
                         {"$set": {"datos": Binary(jpeg), "tipo": "image/jpeg", "origen": origen, "por": por,
                                   "updated_at": ahora}}, upsert=True)
    db.members.update_one({"member_id": member_id},
                          {"$set": {"foto_at": int(ahora.timestamp()), "foto_origen": origen}})


def quitar(member_id: str) -> None:
    fotos_col.delete_one({"member_id": member_id})
    db.members.update_one({"member_id": member_id}, {"$unset": {"foto_at": "", "foto_origen": ""}})


def leer(member_id: str) -> bytes | None:
    doc = fotos_col.find_one({"member_id": member_id}, {"datos": 1})
    return bytes(doc["datos"]) if doc else None


def respuesta(member_id: str, cache: str = "private, max-age=86400") -> Response:
    datos = leer(member_id)
    if not datos:
        return Response(status_code=404)
    return Response(datos, media_type="image/jpeg",
                    headers={"Cache-Control": cache, "X-Content-Type-Options": "nosniff"})


# ── Panel ─────────────────────────────────────────────────────────────────────

def _volver(destino: str, member_id: str, **q) -> RedirectResponse:
    ruta = seguridad.ruta_interna(destino) if destino else ""
    if ruta in ("", "/"):
        ruta = f"/members/{member_id}"
    ruta = ruta.split("?")[0].split("#")[0]
    return RedirectResponse(ruta + "?" + urlencode(q), status_code=303)


@router.get("/members/{member_id}/foto")
def foto_panel(member_id: str):
    return respuesta(member_id)


@router.post("/members/{member_id}/foto")
def foto_panel_guardar(request: Request, member_id: str, archivo: UploadFile | None = File(None),
                       enlace: str = Form(""), quitar_foto: str = Form(""), volver: str = Form("")):
    if not db.members.find_one({"member_id": member_id}, {"_id": 1}):
        return _volver(volver, member_id, error="No existe ese socio.")
    if quitar_foto:
        quitar(member_id)
        return _volver(volver, member_id, ok="Foto quitada.")
    por = request.session.get("user_email", "")
    try:
        if archivo and archivo.filename:
            guardar(member_id, procesar(archivo.file.read(MAX_ARCHIVO + 1)), "panel", por)
        elif enlace.strip():
            guardar(member_id, desde_enlace(enlace), "linkedin", por)
        else:
            return _volver(volver, member_id, error="Elige una foto o pega el enlace de la imagen de LinkedIn.")
    except ValueError as e:
        return _volver(volver, member_id, error=str(e))
    return _volver(volver, member_id, ok="Foto guardada.")
