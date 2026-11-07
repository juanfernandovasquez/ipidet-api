"""
Búsqueda de instituciones para posibles alianzas, con Claude y búsqueda web.

La corre cada semana scheduler.run_investigacion_scheduler y también el botón «Buscar ahora».
No toca socios ni envía nada: solo lee la web y propone. El equipo decide qué hacer.

Lo que entra por la web no es de fiar, así que una propuesta solo se guarda si al menos
una de sus fuentes apareció de verdad en lo que consultó esta misma búsqueda.
"""
import json
import logging
from datetime import datetime, timedelta, timezone

from config import catalogos
from config.settings import ANTHROPIC_API_KEY
import webapp.db as pdb
from webapp import investigacion_ia as ia

logger = logging.getLogger("alianzas")

ESPERA_TRAS_FALLO = timedelta(hours=24)

SISTEMA = f"""Investigas posibles alianzas institucionales para una asociación profesional peruana del ámbito tributario.

Usa la búsqueda web para encontrar instituciones reales y vigentes, y termina llamando una sola vez a `registrar_instituciones`.

Reglas de calidad:
- Cada afirmación concreta debe poder respaldarse con una fuente que hayas consultado en esta búsqueda. Copia las URL exactas.
- Argumenta para esta asociación en particular. Evita frases que servirían para cualquier institución; cita programas, publicaciones, redes o actividades concretas.
- `por_que_importante` explica el peso de la institución en su campo. `por_que_estrategica` explica qué ganaría esta asociación con la unión y por qué ahora; no repitas lo mismo en los dos campos.
- Señala los riesgos con honestidad, en especial conflictos de interés o amenazas a la independencia técnica. Si no conoces ninguno, dilo.
- Si no estás seguro de que exista una relación previa con la asociación, no la afirmes.
- Es mejor proponer menos instituciones que rellenar con candidatas débiles. Si ninguna es sólida, registra una lista vacía.
- Escribe en español, con frases claras y sin relleno.

{ia.AVISO_CONTENIDO_WEB}"""


def _esquema(tipos: list[str]) -> dict:
    texto = {"type": "string"}
    puntaje = {"type": "integer", "enum": [1, 2, 3, 4, 5]}
    institucion = {
        "type": "object",
        "additionalProperties": False,
        "required": ["nombre", "siglas", "tipo", "ambito", "pais", "web", "que_hace", "por_que_importante",
                     "por_que_estrategica", "como_colaborar", "primer_paso", "riesgos", "afinidad", "impacto",
                     "viabilidad", "novedad", "fuentes"],
        "properties": {
            "nombre": {"type": "string", "description": "Nombre oficial completo."},
            "siglas": {"type": "string", "description": "Siglas oficiales, o vacío si no tiene."},
            "tipo": {"type": "string", "enum": tipos},
            "ambito": {"type": "string", "enum": list(catalogos.AMBITOS_ALIANZA)},
            "pais": texto,
            "web": {"type": "string", "description": "Sitio oficial, o vacío si no lo confirmaste."},
            "que_hace": {"type": "string", "description": "Una o dos frases."},
            "por_que_importante": texto,
            "por_que_estrategica": texto,
            "como_colaborar": {"type": "array", "items": texto, "description": "Formas concretas de colaboración."},
            "primer_paso": {"type": "string", "description": "Siguiente acción concreta para acercarse."},
            "riesgos": texto,
            "afinidad": {**puntaje, "description": "Cercanía con la misión. 5 es muy alta."},
            "impacto": {**puntaje, "description": "Beneficio potencial. 5 es muy alto."},
            "viabilidad": {**puntaje, "description": "Facilidad real de concretarla. 5 es muy fácil."},
            "novedad": {"type": "string", "description": "Solo para una institución ya registrada: qué cambió. Vacío en las nuevas."},
            "fuentes": {
                "type": "array",
                "items": {"type": "object", "additionalProperties": False, "required": ["titulo", "url"],
                          "properties": {"titulo": texto, "url": texto}},
            },
        },
    }
    return {
        "name": "registrar_instituciones",
        "description": "Registra el resultado final de la búsqueda. Llámala una sola vez, al terminar de investigar.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["instituciones"],
            "properties": {"instituciones": {"type": "array", "items": institucion}},
        },
    }


def _mensaje(cfg: dict, tipos: list[dict], existentes: list[dict], org: dict) -> str:
    activas = [f"- {e['nombre']}" for e in existentes if e.get("estado") != "descartada"]
    descartadas = [f"- {e['nombre']}" for e in existentes if e.get("estado") == "descartada"]
    return f"""Fecha de hoy: {datetime.now(timezone.utc):%Y-%m-%d}

<asociacion>
{cfg['perfil']}
Sitio web: {org.get('web', '')}
</asociacion>

<criterios>
{cfg['criterios']}
</criterios>

<tipos_de_institucion>
{json.dumps(tipos, ensure_ascii=False)}
</tipos_de_institucion>

<ya_registradas>
{chr(10).join(activas) or 'Ninguna.'}
</ya_registradas>

<descartadas_no_proponer>
{chr(10).join(descartadas) or 'Ninguna.'}
</descartadas_no_proponer>

Revisa primero el sitio de la asociación y encuentra hasta {cfg['propuestas_por_corrida']} instituciones nuevas, de Perú y del exterior, que valga la pena considerar como aliadas. No repitas las ya registradas ni las descartadas. Inclúyelas solo si encontraste una novedad reciente y verificable que cambie la oportunidad, y explícala en `novedad`."""


