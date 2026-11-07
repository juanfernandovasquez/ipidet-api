"""
Cliente IMAP/SMTP de la cuenta de correo que atiende el agente (Gmail / Google Workspace).

Lo usa el agente de correos que corre en la web (webapp/agente_correo.py). Cada mensaje
se identifica por X-GM-MSGID, que Gmail mantiene aunque el correo cambie de carpeta;
el UID de IMAP solo sirve para avanzar en el INBOX.
"""
import email as email_lib
import html as _html
import imaplib
import re
import smtplib
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import getaddresses, make_msgid, parseaddr, parsedate_to_datetime
from html.parser import HTMLParser

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
TIMEOUT = 30

# Adjuntos que se guardan en memoria para que Claude los lea (el resto solo se lista).
_MAX_ADJUNTO_BYTES = 8 * 1024 * 1024
_TIPOS_LEGIBLES = ("image/jpeg", "image/png", "image/gif", "image/webp", "application/pdf")

# Remitentes y cabeceras típicos de correos automáticos: no hay a quién responder.
_LOCAL_AUTOMATICO = re.compile(
    r"^(no-?reply|do-?not-?reply|notificaciones?|notifications?|notify|alerts?|alertas|"
    r"mailer-daemon|postmaster|bounces?|newsletter|news|info-noreply|updates|digest|"
    r"security|account|accounts|support-noreply|calendar-notification)([+.\-_].*)?$",
    re.I,
)
_ASUNTO_AUTORESPUESTA = re.compile(
    r"^(respuesta autom[aá]tica|auto(matic)? ?reply|out of (the )?office|fuera de (la )?oficina|"
    r"undeliverable|no se puede entregar|delivery status notification|mail delivery failed)",
    re.I,
)
_META_FETCH = re.compile(rb"UID (\d+)|X-GM-THRID (\d+)|X-GM-MSGID (\d+)")


def _decodificar(valor) -> str:
    if not valor:
        return ""
    try:
        return str(make_header(decode_header(str(valor)))).strip()
    except Exception:
        return str(valor).strip()


class _HtmlATexto(HTMLParser):
    """Convierte HTML de correo en texto legible: respeta saltos y omite estilos y scripts."""
    _BLOQUES = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "table", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.partes: list[str] = []
        self._omitir = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script", "head", "title"):
            self._omitir += 1
        elif tag in self._BLOQUES:
            self.partes.append("\n")
        elif tag == "a":
            href = dict(attrs).get("href") or ""
            if href.startswith("http"):
                self._href = href

    def handle_endtag(self, tag):
        if tag in ("style", "script", "head", "title"):
            self._omitir = max(0, self._omitir - 1)
        elif tag in self._BLOQUES:
            self.partes.append("\n")

    def handle_data(self, data):
        if not self._omitir:
            self.partes.append(data)


def html_a_texto(contenido: str) -> str:
    parser = _HtmlATexto()
    try:
        parser.feed(contenido)
        texto = "".join(parser.partes)
    except Exception:
        texto = re.sub(r"<[^>]+>", " ", contenido)
    texto = _html.unescape(texto).replace("\xa0", " ")
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r"\n\s*\n\s*(\n\s*)+", "\n\n", texto)
    return texto.strip()


_CORTE_CITA = re.compile(
    r"^(El .{5,120} escribi[oó]:|On .{5,120} wrote:|-{2,} ?(Mensaje original|Original Message|"
    r"Forwarded message|Mensaje reenviado) ?-{2,}|De: .+|From: .+)\s*$",
    re.I | re.M,
)


def sin_citas(texto: str) -> str:
    """Quita lo citado de correos anteriores ("El ... escribió:", líneas con >)."""
    m = _CORTE_CITA.search(texto)
    if m and m.start() > 20:
        texto = texto[: m.start()]
    lineas = [l for l in texto.splitlines() if not l.lstrip().startswith(">")]
    return "\n".join(lineas).strip()


def email_de(direccion: str) -> str:
    return parseaddr(direccion or "")[1].strip().lower()


