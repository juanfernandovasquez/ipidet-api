"""
Búsqueda de posibles ponentes, con Claude y búsqueda web.

Dos modos: semanal (temas prioritarios y eventos próximos) y por evento (botón en cada
evento). La búsqueda prioriza a profesionales de otras instituciones: los socios ya están
en la plataforma.

Son personas, así que las reglas son más estrictas que en alianzas: solo información
profesional pública, cada dato con fuente consultada en esta búsqueda, y nunca correos
ni teléfonos. El contacto se hace por la vía profesional que indique la ficha.
"""
import logging
import re
from datetime import datetime, timezone

from config import catalogos
import webapp.db as pdb
from webapp import alianzas_ia, investigacion_ia as ia

logger = logging.getLogger("ponentes")

SISTEMA = f"""Buscas posibles ponentes para los eventos de una asociación profesional peruana del ámbito tributario.

Usa la búsqueda web para encontrar personas reales con trayectoria verificable, y termina llamando una sola vez a `registrar_ponentes`.

A quién buscar:
- Prioriza profesionales de otras instituciones: universidades, organismos internacionales, institutos y asociaciones fiscales, firmas, empresas y entidades públicas, del Perú y del extranjero, sobre todo de América Latina y España.
- Deben poder exponer en español, salvo que su aporte justifique otro idioma; si es así, dilo en `consideraciones`.
- Busca a quienes ya exponen, enseñan o publican sobre el tema, no solo a quienes tienen un cargo alto.

Reglas de calidad:
- Cada dato concreto, como cargo, institución, libro, artículo o conferencia, debe respaldarse con una fuente consultada en esta búsqueda. Copia las URL exactas.
- Si no confirmaste que el cargo sea el actual, dilo. Nunca inventes ni completes datos de una persona.
- `por_que_relevante` explica por qué interesa a este público en particular. `trayectoria` da la evidencia de que expone o publica sobre el tema. No repitas lo mismo en los dos campos.
- Indica si es funcionario público (por ejemplo de la administración tributaria, el tribunal fiscal, el ministerio de economía o el poder judicial), porque puede tener restricciones para participar o cobrar honorarios. Señala también conflictos de interés.
- Es mejor proponer menos personas que rellenar con candidatos débiles. Si nadie es sólido, registra una lista vacía.
- Escribe en español, con frases claras y sin relleno.

Privacidad:
- Usa solo información profesional pública.
- No incluyas correos, teléfonos, direcciones ni datos personales o familiares. En `canal_contacto` describe la vía profesional para contactar, como su perfil de LinkedIn o la institución donde trabaja, sin datos de contacto.

{ia.AVISO_CONTENIDO_WEB}"""

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
_TELEFONO = re.compile(r"\+?\d[\d\s().-]{7,}\d")


def _sin_contacto(valor: str) -> str:
    """Quita correos y números de teléfono aunque la instrucción ya lo pida."""
    valor = _EMAIL.sub("[dato omitido]", valor)
    return _TELEFONO.sub(lambda m: "[dato omitido]" if sum(c.isdigit() for c in m.group()) >= 9 else m.group(), valor)


