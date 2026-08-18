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