class GmailClient:
    def __init__(self, address: str, app_password: str):
        self.address = address
        self.app_password = app_password
        self._carpetas: dict[str, str] = {}

    # ── Conexión ───────────────────────────────────────────────────────────────

    @contextmanager
    def _imap(self, carpeta: str = "INBOX", solo_lectura: bool = True):
        conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=TIMEOUT)
        try:
            conn.login(self.address, self.app_password)
            if carpeta:
                estado, _ = conn.select(self._citar(carpeta), readonly=solo_lectura)
                if estado != "OK":
                    raise RuntimeError(f"No se pudo abrir la carpeta {carpeta}")
            yield conn
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    @staticmethod
    def _citar(carpeta: str) -> str:
        return carpeta if carpeta == "INBOX" else f'"{carpeta}"'

    def carpeta(self, atributo: str) -> str:
        """Nombre real de la carpeta especial (\\All, \\Sent): depende del idioma de la cuenta."""
        if atributo not in self._carpetas:
            with self._imap(carpeta="") as conn:
                _, lineas = conn.list()
            for linea in lineas or []:
                texto = linea.decode(errors="ignore")
                m = re.match(r'\((?P<flags>[^)]*)\) "[^"]*" "?(?P<nombre>.+?)"?$', texto)
                if m and atributo.lower() in m.group("flags").lower():
                    self._carpetas[atributo] = m.group("nombre")
                    break
            else:
                self._carpetas[atributo] = {"\\All": "[Gmail]/All Mail", "\\Sent": "[Gmail]/Sent Mail"}.get(atributo, "INBOX")
        return self._carpetas[atributo]

    # ── Lectura ────────────────────────────────────────────────────────────────

    def max_uid(self) -> tuple[int, str]:
        """(UID más alto del INBOX, UIDVALIDITY). Si UIDVALIDITY cambia, los UID se renumeraron."""
        with self._imap() as conn:
            _, validez = conn.response("UIDVALIDITY")
            _, data = conn.uid("search", None, "ALL")
            uids = data[0].split() if data and data[0] else []
        uidvalidity = (validez[0] or b"").decode() if validez else ""
        return (int(uids[-1]) if uids else 0), uidvalidity

    def nuevos(self, desde_uid: int, limite: int = 20) -> tuple[list[dict], int]:
        """Mensajes del INBOX con UID > desde_uid, en orden. Devuelve (mensajes, último UID leído).

        Si un mensaje falla al descargarse, se detiene ahí para reintentarlo en el próximo ciclo.
        """
        with self._imap() as conn:
            _, data = conn.uid("search", None, f"UID {desde_uid + 1}:*")
            uids = sorted(int(u) for u in (data[0].split() if data and data[0] else []) if int(u) > desde_uid)
            mensajes, ultimo = [], desde_uid
            for uid in uids[:limite]:
                msg = self._descargar(conn, str(uid))
                if msg is None:
                    break
                mensajes.append(msg)
                ultimo = uid
        return mensajes, ultimo

    def _descargar(self, conn, uid: str) -> dict | None:
        try:
            _, data = conn.uid("fetch", uid, "(UID X-GM-THRID X-GM-MSGID BODY.PEEK[])")
        except Exception:
            return None
        meta, raw = b"", None
        for parte in data or []:
            if isinstance(parte, tuple):
                meta, raw = parte[0], parte[1]
                break
        if not raw:
            return None
        datos = {}
        for m in _META_FETCH.finditer(meta):
            if m.group(1):
                datos["uid"] = int(m.group(1))
            elif m.group(2):
                datos["gm_thrid"] = m.group(2).decode()
            elif m.group(3):
                datos["gm_msgid"] = m.group(3).decode()
        msg = parsear(raw)
        msg.update(datos)
        return msg

    def _buscar_en_todos(self, conn, criterio: str) -> list[bytes]:
        _, data = conn.uid("search", None, criterio)
        return data[0].split() if data and data[0] else []

    def hilo(self, gm_thrid: str, excluir_msgid: str = "", limite: int = 6) -> list[dict]:
        """Mensajes anteriores del mismo hilo (incluye lo que el equipo respondió desde Gmail)."""
        if not gm_thrid:
            return []
        with self._imap(self.carpeta("\\All")) as conn:
            uids = self._buscar_en_todos(conn, f"X-GM-THRID {gm_thrid}")
            mensajes = []
            for uid in uids[-(limite + 1):]:
                msg = self._descargar(conn, uid.decode())
                if msg and msg.get("gm_msgid") != excluir_msgid:
                    mensajes.append(msg)
        mensajes.sort(key=lambda m: m["fecha"])
        return mensajes[-limite:]

    def original(self, gm_msgid: str) -> bytes | None:
        """Bytes del mensaje tal como llegó (para reenviarlo o ver un adjunto)."""
        with self._imap(self.carpeta("\\All")) as conn:
            uids = self._buscar_en_todos(conn, f"X-GM-MSGID {gm_msgid}")
            if not uids:
                return None
            _, data = conn.uid("fetch", uids[-1], "(BODY.PEEK[])")
        for parte in data or []:
            if isinstance(parte, tuple):
                return parte[1]
        return None

    def respondido_despues(self, gm_thrid: str, desde: datetime, propias: set[str]) -> bool:
        """True si alguna de nuestras direcciones escribió en el hilo después de `desde`."""
        if not gm_thrid:
            return False
        with self._imap(self.carpeta("\\All")) as conn:
            uids = self._buscar_en_todos(conn, f"X-GM-THRID {gm_thrid}")
            for uid in uids[-5:]:
                _, data = conn.uid("fetch", uid, "(BODY.PEEK[HEADER.FIELDS (FROM DATE)])")
                for parte in data or []:
                    if not isinstance(parte, tuple):
                        continue
                    cab = email_lib.message_from_bytes(parte[1])
                    try:
                        fecha = parsedate_to_datetime(cab.get("Date", "")).astimezone(timezone.utc)
                    except Exception:
                        continue
                    if email_de(cab.get("From", "")) in propias and fecha > desde:
                        return True
        return False

    def marcar_leido(self, gm_msgid: str) -> None:
        try:
            with self._imap(self.carpeta("\\All"), solo_lectura=False) as conn:
                for uid in self._buscar_en_todos(conn, f"X-GM-MSGID {gm_msgid}"):
                    conn.uid("store", uid, "+FLAGS", "(\\Seen)")
        except Exception:
            pass

    # ── Envío ──────────────────────────────────────────────────────────────────

    def construir_respuesta(self, original: dict, texto: str, html: str = "",
                            remitente: str = "") -> EmailMessage:
        """Respuesta encadenada al hilo: In-Reply-To y References apuntan al mensaje respondido."""
        msg = EmailMessage()
        msg["From"] = remitente or self.address
        msg["To"] = original.get("reply_to") or original["de"]
        asunto = original.get("asunto") or ""
        msg["Subject"] = asunto if re.match(r"^\s*(re|rv|aw)\s*:", asunto, re.I) else f"Re: {asunto}"
        if original.get("message_id"):
            msg["In-Reply-To"] = original["message_id"]
            msg["References"] = " ".join(filter(None, [original.get("references", ""), original["message_id"]])).strip()
        msg["Message-ID"] = make_msgid(domain=self.address.split("@")[-1] or "ipidet.org")
        msg["Date"] = email_lib.utils.formatdate(localtime=False)
        msg.set_content(texto)
        if html:
            msg.add_alternative(html, subtype="html")
        return msg

    def enviar(self, msg: EmailMessage) -> None:
        """Envía por SMTP de Gmail. Gmail guarda la copia en Enviados por su cuenta."""
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            server.starttls()
            server.login(self.address, self.app_password)
            server.send_message(msg)

    def guardar_en_enviados(self, msg: EmailMessage) -> None:
        """Copia en Enviados un mensaje que salió por otro canal, para que el hilo quede completo."""
        with self._imap(carpeta="") as conn:
            conn.append(self._citar(self.carpeta("\\Sent")), "(\\Seen)",
                        imaplib.Time2Internaldate(time.time()), msg.as_bytes())

    def construir_reenvio(self, destino: str, original_raw: bytes, asunto: str, nota: str = "") -> EmailMessage:
        """Reenvía el correo original completo (con adjuntos) como archivo .eml adjunto."""
        msg = EmailMessage()
        msg["From"] = self.address
        msg["To"] = destino
        msg["Subject"] = f"Fwd: {asunto}"
        msg["Message-ID"] = make_msgid(domain=self.address.split("@")[-1] or "ipidet.org")
        msg.set_content(nota or "Reenviado desde la cola de correos de la plataforma IPIDET.")
        msg.add_attachment(original_raw, maintype="message", subtype="rfc822", filename="correo-original.eml")
        return msg

    def send_email(self, to: str, subject: str, body: str) -> str | None:
        """Correo nuevo en texto plano (lo usan los recordatorios de billing/reminders.py)."""
        msg = EmailMessage()
        msg["From"] = self.address
        msg["To"] = to
        msg["Subject"] = subject
        msg["Message-ID"] = make_msgid(domain=self.address.split("@")[-1] or "ipidet.org")
        msg.set_content(body)
        try:
            self.enviar(msg)
            return msg["Message-ID"]
        except Exception as e:
            print(f"SMTP error (send_email): {e}")
            return None