def _esquema() -> dict:
    texto = {"type": "string"}
    puntaje = {"type": "integer", "enum": [1, 2, 3, 4, 5]}
    persona = {
        "type": "object",
        "additionalProperties": False,
        "required": ["nombre", "cargo", "institucion", "pais", "ambito", "temas", "por_que_relevante", "trayectoria",
                     "charlas_posibles", "relacion_evento", "canal_contacto", "linkedin", "perfil_url",
                     "es_funcionario_publico", "consideraciones", "relevancia", "prestigio", "viabilidad",
                     "novedad", "fuentes"],
        "properties": {
            "nombre": {"type": "string", "description": "Nombre completo, tal como aparece en las fuentes."},
            "cargo": {"type": "string", "description": "Cargo o rol profesional confirmado."},
            "institucion": texto,
            "pais": texto,
            "ambito": {"type": "string", "enum": list(catalogos.AMBITOS_ALIANZA)},
            "temas": {"type": "array", "items": texto, "description": "Temas en los que es referente."},
            "por_que_relevante": texto,
            "trayectoria": texto,
            "charlas_posibles": {"type": "array", "items": texto, "description": "Títulos tentativos de exposiciones para este público."},
            "relacion_evento": {"type": "string", "description": "Si se pidió para un evento: por qué encaja en él. Si no, vacío."},
            "canal_contacto": {"type": "string", "description": "Vía profesional para contactar, sin correos ni teléfonos."},
            "linkedin": {"type": "string", "description": "URL de su perfil si apareció en la búsqueda, o vacío."},
            "perfil_url": {"type": "string", "description": "Página institucional con su perfil, o vacío."},
            "es_funcionario_publico": {"type": "boolean"},
            "consideraciones": texto,
            "relevancia": {**puntaje, "description": "Interés para este público. 5 es muy alto."},
            "prestigio": {**puntaje, "description": "Reconocimiento en su campo. 5 es muy alto."},
            "viabilidad": {**puntaje, "description": "Probabilidad real de que acepte. 5 es muy alta."},
            "novedad": {"type": "string", "description": "Solo para alguien ya registrado: qué cambió. Vacío en los nuevos."},
            "fuentes": {
                "type": "array",
                "items": {"type": "object", "additionalProperties": False, "required": ["titulo", "url"],
                          "properties": {"titulo": texto, "url": texto}},
            },
        },
    }
    return {
        "name": "registrar_ponentes",
        "description": "Registra el resultado final de la búsqueda. Llámala una sola vez, al terminar de investigar.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["ponentes"],
            "properties": {"ponentes": {"type": "array", "items": persona}},
        },
    }


def _mensaje(cfg: dict, contexto: dict, evento: dict | None) -> str:
    def lista(items, vacio="Ninguno."):
        return "\n".join(f"- {x}" for x in items) or vacio

    proximos = [f"{e['fecha']}: {e['titulo']}" for e in contexto["proximos"]]
    asistencia = [f"{e['titulo']} ({e['asistentes']} de {e['inscritos']} inscritos asistieron)" for e in contexto["asistencia"]]
    pedido = (
        f"""<evento>
Título: {evento['titulo']}
Fecha: {evento.get('fecha', '')}
Descripción: {ia.texto(evento.get('descripcion'), 1500) or 'Sin descripción.'}
</evento>

Encuentra hasta {cfg['propuestas_por_corrida']} posibles ponentes para este evento. En `relacion_evento` explica por qué cada uno encaja."""
        if evento else
        f"Encuentra hasta {cfg['propuestas_por_corrida']} posibles ponentes nuevos para los temas prioritarios y los eventos próximos. Deja `relacion_evento` vacío."
    )
    return f"""Fecha de hoy: {datetime.now(timezone.utc):%Y-%m-%d}

<asociacion>
{catalogos.get_alianzas_config().get('perfil', '')}
</asociacion>

<publico>
{cfg['publico']}
</publico>

<temas_prioritarios>
{cfg['temas']}
</temas_prioritarios>

<eventos_proximos>
{lista(proximos)}
</eventos_proximos>

<asistencia_de_eventos_anteriores>
{lista(asistencia, 'Sin datos.')}
</asistencia_de_eventos_anteriores>

<instituciones_en_estudio_como_posibles_aliadas>
Pueden ser una vía para encontrar o contactar ponentes. No son aliadas confirmadas: no las presentes como tales.
{lista(contexto['instituciones'])}
</instituciones_en_estudio_como_posibles_aliadas>

<ya_registrados>
{lista(contexto['registrados'])}
</ya_registrados>

<descartados_no_proponer>
{lista(contexto['descartados'])}
</descartados_no_proponer>

{pedido} No repitas a los ya registrados ni a los descartados, salvo una novedad reciente y verificable que explicarás en `novedad`."""


