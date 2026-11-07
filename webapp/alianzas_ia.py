"""
Alianzas: contexto para la investigación y validación de lo que Claude Code encuentra.

La búsqueda la hace Claude Code en la computadora del equipo (/buscar-alianzas), con la
suscripción y sin la API. `investigacion.py` le entrega el contexto y luego importa el
resultado pasando por `preparar`: una institución se guarda solo si al menos una de
sus fuentes se pudo abrir y la menciona.
"""
from config import catalogos
import webapp.db as pdb
from webapp import investigacion_ia as ia


def contexto() -> dict:
    cfg = catalogos.get_alianzas_config()
    existentes = pdb.get_alianzas_nombres()
    return {
        "fecha_hoy": pdb._hoy_lima(),
        "asociacion": cfg.get("perfil", ""),
        "sitio_web": catalogos.get_organizacion().get("web", ""),
        "criterios": cfg.get("criterios", ""),
        "maximo_instituciones": cfg.get("propuestas_por_corrida", 5),
        "tipos_de_institucion": catalogos.get_catalogo("tipos_institucion"),
        "ambitos": list(catalogos.AMBITOS_ALIANZA),
        "ya_registradas": [e["nombre"] for e in existentes if e.get("estado") != "descartada"],
        "descartadas_no_proponer": [e["nombre"] for e in existentes if e.get("estado") == "descartada"],
    }


def preparar(item: dict, tipos_validos: set, lector: ia.Lector) -> tuple[dict | None, str]:
    """Valida una propuesta. Devuelve (documento, "") o (None, motivo del descarte)."""
    nombre = ia.texto(item.get("nombre"), 200)
    if not nombre:
        return None, "sin nombre"
    if not all(ia.texto(item.get(c)) for c in ("por_que_importante", "por_que_estrategica")):
        return None, f"{nombre}: falta explicar por qué es importante y por qué estratégica"

    nombre_n = ia.normalizar(nombre)
    siglas_n = ia.normalizar(item.get("siglas") or "")
    palabras = [p for p in nombre_n.split() if len(p) > 3]

    def menciona(pagina: str) -> bool:
        if nombre_n in pagina or (len(siglas_n.strip()) >= 3 and siglas_n in pagina):
            return True
        return bool(palabras) and sum(f" {p} " in pagina for p in palabras) >= max(2, round(len(palabras) * 0.7))

    fuentes, _, motivos = ia.fuentes_verificadas(item.get("fuentes"), lector, menciona)
    if not fuentes:
        return None, f"{nombre}: ninguna fuente se pudo verificar ({motivos or 'sin fuentes'})"

    web = str(item.get("web") or "").strip()
    host = ia.dominio(web)
    if not host or ia.es_red_social(host) or lector.leer(web)[1]:
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


def importar(items: list, guardar: bool = True) -> dict:
    tipos_validos = {t["valor"] for t in catalogos.get_catalogo("tipos_institucion")}
    resultado = {"nuevas": 0, "actualizadas": 0, "descartadas": [], "aceptadas": []}
    lector = ia.Lector()
    corrida_id = pdb.registrar_corrida("alianzas") if guardar else ""
    try:
        for item in items:
            doc, motivo = preparar(item, tipos_validos, lector)
            if not doc:
                resultado["descartadas"].append(motivo)
                continue
            resultado["aceptadas"].append(doc["nombre"])
            if not guardar:
                continue
            accion = pdb.guardar_propuesta_alianza(doc, corrida_id)
            if accion == "nueva":
                resultado["nuevas"] += 1
            elif accion == "actualizada":
                resultado["actualizadas"] += 1
            else:
                resultado["descartadas"].append(f"{doc['nombre']}: ya estaba registrada y no trae novedad, o fue descartada")
    finally:
        lector.cerrar()
        if guardar:
            pdb.cerrar_corrida("alianzas", corrida_id, resultado)
    return resultado