# ── Parseo ─────────────────────────────────────────────────────────────────────

def _texto_parte(parte) -> str:
    payload = parte.get_payload(decode=True) or b""
    return payload.decode(parte.get_content_charset() or "utf-8", errors="replace")


def detectar_automatico(msg: dict) -> str:
    """Motivo por el que un correo es automático (no espera respuesta), o "" si no lo es."""
    cab = msg.get("cabeceras", {})
    if (cab.get("auto-submitted") or "no").lower() != "no":
        return "Enviado por un sistema automático (Auto-Submitted)"
    if (cab.get("precedence") or "").lower() in ("bulk", "list", "junk"):
        return "Envío masivo (Precedence)"
    if cab.get("list-unsubscribe") or cab.get("list-id"):
        return "Boletín o lista de distribución"
    local = (msg.get("de_email") or "").split("@")[0]
    if _LOCAL_AUTOMATICO.match(local):
        return "Remitente automático (no-reply / notificaciones)"
    if _ASUNTO_AUTORESPUESTA.match(msg.get("asunto") or ""):
        return "Respuesta automática o aviso de entrega"
    return ""


def parsear(raw: bytes) -> dict:
    m = email_lib.message_from_bytes(raw)
    de = _decodificar(m.get("From"))
    nombre, correo = parseaddr(de)
    reply_to = email_de(_decodificar(m.get("Reply-To")))
    try:
        fecha = parsedate_to_datetime(m.get("Date", "")).astimezone(timezone.utc)
    except Exception:
        fecha = datetime.now(timezone.utc)

    texto_plano, texto_html, adjuntos = "", "", []
    for parte in (m.walk() if m.is_multipart() else [m]):
        if parte.is_multipart():
            continue
        tipo = parte.get_content_type()
        disposicion = (parte.get_content_disposition() or "").lower()
        nombre_archivo = _decodificar(parte.get_filename()) if parte.get_filename() else ""
        if disposicion != "attachment" and not nombre_archivo and tipo == "text/plain" and not texto_plano:
            texto_plano = _texto_parte(parte)
        elif disposicion != "attachment" and not nombre_archivo and tipo == "text/html" and not texto_html:
            texto_html = _texto_parte(parte)
        elif tipo.startswith(("image/", "application/", "audio/", "video/", "text/")):
            datos = parte.get_payload(decode=True) or b""
            if not datos:
                continue
            # Imágenes pequeñas incrustadas (firmas, logos) no aportan nada.
            if tipo.startswith("image/") and disposicion == "inline" and len(datos) < 15_000:
                continue
            adjuntos.append({
                "nombre": nombre_archivo or f"adjunto-{len(adjuntos) + 1}",
                "tipo": tipo,
                "tamano": len(datos),
                "datos": datos if tipo in _TIPOS_LEGIBLES and len(datos) <= _MAX_ADJUNTO_BYTES else None,
            })

    cuerpo = texto_plano.replace("\r\n", "\n").strip() or html_a_texto(texto_html)
    cuerpo = re.sub(r"[ \t]+\n", "\n", cuerpo)
    cuerpo = re.sub(r"\n{3,}", "\n\n", cuerpo)
    cabeceras = {k.lower(): _decodificar(m.get(k)) for k in
                 ("Auto-Submitted", "Precedence", "List-Unsubscribe", "List-Id", "X-Autoreply")}
    msg = {
        "message_id": (m.get("Message-ID") or "").strip(),
        "in_reply_to": (m.get("In-Reply-To") or "").strip(),
        "references": " ".join((m.get("References") or "").split()),
        "de": de,
        "de_nombre": _decodificar(nombre) or correo,
        "de_email": correo.strip().lower(),
        "reply_to": reply_to if reply_to and reply_to != correo.strip().lower() else "",
        "para": [e.lower() for _, e in getaddresses([_decodificar(m.get("To"))]) if e],
        "cc": [e.lower() for _, e in getaddresses([_decodificar(m.get("Cc"))]) if e],
        "asunto": _decodificar(m.get("Subject")) or "(sin asunto)",
        "fecha": fecha,
        "cuerpo": cuerpo,
        "adjuntos": adjuntos,
        "cabeceras": {k: v for k, v in cabeceras.items() if v},
    }
    if cabeceras.get("x-autoreply"):
        msg["cabeceras"]["auto-submitted"] = "auto-replied"
    return msg
