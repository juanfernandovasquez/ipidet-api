"""
Envío de correos transaccionales vía Brevo.
- Si BREVO_API_KEY está configurada: usa HTTP API v3 (funciona en cualquier servidor)
- Si no: fallback a SMTP (requiere puerto 587 abierto y IP autorizada)
"""
import base64 as _b64
import re as _re
import smtplib
import asyncio
import json as _json
import urllib.request
import urllib.error
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from functools import partial
from config.settings import (
    BREVO_SMTP_HOST, BREVO_SMTP_PORT,
    BREVO_SMTP_USER, BREVO_SMTP_PASSWORD,
    BREVO_FROM_EMAIL, BREVO_FROM_NAME,
    BREVO_API_KEY,
)


def _send_sync(
    to: str | list[str],
    subject: str,
    html_body: str,
    attachments: list[dict] | None = None,
    reply_to: str | None = None,
):
    """
    Envía un email via Brevo SMTP (blocking).
    attachments: [{"filename": "...", "data": bytes, "mime": "application/pdf"}]
    """
    recipients = [to] if isinstance(to, str) else to

    msg = MIMEMultipart("mixed")
    msg["From"]    = f"{BREVO_FROM_NAME} <{BREVO_FROM_EMAIL}>"
    msg["To"]      = ", ".join(recipients)
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to

    msg.attach(MIMEText(html_body, "html", "utf-8"))

    for att in (attachments or []):
        part = MIMEBase(*att.get("mime", "application/octet-stream").split("/", 1))
        part.set_payload(att["data"])
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment",
                        filename=att["filename"])
        msg.attach(part)

    with smtplib.SMTP(BREVO_SMTP_HOST, BREVO_SMTP_PORT, timeout=20) as server:
        server.ehlo()
        server.starttls()
        server.login(BREVO_SMTP_USER, BREVO_SMTP_PASSWORD)
        server.sendmail(BREVO_FROM_EMAIL, recipients, msg.as_bytes())


def _send_api(to: str, subject: str, html_body: str,
              attachments: list[dict] | None = None, reply_to: str | None = None):
    """Envía un email via Brevo HTTP API v3."""
    payload: dict = {
        "sender":      {"name": BREVO_FROM_NAME, "email": BREVO_FROM_EMAIL},
        "to":          [{"email": to}] if isinstance(to, str) else [{"email": e} for e in to],
        "subject":     subject,
        "htmlContent": html_body,
    }
    if reply_to:
        payload["replyTo"] = {"email": reply_to}
    req = urllib.request.Request(
        "https://api.brevo.com/v3/smtp/email",
        data=_json.dumps(payload).encode("utf-8"),
        headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=30)


async def send_email(
    to: str | list[str],
    subject: str,
    html_body: str,
    attachments: list[dict] | None = None,
    reply_to: str | None = None,
):
    """Usa HTTP API si BREVO_API_KEY está configurada; si no, SMTP."""
    loop = asyncio.get_running_loop()
    if BREVO_API_KEY:
        await loop.run_in_executor(
            None, partial(_send_api, to, subject, html_body, attachments, reply_to)
        )
    else:
        await loop.run_in_executor(
            None, partial(_send_sync, to, subject, html_body, attachments, reply_to)
        )


_HAS_HTML = _re.compile(r'<[a-zA-Z/!]')


def _render_body(cuerpo: str) -> str:
    """Plain text → \n a <br>. Si hay etiquetas HTML, pasa el cuerpo directo."""
    if _HAS_HTML.search(cuerpo):
        return cuerpo
    return cuerpo.replace("\n", "<br>")


