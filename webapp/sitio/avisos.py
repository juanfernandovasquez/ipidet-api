"""Correos del sitio a la persona (Brevo, por mailer.send_email: R9) y avisos al equipo por Telegram.

Todo valor que escribió una persona va escapado: estos correos salen con la marca de IPIDET.
Las funciones son síncronas: se llaman desde rutas `def` o BackgroundTasks (hilo aparte).
"""
import asyncio
import base64
import logging
from html import escape

from webapp import mailer
from webapp.sitio import config

logger = logging.getLogger(__name__)


def _logo() -> list[dict]:
    att = mailer.logo_inline_att()
    if not att:
        return []
    # send_email espera bytes en «data» (logo_inline_att los da en base64).
    return [{**att, "data": base64.b64decode(att["data_b64"])}]


def correo(para: str, asunto: str, parrafos: list[str], boton: tuple[str, str] | None = None,
           destacado: str = "") -> bool:
    cuerpo = "".join(f'<p style="color:#475569;line-height:1.7;margin:0 0 14px">{escape(p)}</p>' for p in parrafos)
    if destacado:
        cuerpo += ('<p style="font-size:30px;font-weight:700;letter-spacing:8px;color:#0f4a2c;margin:18px 0;'
                   f'font-family:monospace">{escape(destacado)}</p>')
    if boton:
        url = config.cargar()["url_base"].rstrip("/") + boton[1]
        cuerpo += (f'<p style="margin:22px 0 0"><a href="{escape(url)}" style="display:inline-block;background:#146c3b;'
                   f'color:#ffffff;text-decoration:none;font-weight:700;padding:11px 20px;border-radius:8px">'
                   f'{escape(boton[0])}</a></p>')
    asunto = " ".join(asunto.split())[:180]
    try:
        asyncio.run(mailer.send_email(para, asunto, mailer._base_html(cuerpo), _logo()))
        return True
    except Exception as e:  # noqa: BLE001 — un correo caído no deshace una inscripción o un pago
        logger.error("sitio: no se pudo enviar «%s»: %s", asunto, e)
        return False


def equipo(texto: str) -> None:
    try:
        from webapp import agente_correo
        agente_correo._tg("sendMessage", chat_id=agente_correo.TELEGRAM_CHAT_ID, text=texto[:3500],
                          disable_web_page_preview="true")
    except Exception as e:  # noqa: BLE001
        logger.error("sitio: no se pudo avisar por Telegram: %s", e)


def codigo(para: str, numero: str, minutos: int) -> bool:
    return correo(para, f"{numero} es tu código de ingreso a IPIDET", [
        "Usa este código para entrar a la web de IPIDET.",
        f"Vence en {minutos} minutos y sirve una sola vez. Si no lo pediste, ignora este correo: "
        "nadie puede entrar sin el código. El equipo de IPIDET nunca te lo pedirá.",
    ], destacado=numero)


def de_orden(orden: dict) -> None:
    """Le cuenta a la persona en qué quedó su inscripción o su pago."""
    que = orden.get("descripcion", "")
    estado = orden.get("estado")
    ruta = ("Ver en Mi cuenta", f"/pagar/{orden['_id']}")
    if estado == "confirmada" and orden.get("tipo") == "evento":
        partes = [f"Tu inscripción quedó confirmada: {que}."]
        if orden.get("monto"):
            partes.append(f"Recibimos tu pago de {config.soles(orden.get('monto_pagado') or orden['monto'])}.")
        partes.append("En Mi cuenta encontrarás los datos para participar.")
        correo(orden["email"], f"Inscripción confirmada: {que}", partes, ("Ver el evento", f"/eventos/{orden['evento_id']}"))
    elif estado == "confirmada":
        correo(orden["email"], f"Pago confirmado: {que}", [
            f"Confirmamos tu pago de {config.soles(orden.get('monto_pagado') or orden['monto'])} por {que}. Gracias.",
            "Verás tu comprobante en Mi cuenta cuando sea emitido.",
        ], ("Ver Mi cuenta", "/cuenta"))
    elif estado == "por_pagar":
        correo(orden["email"], f"Completa tu pago: {que}", [
            f"Separamos tu solicitud: {que}, por {config.soles(orden['monto'])}.",
            "Entra a Mi cuenta para ver los medios de pago e informar tu operación. "
            "Se confirma cuando verifiquemos el abono.",
        ], ruta)
    elif estado == "en_revision":
        correo(orden["email"], f"Recibimos los datos de tu pago: {que}", [
            f"Vamos a verificar tu pago de {config.soles(orden['monto'])} por {que}. Te avisaremos al confirmarlo.",
        ], ruta)
    elif estado == "observada":
        correo(orden["email"], f"Revisa tu pago: {que}", [
            f"Todavía no pudimos confirmar tu pago por {que}.",
            f"Motivo: {orden.get('observacion', '')}",
            "Puedes corregir los datos o adjuntar otra constancia desde Mi cuenta.",
        ], ruta)
    elif estado == "anulada":
        correo(orden["email"], f"Solicitud anulada: {que}", [
            f"Se anuló tu solicitud: {que}.", *([f"Motivo: {orden['observacion']}"] if orden.get("observacion") else []),
        ], ("Ver Mi cuenta", "/cuenta"))
