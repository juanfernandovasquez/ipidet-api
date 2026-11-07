"""
Ponentes: contexto para la investigación y validación de lo que Claude Code encuentra.

La búsqueda la hace Claude Code en la computadora del equipo (/buscar-ponentes), con la
suscripción y sin la API. Prioriza a profesionales de otras instituciones.

Son personas, así que las reglas son estrictas: solo información profesional pública,
cada ficha con al menos una fuente que se pudo abrir y lo nombra, y nunca correos ni
teléfonos. LinkedIn y el perfil institucional se guardan solo si se pueden comprobar.
"""
import re

from config import catalogos
import webapp.db as pdb
from webapp import investigacion_ia as ia

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
_TELEFONO = re.compile(r"\+?\d[\d\s().-]{7,}\d")


def _sin_contacto(valor: str) -> str:
    """Quita correos y números de teléfono aunque la instrucción ya lo pida."""
    valor = _EMAIL.sub("[dato omitido]", valor)
    return _TELEFONO.sub(lambda m: "[dato omitido]" if sum(c.isdigit() for c in m.group()) >= 9 else m.group(), valor)


def buscar_evento(referencia: str) -> dict | None:
    """Acepta el ID del evento o parte de su título."""
    referencia = (referencia or "").strip()
    if not referencia:
        return None
    if re.fullmatch(r"[0-9a-f]{24}", referencia):
        return pdb.get_evento(referencia)
    candidatos = pdb.eventos_col.find({"titulo": {"$regex": re.escape(referencia), "$options": "i"},
                                       "estado": {"$ne": "cancelado"}}, {"_id": 1}).sort("fecha", -1).limit(1)
    primero = next(iter(candidatos), None)
    return pdb.get_evento(str(primero["_id"])) if primero else None


def contexto(evento: dict | None = None) -> dict:
    cfg = catalogos.get_ponentes_config()
    datos = pdb.get_contexto_ponentes()
    salida = {
        "fecha_hoy": pdb._hoy_lima(),
        "asociacion": catalogos.get_alianzas_config().get("perfil", ""),
        "publico": cfg.get("publico", ""),
        "temas_prioritarios": cfg.get("temas", ""),
        "maximo_ponentes": cfg.get("propuestas_por_corrida", 5),
        "ambitos": list(catalogos.AMBITOS_ALIANZA),
        "eventos_proximos": [f"{e['fecha']}: {e['titulo']}" for e in datos["proximos"]],
        "asistencia_de_eventos_anteriores": [f"{e['titulo']}: {e['asistentes']} de {e['inscritos']} inscritos asistieron"
                                             for e in datos["asistencia"]],
        "instituciones_en_estudio_como_posibles_aliadas": datos["instituciones"],
        "ya_registrados": datos["registrados"],
        "descartados_no_proponer": datos["descartados"],
    }
    if evento:
        salida["evento"] = {"id": evento["_id"], "titulo": evento["titulo"], "fecha": evento.get("fecha", ""),
                            "descripcion": ia.texto(evento.get("descripcion"), 1500)}
    return salida


def preparar(item: dict, lector: ia.Lector) -> tuple[dict | None, str]:
    nombre = ia.texto(item.get("nombre"), 150)
    if not nombre:
        return None, "sin nombre"
    if not ia.texto(item.get("por_que_relevante")):
        return None, f"{nombre}: falta explicar por qué interesa a nuestro público"

    palabras = [p for p in ia.normalizar(nombre).split() if len(p) > 2]

    def menciona(pagina: str) -> bool:
        # Al menos dos partes del nombre: un apellido suelto se repite entre personas.
        return len(palabras) >= 2 and sum(f" {p} " in pagina for p in palabras) >= min(2, len(palabras))

    fuentes, paginas, motivos = ia.fuentes_verificadas(item.get("fuentes"), lector, menciona)
    if not fuentes:
        return None, f"{nombre}: ninguna fuente se pudo verificar ({motivos or 'sin fuentes'})"

    linkedin = ""
    try:
        candidato = pdb.normalizar_linkedin(item.get("linkedin") or "")
    except ValueError:
        candidato = ""
    if candidato:
        # LinkedIn no deja leer perfiles sin sesión: vale si una fuente verificada enlaza ese perfil.
        ruta = ia.url_clave(candidato).split("linkedin.com", 1)[-1].lower()
        if any(ruta and ruta in html.lower() for html in paginas):
            linkedin = candidato

    perfil_url = str(item.get("perfil_url") or "").strip()
    if perfil_url:
        html, fallo = lector.leer(perfil_url)
        if fallo or not menciona(ia.normalizar(re.sub(r"<[^>]+>", " ", html))):
            perfil_url = ""

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
        "linkedin": linkedin,
        "perfil_url": perfil_url,
        "es_funcionario_publico": bool(item.get("es_funcionario_publico")),
        "consideraciones": limpio("consideraciones"),
        "relevancia": ia.puntaje(item.get("relevancia")),
        "prestigio": ia.puntaje(item.get("prestigio")),
        "viabilidad": ia.puntaje(item.get("viabilidad")),
        "fuentes": fuentes,
        "novedad": limpio("novedad"),
        "relacion_evento": limpio("relacion_evento"),
    }, ""


def importar(items: list, evento: dict | None = None, guardar: bool = True) -> dict:
    resultado = {"nuevos": 0, "actualizados": 0, "descartados": [], "aceptados": []}
    lector = ia.Lector()
    extra = {"evento_id": evento["_id"], "evento_titulo": evento["titulo"]} if evento else {}
    corrida_id = pdb.registrar_corrida("ponentes", extra) if guardar else ""
    try:
        for item in items:
            doc, motivo = preparar(item, lector)
            if not doc:
                resultado["descartados"].append(motivo)
                continue
            resultado["aceptados"].append(doc["nombre"])
            if not guardar:
                continue
            accion = pdb.guardar_propuesta_ponente(doc, corrida_id, evento)
            if accion == "nuevo":
                resultado["nuevos"] += 1
            elif accion == "actualizado":
                resultado["actualizados"] += 1
            else:
                resultado["descartados"].append(f"{doc['nombre']}: ya estaba registrado y no trae novedad, o fue descartado")
    finally:
        lector.cerrar()
        if guardar:
            pdb.cerrar_corrida("ponentes", corrida_id, resultado)
    return resultado