def _send_bulk_sync(mensajes: list[dict], attachments: list[dict] | None = None) -> tuple[int, int, list[str], list[dict]]:
    """
    Envía múltiples emails en una sola sesión SMTP.
    mensajes: [{"to": str, "nombre": str, "subject": str, "html_body": str}]
    Devuelve (enviados, fallidos, errores[], fallidos_detalle[]).
    """
    enviados, fallidos, errores, fallidos_detalle = 0, 0, [], []
    try:
        server = smtplib.SMTP(BREVO_SMTP_HOST, BREVO_SMTP_PORT, timeout=30)
        server.ehlo()
        server.starttls()
        server.login(BREVO_SMTP_USER, BREVO_SMTP_PASSWORD)
    except Exception as exc:
        err = str(exc)
        return 0, len(mensajes), [err], [
            {"email": m["to"], "nombre": m.get("nombre", ""), "error": err}
            for m in mensajes
        ]

    # Decodificar adjuntos una sola vez para toda la sesión SMTP
    att_bytes = []
    for a in (attachments or []):
        try:
            data = _b64.b64decode(a["data_b64"]) if "data_b64" in a else a.get("data", b"")
            att_bytes.append({"filename": a["filename"], "data": data,
                               "mime": a.get("mime", "application/octet-stream")})
        except Exception:
            pass

    try:
        for m in mensajes:
            try:
                msg = MIMEMultipart("mixed")
                msg["From"]    = f"{BREVO_FROM_NAME} <{BREVO_FROM_EMAIL}>"
                msg["To"]      = m["to"]
                msg["Subject"] = m["subject"]
                msg.attach(MIMEText(m["html_body"], "html", "utf-8"))
                for att in att_bytes:
                    mime_type, mime_subtype = att["mime"].split("/", 1)
                    part = MIMEBase(mime_type, mime_subtype)
                    part.set_payload(att["data"])
                    encoders.encode_base64(part)
                    part.add_header("Content-Disposition", "attachment", filename=att["filename"])
                    msg.attach(part)
                server.sendmail(BREVO_FROM_EMAIL, [m["to"]], msg.as_bytes())
                enviados += 1
            except Exception as exc:
                fallidos += 1
                err = str(exc)
                if err not in errores:
                    errores.append(err)
                fallidos_detalle.append({"email": m["to"], "nombre": m.get("nombre", ""), "error": err})
    finally:
        try:
            server.quit()
        except Exception:
            pass

    return enviados, fallidos, errores, fallidos_detalle


def _send_bulk_api(mensajes: list[dict], attachments: list[dict] | None = None) -> tuple[int, int, list[str], list[dict]]:
    """Usa Brevo HTTP API v3 — funciona en Render y cualquier servidor (puerto 443)."""
    enviados, fallidos, errores, fallidos_detalle = 0, 0, [], []

    # Preparar adjuntos en formato Brevo (base64 ya listo)
    att_brevo = []
    for a in (attachments or []):
        att_brevo.append({"name": a["filename"], "content": a.get("data_b64", "")})

    for m in mensajes:
        body: dict = {
            "sender":      {"name": BREVO_FROM_NAME, "email": BREVO_FROM_EMAIL},
            "to":          [{"email": m["to"]}],
            "subject":     m["subject"],
            "htmlContent": m["html_body"],
        }
        if att_brevo:
            body["attachment"] = att_brevo
        payload = _json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            "https://api.brevo.com/v3/smtp/email",
            data=payload,
            headers={
                "api-key":      BREVO_API_KEY,
                "Content-Type": "application/json",
                "Accept":       "application/json",
            },
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=30)
            enviados += 1
        except urllib.error.HTTPError as exc:
            fallidos += 1
            body = exc.read().decode("utf-8", errors="replace")
            err = f"HTTP {exc.code}: {body[:200]}"
            if err not in errores:
                errores.append(err)
            fallidos_detalle.append({"email": m["to"], "nombre": m.get("nombre", ""), "error": err})
        except Exception as exc:
            fallidos += 1
            err = str(exc)
            if err not in errores:
                errores.append(err)
            fallidos_detalle.append({"email": m["to"], "nombre": m.get("nombre", ""), "error": err})
    return enviados, fallidos, errores, fallidos_detalle


async def send_bulk(mensajes: list[dict],
                    attachments: list[dict] | None = None) -> tuple[int, int, list[str], list[dict]]:
    """
    Usa HTTP API si BREVO_API_KEY está configurada; si no, SMTP.
    attachments: [{"filename": str, "data_b64": str, "mime": str}]  — mismo adjunto para todos.
    """
    loop = asyncio.get_running_loop()
    if BREVO_API_KEY:
        return await loop.run_in_executor(None, partial(_send_bulk_api, mensajes, attachments))
    return await loop.run_in_executor(None, partial(_send_bulk_sync, mensajes, attachments))


# ── Plantillas ────────────────────────────────────────────────────────────────

