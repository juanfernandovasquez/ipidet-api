"""Defensas comunes de todo el servidor (panel, portal, webhooks y web pública)."""
import hmac
import re
import time
from urllib.parse import unquote

from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse

MAX_CUERPO_PUBLICO = 1_000_000          # lo que se acepta sin sesión
MAX_CUERPO_CONSTANCIA = 4_200_000       # /web/api/ordenes/<id>/informar
_RE_CONSTANCIA = re.compile(r"/web/api/ordenes/[0-9a-f]{24}/informar")
_CABECERAS = {
    "Strict-Transport-Security": "max-age=31536000",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def ip_cliente(request) -> str:
    """Render está detrás de Cloudflare, que pisa CF-Connecting-IP. El primer valor de X-Forwarded-For
    lo puede escribir el visitante: no sirve para limitar intentos."""
    return (request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "") or "?")[:64]


def iguales(recibido: str, esperado: str) -> bool:
    """Comparación de secretos en tiempo constante. Sin secreto configurado nunca coincide."""
    return bool(esperado) and hmac.compare_digest(str(recibido or "").encode(), esperado.encode())


def ruta_interna(valor: str) -> str:
    """Destino tras el login: solo una ruta de este servidor (nada de //otro.sitio ni /\\otro.sitio)."""
    v = str(valor or "")
    for x in (v, unquote(v)):
        if not x.startswith("/") or x.startswith("//") or "\\" in x or any(ord(c) < 32 for c in x):
            return "/"
    return v


class Intentos:
    """Límite por clave en memoria (una sola instancia), sin crecer sin fin."""

    def __init__(self, maximo: int, segundos: int):
        self.maximo, self.segundos = maximo, segundos
        self._d: dict[str, tuple[int, float]] = {}

    def permitido(self, clave: str) -> bool:
        n, hasta = self._d.get(clave, (0, 0.0))
        return hasta < time.monotonic() or n < self.maximo

    def sumar(self, clave: str) -> None:
        ahora = time.monotonic()
        if len(self._d) > 20_000:
            self._d = {k: v for k, v in self._d.items() if v[1] >= ahora}
            if len(self._d) > 20_000:
                self._d.clear()
        n, hasta = self._d.get(clave, (0, 0.0))
        self._d[clave] = (1, ahora + self.segundos) if hasta < ahora else (n + 1, hasta)


class Seguridad:
    """Middleware ASGI exterior: cabeceras, tope de cuerpo en rutas públicas y POST solo del mismo origen."""

    def __init__(self, app, es_publica):
        self.app, self.es_publica = app, es_publica

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # scope["path"], nunca request.url.path: esa se puede alterar con la cabecera Host.
        ruta, metodo = scope.get("path", ""), scope.get("method", "GET")
        cab = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}

        async def enviar(msg):
            if msg["type"] == "http.response.start":
                h = MutableHeaders(scope=msg)
                for k, v in _CABECERAS.items():
                    if k not in h:
                        h[k] = v
            await send(msg)

        publica = self.es_publica(ruta)
        if metodo not in ("GET", "HEAD", "OPTIONS"):
            de_otro_sitio = cab.get("sec-fetch-site") in ("cross-site", "same-site")
            if de_otro_sitio and (not publica or ruta == "/login"):
                await JSONResponse({"error": "Solicitud de otro sitio"}, status_code=403)(scope, receive, enviar)
                return
        if not publica or metodo in ("GET", "HEAD", "OPTIONS"):
            await self.app(scope, receive, enviar)
            return

        tope = MAX_CUERPO_CONSTANCIA if _RE_CONSTANCIA.fullmatch(ruta) else MAX_CUERPO_PUBLICO
        largo = cab.get("content-length", "")
        excedido = largo.isdigit() and int(largo) > tope
        trozos: list[bytes] = []
        total = 0
        while not excedido:     # se cuenta lo que llega de verdad: Content-Length puede faltar
            msg = await receive()
            if msg["type"] != "http.request":
                return
            total += len(msg.get("body", b""))
            excedido = total > tope
            trozos.append(msg.get("body", b""))
            if not msg.get("more_body"):
                break
        if excedido:
            await JSONResponse({"error": "Envío demasiado grande"}, status_code=413)(scope, receive, enviar)
            return

        cuerpo, entregado = b"".join(trozos), False

        async def recibir():
            nonlocal entregado
            if entregado:
                return await receive()
            entregado = True
            return {"type": "http.request", "body": cuerpo, "more_body": False}

        await self.app(scope, recibir, enviar)