def _preparar(item: dict, tipos_validos: set, consultadas: set) -> tuple[dict | None, str]:
    """Valida una propuesta. Devuelve (documento, "") o (None, motivo del descarte)."""
    nombre = ia.texto(item.get("nombre"), 200)
    if not nombre:
        return None, "sin nombre"
    fuentes = ia.fuentes_verificadas(item.get("fuentes"), consultadas)
    if not fuentes:
        return None, f"{nombre}: ninguna fuente salió de los resultados de la búsqueda"
    if not all(ia.texto(item.get(c)) for c in ("por_que_importante", "por_que_estrategica")):
        return None, f"{nombre}: sin sustento"

    web = str(item.get("web") or "").strip()
    host = ia.dominio(web)
    if not host or ia.es_red_social(host):
        web, host = "", ""

    return {
        "nombre": nombre,
        "siglas": ia.texto(item.get("siglas"), 30),
        "tipo": item.get("tipo") if item.get("tipo") in tipos_validos else "otra",
        "ambito": item.get("ambito") if item.get("ambito") in catalogos.AMBITOS_ALIANZA else "internacional",
        "pais": ia.texto(item.get("pais"), 80),
        "web": web,
        "dominio": host,
        "que_hace": ia.texto(item.get("que_hace")),
        "por_que_importante": ia.texto(item.get("por_que_importante")),
        "por_que_estrategica": ia.texto(item.get("por_que_estrategica")),
        "como_colaborar": [ia.texto(c, 300) for c in (item.get("como_colaborar") or []) if ia.texto(c)][:8],
        "primer_paso": ia.texto(item.get("primer_paso")),
        "riesgos": ia.texto(item.get("riesgos")),
        "afinidad": ia.puntaje(item.get("afinidad")),
        "impacto": ia.puntaje(item.get("impacto")),
        "viabilidad": ia.puntaje(item.get("viabilidad")),
        "fuentes": fuentes,
        "novedad": ia.texto(item.get("novedad")),
    }, ""


def ejecutar(origen: str) -> dict | None:
    """Corre una búsqueda. Devuelve None si ya hay otra en curso."""
    corrida_id = pdb.reservar_corrida_alianzas(origen)
    if not corrida_id:
        return None
    cfg = catalogos.get_alianzas_config()
    uso = ia.uso_vacio()
    resultado = {"nuevas": 0, "actualizadas": 0, "descartadas": []}
    try:
        if not cfg.get("perfil"):
            raise RuntimeError("Falta el perfil de IPIDET en la configuración de Alianzas.")
        tipos = catalogos.get_catalogo("tipos_institucion")
        if not tipos:
            raise RuntimeError("Falta el catálogo de tipos de institución en /configuracion.")
        mensaje = _mensaje(cfg, tipos, pdb.get_alianzas_nombres(), catalogos.get_organizacion())
        datos, consultadas = ia.investigar(cfg, SISTEMA, mensaje, _esquema([t["valor"] for t in tipos]), uso)
        tipos_validos = {t["valor"] for t in tipos}
        for item in datos.get("instituciones") or []:
            doc, motivo = _preparar(item, tipos_validos, consultadas)
            if not doc:
                resultado["descartadas"].append(motivo)
                continue
            accion = pdb.guardar_propuesta_alianza(doc, corrida_id)
            if accion == "nueva":
                resultado["nuevas"] += 1
            elif accion == "actualizada":
                resultado["actualizadas"] += 1
            else:
                resultado["descartadas"].append(f"{doc['nombre']}: ya estaba descartada o sin novedad")
        pdb.cerrar_corrida_alianzas(corrida_id, "completada", resultado, uso, ia.costo(cfg, uso))
    except Exception as exc:
        logger.exception("Alianzas: la búsqueda falló")
        pdb.cerrar_corrida_alianzas(corrida_id, "fallida", {**resultado, "error": str(exc)[:500]}, uso, ia.costo(cfg, uso))
    return {**resultado, "uso": uso, "corrida_id": corrida_id, "costo": ia.costo(cfg, uso)}


def toca(cfg: dict, estado: dict) -> bool:
    """Respeta la frecuencia configurada. Tras un fallo espera un día para no gastar en reintentos."""
    if not cfg["activa"] or not ANTHROPIC_API_KEY:
        return False
    ahora = datetime.now(timezone.utc)
    exitosa, intento = estado.get("ultima_exitosa"), estado.get("ultimo_intento")
    if exitosa and ahora - exitosa < timedelta(days=int(cfg["frecuencia_dias"])):
        return False
    return not (intento and ahora - intento < ESPERA_TRAS_FALLO)


def ejecutar_si_toca() -> None:
    if toca(catalogos.get_alianzas_config(), pdb.get_alianzas_estado()):
        ejecutar("programada")
