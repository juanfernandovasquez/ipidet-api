"""
Scheduler de cobranzas: envía recordatorios de cuota pendiente periódicamente.
Se lanza como asyncio.create_task() desde el startup de FastAPI.
"""
import asyncio
import logging
from datetime import datetime, timezone

from config.settings import (
    SCHEDULER_ENABLED,
    SCHEDULER_INTERVAL_HOURS,
    SCHEDULER_PERIODO,
    SCHEDULER_ESTADOS,
)
import webapp.db as pdb
import webapp.mailer as mailer

logger = logging.getLogger("scheduler")

# Estado en memoria para el panel de control
_estado = {
    "ultimo_envio": None,   # datetime UTC del último ciclo completado
    "enviados":     0,
    "fallidos":     0,
    "activo":       False,
}


def get_estado() -> dict:
    return dict(_estado)


async def run_scheduler():
    if not SCHEDULER_ENABLED:
        logger.info("Scheduler deshabilitado (SCHEDULER_ENABLED=false)")
        return

    _estado["activo"] = True
    logger.info(
        "Scheduler iniciado — período %s, estados %s, intervalo %sh",
        SCHEDULER_PERIODO, SCHEDULER_ESTADOS, SCHEDULER_INTERVAL_HOURS,
    )

    while True:
        await asyncio.sleep(SCHEDULER_INTERVAL_HOURS * 3600)
        await _ejecutar_ciclo()


async def _ejecutar_ciclo():
    logger.info("Scheduler: iniciando ciclo de recordatorios")
    enviados, fallidos = 0, 0
    destinatarios = []

    try:
        destinatarios = pdb.get_comunicacion_destinatarios(
            periodo=SCHEDULER_PERIODO,
            estados_pago=SCHEDULER_ESTADOS,
        )
    except Exception as exc:
        logger.error("Scheduler: error consultando destinatarios — %s", exc)
        return

    for dest in destinatarios:
        try:
            html = mailer.tpl_recordatorio_cuota(dest["nombre"], SCHEDULER_PERIODO)
            await mailer.send_email(
                dest["email"],
                f"Recordatorio: cuota IPIDET {SCHEDULER_PERIODO} pendiente",
                html,
            )
            enviados += 1
        except Exception as exc:
            logger.warning("Scheduler: fallo al enviar a %s — %s", dest["email"], exc)
            fallidos += 1

    try:
        pdb.save_comunicacion_log(
            asunto=f"Recordatorio automático cuota {SCHEDULER_PERIODO}",
            plantilla="recordatorio_auto",
            filtros={"periodo": SCHEDULER_PERIODO, "estados": SCHEDULER_ESTADOS},
            destinatarios=destinatarios,
            usuario="scheduler",
        )
    except Exception as exc:
        logger.error("Scheduler: error guardando log — %s", exc)

    _estado.update({
        "ultimo_envio": datetime.now(timezone.utc).isoformat(),
        "enviados":     enviados,
        "fallidos":     fallidos,
    })
    logger.info("Scheduler: ciclo completado — %d enviados, %d fallidos", enviados, fallidos)


# ── Envíos programados ─────────────────────────────────────────────────────────

PROGRAMADOS_INTERVAL = 300  # 5 minutos


async def run_programados_scheduler():
    """Chequea cada 5 minutos si hay envíos masivos programados listos para enviarse."""
    logger.info("Scheduler de envíos programados iniciado")

    # Ejecutar inmediatamente al arrancar por si quedó algo pendiente del pasado
    await _ejecutar_programados()

    while True:
        await asyncio.sleep(PROGRAMADOS_INTERVAL)
        await _ejecutar_programados()


async def _ejecutar_programados():
    try:
        pendientes = pdb.get_envios_programados_pendientes()
    except Exception as exc:
        logger.error("Programados: error consultando pendientes — %s", exc)
        return

    if not pendientes:
        return

    for envio in pendientes:
        envio_id = envio["_id"]
        logger.info("Programados: ejecutando envío %s — '%s'", envio_id, envio.get("asunto", ""))

        # Marcar como enviando para evitar doble ejecución en caso de restart
        try:
            pdb.update_envio_programado_estado(envio_id, "enviando")
        except Exception as exc:
            logger.error("Programados: no se pudo marcar como enviando %s — %s", envio_id, exc)
            continue

        try:
            asunto      = envio.get("asunto", "")
            cuerpo      = envio.get("cuerpo", "")
            disclaimer  = envio.get("disclaimer", True)
            destinatarios = envio.get("destinatarios", [])

            mensajes = []
            for d in destinatarios:
                email  = d.get("email", "")
                nombre = d.get("nombre", "")
                if not email:
                    continue
                cuerpo_p    = cuerpo.replace("{{nombre}}", nombre)
                cuerpo_html = cuerpo_p.replace("\n", "<br>")
                html = mailer._base_html(
                    f'<p style="color:#475569;line-height:1.7">{cuerpo_html}</p>',
                    disclaimer=disclaimer,
                )
                asunto_p = asunto.replace("{{nombre}}", nombre)
                mensajes.append({"to": email, "nombre": nombre,
                                  "subject": asunto_p, "html_body": html})

            enviados, fallidos, _, fallidos_detalle = await mailer.send_bulk(mensajes)

            try:
                pdb.save_comunicacion_log(
                    asunto=asunto,
                    plantilla="programado",
                    filtros=envio.get("filtros", {}),
                    destinatarios=destinatarios,
                    usuario="scheduler_programado",
                    fallidos_detalle=fallidos_detalle,
                )
            except Exception as exc:
                logger.error("Programados: error guardando log — %s", exc)

            pdb.update_envio_programado_estado(envio_id, "enviado", resultado={
                "enviados": enviados,
                "fallidos": fallidos,
            })
            logger.info("Programados: %s completado — %d enviados, %d fallidos",
                        envio_id, enviados, fallidos)

        except Exception as exc:
            logger.error("Programados: error ejecutando %s — %s", envio_id, exc)
            try:
                pdb.update_envio_programado_estado(envio_id, "fallido",
                                                    resultado={"error": str(exc)})
            except Exception:
                pass
