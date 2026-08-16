import imaplib
import smtplib
import email as email_lib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.image import MIMEImage
from email import encoders
from email.header import decode_header
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone, timedelta


IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


class GmailClient:
    def __init__(self, address: str, app_password: str):
        self.address = address
        self.app_password = app_password

    def _imap(self) -> imaplib.IMAP4_SSL:
        conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        conn.login(self.address, self.app_password)
        return conn

    def get_inbox_max_uid(self) -> int:
        """Devuelve el UID más alto actual del INBOX (leído o no)."""
        try:
            conn = self._imap()
            conn.select("INBOX")
            _, data = conn.uid("search", None, "ALL")
            uids = data[0].split()
            conn.logout()
            return int(uids[-1]) if uids else 0
        except Exception as e:
            print(f"IMAP error (max uid): {e}")
            return 0

    def get_new_messages(self, min_uid: int) -> tuple[list, int]:
        """
        Devuelve (mensajes_nuevos, nuevo_max_uid).
        Busca UNSEEN con UID >= min_uid usando UID SEARCH.
        No usa SINCE — evita problemas de zona horaria con INTERNALDATE.
        """
        try:
            conn = self._imap()
            conn.select("INBOX")
            search_range = f"{min_uid}:*"
            # Sin filtro UNSEEN — el usuario puede haber leído el correo antes del ciclo.
            # La DB already_processed evita duplicados.
            _, data = conn.uid("search", None, f"UID {search_range}")
            uids = [int(u) for u in data[0].split() if int(u) >= min_uid]

            messages = []
            max_uid = min_uid - 1  # No avanza si no hay emails nuevos
            for uid in uids:
                msg = self._fetch(conn, str(uid).encode())
                if msg:
                    messages.append(msg)
                    max_uid = uid  # Solo avanza si el fetch fue exitoso
                else:
                    # Fetch fallido (SSL drop, timeout): no avanzar past este UID.
                    # El siguiente ciclo lo reintentará con conexión fresca.
                    print(f"  [!] Fetch fallido UID {uid}, se reintentará.")
                    break

            conn.logout()
            return messages, max_uid
        except Exception as e:
            print(f"IMAP error (inbox): {e}")
            return [], min_uid

    def get_sent_messages(self, since: datetime) -> list:
        try:
            conn = self._imap()
            folders = ["[Gmail]/Enviados", "[Gmail]/Sent Mail", "Sent"]
            selected = False
            for folder in folders:
                try:
                    conn.select(folder)
                    selected = True
                    break
                except Exception:
                    continue
            if not selected:
                conn.logout()
                return []

            search_since = since - timedelta(days=1)
            date_str = search_since.strftime("%d-%b-%Y")
            _, data = conn.search(None, f"SINCE {date_str}")
            ids = data[0].split()
            messages = [self._fetch(conn, uid) for uid in ids]
            conn.logout()
            return [m for m in messages if m]
        except Exception as e:
            print(f"IMAP error (sent): {e}")
            return []

    def _fetch(self, conn: imaplib.IMAP4_SSL, uid: bytes) -> dict | None:
        try:
            _, data = conn.uid("fetch", uid, "(BODY.PEEK[])")
            if not data or not data[0]:
                return None
            raw = data[0][1]
            msg = email_lib.message_from_bytes(raw)

            subject = self._decode_header(msg.get("Subject", "(sin asunto)"))
            from_addr = self._decode_header(msg.get("From", ""))
            to_addr = self._decode_header(msg.get("To", ""))
            date = msg.get("Date", "")
            message_id = msg.get("Message-ID", uid.decode())
            thread_id = msg.get("References", message_id).split()[-1] if msg.get("References") else message_id

            body, images = self._extract_body(msg)

            try:
                received_at = parsedate_to_datetime(date).astimezone(timezone.utc)
            except Exception:
                received_at = datetime.now(timezone.utc)

            return {
                "id": message_id,
                "thread_id": thread_id,
                "imap_uid": uid.decode(),
                "from": from_addr,
                "to": to_addr,
                "subject": subject,
                "date": date,
                "received_at": received_at,
                "body": body,
                "images": images,
                "snippet": body[:150],
            }
        except Exception as e:
            print(f"Error procesando mensaje UID {uid}: {e}")
            return None

    def _decode_header(self, value: str) -> str:
        parts = decode_header(value)
        decoded = []
        for part, charset in parts:
            if isinstance(part, bytes):
                decoded.append(part.decode(charset or "utf-8", errors="ignore"))
            else:
                decoded.append(part)
        return " ".join(decoded)

    def _extract_body(self, msg) -> tuple[str, list]:
        """Retorna (texto_plano, lista_de_imágenes).
        Cada imagen: {"media_type": "image/jpeg", "data": "<base64>"}"""
        import base64 as _b64
        body = ""
        images = []

        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                disposition = part.get("Content-Disposition", "")
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                if ct == "text/plain" and "attachment" not in disposition:
                    if not body:
                        body = payload.decode(part.get_content_charset() or "utf-8", errors="ignore").strip()
                elif ct.startswith("image/"):
                    images.append({
                        "media_type": ct,
                        "data": _b64.standard_b64encode(payload).decode("utf-8"),
                    })
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                body = payload.decode(msg.get_content_charset() or "utf-8", errors="ignore").strip()

        return body, images

    def send_reply(self, thread_id: str, to: str, subject: str, body: str,
                   attachments: list = None, inline_images: list = None) -> bool:
        """
        attachments: archivos adjuntos normales (descargas).
        inline_images: imágenes embebidas en el cuerpo del correo (flyers, etc.).
        """
        import html as _html
        try:
            img_types = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "gif": "gif", "webp": "webp"}

            outer = MIMEMultipart("mixed")
            outer["From"] = self.address
            outer["To"] = to
            outer["Subject"] = subject if subject.startswith("Re:") else f"Re: {subject}"
            outer["In-Reply-To"] = thread_id
            outer["References"] = thread_id

            if inline_images:
                # multipart/related contiene el HTML + las imágenes inline referenciadas por CID
                related = MIMEMultipart("related")

                # Construir HTML: texto plano convertido + imagen(es) al final
                html_text = _html.escape(body).replace("\n", "<br>\n")
                cids = [f"flyer{i}@ipidet" for i in range(len(inline_images))]
                for cid in cids:
                    html_text += (
                        f'<br><br><img src="cid:{cid}" '
                        f'style="max-width:600px;width:100%;display:block;">'
                    )

                alt = MIMEMultipart("alternative")
                alt.attach(MIMEText(body, "plain", "utf-8"))
                alt.attach(MIMEText(
                    f'<html><body style="font-family:Arial,sans-serif;font-size:14px;">'
                    f'{html_text}</body></html>',
                    "html", "utf-8",
                ))
                related.attach(alt)

                for path, cid in zip(inline_images, cids):
                    try:
                        with open(path, "rb") as f:
                            data = f.read()
                        ext = path.rsplit(".", 1)[-1].lower()
                        part = MIMEImage(data, _subtype=img_types.get(ext, "jpeg"))
                        part.add_header("Content-Disposition", "inline")
                        part.add_header("Content-ID", f"<{cid}>")
                        related.attach(part)
                    except Exception as e:
                        print(f"Error embebiendo imagen {path}: {e}")

                outer.attach(related)
            else:
                outer.attach(MIMEText(body, "plain", "utf-8"))

            # Adjuntos normales (archivos de descarga)
            for path in (attachments or []):
                try:
                    with open(path, "rb") as f:
                        data = f.read()
                    filename = path.split("\\")[-1].split("/")[-1]
                    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
                    if ext in img_types:
                        part = MIMEImage(data, _subtype=img_types[ext], name=filename)
                    else:
                        part = MIMEBase("application", "octet-stream")
                        part.set_payload(data)
                        encoders.encode_base64(part)
                    part.add_header("Content-Disposition", "attachment", filename=filename)
                    outer.attach(part)
                except Exception as e:
                    print(f"Error adjuntando {path}: {e}")

            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                server.starttls()
                server.login(self.address, self.app_password)
                server.send_message(outer)
            return True
        except Exception as e:
            print(f"SMTP error: {e}")
            return False

    def send_email(self, to: str, subject: str, body: str) -> str | None:
        """Envía un email nuevo (no reply). Retorna el Message-ID generado o None si falla."""
        import uuid as _uuid
        try:
            msg = MIMEMultipart()
            message_id = f"<billing-{_uuid.uuid4()}@ipidet.org>"
            msg["Message-ID"] = message_id
            msg["From"] = self.address
            msg["To"] = to
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain", "utf-8"))
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                server.starttls()
                server.login(self.address, self.app_password)
                server.send_message(msg)
            return message_id
        except Exception as e:
            print(f"SMTP error (send_email): {e}")
            return None

    def forward_email(self, to: str, email_data: dict) -> bool:
        """Reenvía un email recibido a otra dirección."""
        try:
            msg = MIMEMultipart()
            msg["From"] = self.address
            msg["To"] = to
            msg["Subject"] = f"Fwd: {email_data['subject']}"

            body = (
                f"---------- Mensaje reenviado ----------\n"
                f"De: {email_data['from']}\n"
                f"Fecha: {email_data.get('date', '')}\n"
                f"Asunto: {email_data['subject']}\n"
                f"Para: {email_data.get('to', '')}\n\n"
                f"{email_data.get('body', '')}"
            )
            msg.attach(MIMEText(body, "plain", "utf-8"))

            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                server.starttls()
                server.login(self.address, self.app_password)
                server.send_message(msg)
            return True
        except Exception as e:
            print(f"SMTP error (forward): {e}")
            return False

    def mark_as_read(self, imap_uid: str):
        try:
            conn = self._imap()
            conn.select("INBOX")
            conn.uid("store", imap_uid.encode(), "+FLAGS", "\\Seen")
            conn.logout()
        except Exception:
            pass