def _preparar(item: dict, consultadas: set) -> tuple[dict | None, str]:
    nombre = ia.texto(item.get("nombre"), 150)
    if not nombre:
        return None, "sin nombre"
    fuentes = ia.fuentes_verificadas(item.get("fuentes"), consultadas)
    if not fuentes:
        return None, f"{nombre}: ninguna fuente salió de los resultados de la búsqueda"
    if not ia.texto(item.get("por_que_relevante")):
        return None, f"{nombre}: sin sustento"

    def url_verificada(campo, solo_linkedin=False):
        url = str(item.get(campo) or "").strip()
        if not ia.url_clave(url) or ia.url_clave(url) not in consultadas:
            return ""
        if solo_linkedin:
            try:
                return pdb.normalizar_linkedin(url)
            except ValueError:
                return ""
        return url

    def limpio(campo, largo=ia.LARGO_MAXIMO):
        return _sin_contacto(ia.texto(item.get(campo), largo))

    return {
        "nombre": nombre,
        "cargo": limpio("cargo", 200),
        "institucion": limpio("institucion", 200),
        "pais": ia.texto(item.get("pais"), 80),
        "ambito": item.get("ambito") if item.get("ambito") in catalogos.AMBITOS_ALIANZA else "internacional",
        "temas": [ia.texto(t, 120) for t in (item.get("temas") or []) if ia.texto(t)][:8],
        "por_que_relevante": limpio("por_que_relevante"),
        "trayectoria": limpio("trayectoria"),
        "charlas_posibles": [_sin_contacto(ia.texto(c, 200)) for c in (item.get("charlas_posibles") or []) if ia.texto(c)][:5],
        "canal_contacto": limpio("canal_contacto", 400),
        "linkedin": url_verificada("linkedin", solo_linkedin=True),
        "perfil_url": url_verificada("perfil_url"),
        "es_funcionario_publico": bool(item.get("es_funcionario_publico")),
        "consideraciones": limpio("consideraciones"),
        "relevancia": ia.puntaje(item.get("relevancia")),
        "prestigio": ia.puntaje(item.get("prestigio")),
        "viabilidad": ia.puntaje(item.get("viabilidad")),
        "fuentes": fuentes,
        "novedad": limpio("novedad"),
        "relacion_evento": limpio("relacion_evento"),
    }, ""


def ejecutar(origen: str, evento_id: str = "") -> dict | None:
    """Corre una búsqueda semanal o para un evento. Devuelve None si ya hay otra en curso."""
    evento = pdb.get_evento(evento_id) if evento_id else None
    if evento_id and not evento:
        return None
    corrida_id = pdb.reservar_corrida_ponentes(origen, evento_id, evento["titulo"] if evento else "")
    if not corrida_id:
        return None
    cfg = catalogos.get_ponentes_config()
    uso = ia.uso_vacio()
    resultado = {"nuevos": 0, "actualizados": 0, "descartados": []}
    try:
        if not cfg.get("publico"):
            raise RuntimeError("Falta describir el público en la configuración de Ponentes.")
        mensaje = _mensaje(cfg, pdb.get_contexto_ponentes(), evento)
        datos, consultadas = ia.investigar(cfg, SISTEMA, mensaje, _esquema(), uso)
        for item in datos.get("ponentes") or []:
            doc, motivo = _preparar(item, consultadas)
            if not doc:
                resultado["descartados"].append(motivo)
                continue
            accion = pdb.guardar_propuesta_ponente(doc, corrida_id, evento)
            if accion == "nuevo":
                resultado["nuevos"] += 1
            elif accion == "actualizado":
                resultado["actualizados"] += 1
            else:
                resultado["descartados"].append(f"{doc['nombre']}: ya estaba descartado o sin novedad")
        pdb.cerrar_corrida_ponentes(corrida_id, "completada", resultado, uso, ia.costo(cfg, uso))
    except Exception as exc:
        logger.exception("Ponentes: la búsqueda falló")
        pdb.cerrar_corrida_ponentes(corrida_id, "fallida", {**resultado, "error": str(exc)[:500]}, uso, ia.costo(cfg, uso))
    return {**resultado, "uso": uso, "corrida_id": corrida_id, "costo": ia.costo(cfg, uso)}


def ejecutar_si_toca() -> None:
    """Solo la búsqueda semanal cuenta para la frecuencia; las de un evento son aparte."""
    est = pdb.get_ponentes_estado()
    semanal = {"ultima_exitosa": est.get("ultima_exitosa"), "ultimo_intento": est.get("ultimo_intento_semanal")}
    if alianzas_ia.toca(catalogos.get_ponentes_config(), semanal):
        ejecutar("programada")
