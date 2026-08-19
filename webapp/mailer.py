"""
Envío de correos transaccionales vía Brevo SMTP.
Expone send_email() — blocking, llamar en un thread pool desde rutas async.
"""
import smtplib
import asyncio
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from functools import partial
from config.settings import (
    BREVO_SMTP_HOST, BREVO_SMTP_PORT,
    BREVO_SMTP_USER, BREVO_SMTP_PASSWORD,
    BREVO_FROM_EMAIL, BREVO_FROM_NAME,
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


async def send_email(
    to: str | list[str],
    subject: str,
    html_body: str,
    attachments: list[dict] | None = None,
    reply_to: str | None = None,
):
    """Versión async de _send_sync — no bloquea el event loop."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        partial(_send_sync, to, subject, html_body, attachments, reply_to),
    )


def _send_bulk_sync(
    mensajes: list[dict],
) -> tuple[int, int, list[str]]:
    """
    Envía múltiples emails en una sola sesión SMTP.
    mensajes: [{"to": str, "subject": str, "html_body": str}]
    Devuelve (enviados, fallidos, errores[]).
    """
    enviados, fallidos, errores = 0, 0, []
    try:
        server = smtplib.SMTP(BREVO_SMTP_HOST, BREVO_SMTP_PORT, timeout=30)
        server.ehlo()
        server.starttls()
        server.login(BREVO_SMTP_USER, BREVO_SMTP_PASSWORD)
    except Exception as exc:
        return 0, len(mensajes), [str(exc)]

    try:
        for m in mensajes:
            try:
                msg = MIMEMultipart("mixed")
                msg["From"]    = f"{BREVO_FROM_NAME} <{BREVO_FROM_EMAIL}>"
                msg["To"]      = m["to"]
                msg["Subject"] = m["subject"]
                msg.attach(MIMEText(m["html_body"], "html", "utf-8"))
                server.sendmail(BREVO_FROM_EMAIL, [m["to"]], msg.as_bytes())
                enviados += 1
            except Exception as exc:
                fallidos += 1
                msg_err = str(exc)
                if msg_err not in errores:
                    errores.append(msg_err)
    finally:
        try:
            server.quit()
        except Exception:
            pass

    return enviados, fallidos, errores


async def send_bulk(mensajes: list[dict]) -> tuple[int, int, list[str]]:
    """Versión async de _send_bulk_sync."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(_send_bulk_sync, mensajes))


# ── Plantillas ────────────────────────────────────────────────────────────────

def _base_html(contenido: str) -> str:
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
          <p style="margin:4px 0 0;font-size:12px;color:#94a3b8">Instituto Profesional e Integración de Estudios Tributarios</p>
        </td></tr>
        <!-- Body -->
        <tr><td style="padding:32px">
          {contenido}
        </td></tr>
        <!-- Footer -->
        <tr><td style="background:#f1f5f9;padding:16px 32px;border-top:1px solid #e2e8f0">
          <p style="margin:0;font-size:11px;color:#94a3b8;text-align:center">
            IPIDET · administracion@ipidet.org<br>
            Este correo fue generado automáticamente. No responder directamente a este mensaje.
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