def _base_html(contenido: str, disclaimer: bool = True) -> str:
    disclaimer_line = (
        "<br>Este correo fue generado automáticamente. No responder directamente a este mensaje."
        if disclaimer else ""
    )
    return f"""
<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f8fafc;font-family:Arial,sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc;padding:32px 16px">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e2e8f0">
        <!-- Header -->
        <tr><td style="background:#1e3a5f;padding:24px 32px">
          <p style="margin:0;font-size:22px;font-weight:700;color:#ffffff">IPIDET</p>
          <p style="margin:4px 0 0;font-size:12px;color:#94a3b8">Instituto Peruano de Investigación y Desarrollo Tributario</p>
        </td></tr>
        <!-- Body -->
        <tr><td style="padding:32px">
          {contenido}
        </td></tr>
        <!-- Footer -->
        <tr><td style="background:#f1f5f9;padding:16px 32px;border-top:1px solid #e2e8f0">
          <p style="margin:0;font-size:11px;color:#94a3b8;text-align:center">
            IPIDET · administracion@ipidet.org{disclaimer_line}
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def tpl_bienvenida(nombre: str) -> str:
    return _base_html(f"""
      <h2 style="margin:0 0 16px;color:#1e3a5f;font-size:20px">Bienvenido/a a IPIDET</h2>
      <p style="color:#475569;line-height:1.6">Estimado/a <strong>{nombre}</strong>,</p>
      <p style="color:#475569;line-height:1.6">
        Nos complace confirmar su membresía en IPIDET. A partir de ahora tiene acceso
        a todos los beneficios de nuestra comunidad profesional.
      </p>
      <p style="color:#475569;line-height:1.6">Ante cualquier consulta, comuníquese con nosotros respondiendo a este correo.</p>
      <p style="color:#475569;line-height:1.6;margin-top:24px">Cordialmente,<br><strong>Equipo IPIDET</strong></p>
    """)


def tpl_recordatorio_cuota(nombre: str, periodo: str, monto: str = "") -> str:
    monto_line = f"<p style='color:#475569;line-height:1.6'>Monto: <strong>S/ {monto}</strong></p>" if monto else ""
    return _base_html(f"""
      <h2 style="margin:0 0 16px;color:#1e3a5f;font-size:20px">Recordatorio de cuota {periodo}</h2>
      <p style="color:#475569;line-height:1.6">Estimado/a <strong>{nombre}</strong>,</p>
      <p style="color:#475569;line-height:1.6">
        Le recordamos que tiene pendiente el pago de su cuota anual de membresía correspondiente al período <strong>{periodo}</strong>.
      </p>
      {monto_line}
      <p style="color:#475569;line-height:1.6">
        Para realizar su pago o consultar las opciones de fraccionamiento, comuníquese con nosotros.
      </p>
      <p style="color:#475569;line-height:1.6;margin-top:24px">Cordialmente,<br><strong>Equipo IPIDET</strong></p>
    """)


def tpl_comprobante(nombre: str, num_comprobante: str, tipo: str,
                    periodo: str, fecha_emision: str) -> str:
    tipo_label = {"boleta": "Boleta de Venta", "factura": "Factura",
                  "recibo": "Recibo por Honorarios"}.get(tipo, tipo.capitalize())
    return _base_html(f"""
      <h2 style="margin:0 0 16px;color:#1e3a5f;font-size:20px">Comprobante de pago — {periodo}</h2>
      <p style="color:#475569;line-height:1.6">Estimado/a <strong>{nombre}</strong>,</p>
      <p style="color:#475569;line-height:1.6">
        Adjunto encontrará el comprobante correspondiente a su cuota de membresía {periodo}.
      </p>
      <table style="border-collapse:collapse;width:100%;margin:20px 0">
        <tr style="background:#f1f5f9">
          <td style="padding:10px 16px;font-size:13px;color:#64748b;font-weight:600">Tipo</td>
          <td style="padding:10px 16px;font-size:13px;color:#1e293b">{tipo_label}</td>
        </tr>
        <tr>
          <td style="padding:10px 16px;font-size:13px;color:#64748b;font-weight:600">N° comprobante</td>
          <td style="padding:10px 16px;font-size:13px;color:#1e293b">{num_comprobante}</td>
        </tr>
        <tr style="background:#f1f5f9">
          <td style="padding:10px 16px;font-size:13px;color:#64748b;font-weight:600">Fecha de emisión</td>
          <td style="padding:10px 16px;font-size:13px;color:#1e293b">{fecha_emision}</td>
        </tr>
        <tr>
          <td style="padding:10px 16px;font-size:13px;color:#64748b;font-weight:600">Período</td>
          <td style="padding:10px 16px;font-size:13px;color:#1e293b">{periodo}</td>
        </tr>
      </table>
      <p style="color:#475569;line-height:1.6;margin-top:8px">
        Si tiene alguna consulta sobre este comprobante, comuníquese con nosotros.
      </p>
      <p style="color:#475569;line-height:1.6;margin-top:24px">Cordialmente,<br><strong>Equipo IPIDET</strong></p>
    """)
