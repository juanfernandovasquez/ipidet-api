"""Configuración y contenido del sitio (R10): todo se edita en /sitio.

`inicial.json` solo da el valor de una clave mientras el equipo no la haya guardado en
config.sitio_config, igual que los catálogos con «inicial».
"""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from config import catalogos
from config.mongo_client import db

_config = db.config
_INICIAL = json.loads((Path(__file__).parent / "inicial.json").read_text(encoding="utf-8"))
LIMA = timezone(timedelta(hours=-5))

# Formulario de /sitio/contenido: clave → (etiqueta, formato, columnas si es una lista)
#   texto · parrafo · url · numero · lista (una por línea) · tabla (columnas separadas con «|»)
GRUPOS: list[tuple[str, dict[str, tuple[str, str, int]]]] = [
    ("Contacto", {
        "direccion":   ("Dirección", "texto", 0),
        "mapa_url":    ("Enlace al mapa", "url", 0),
        "telefonos":   ("Teléfonos (uno por línea)", "lista", 0),
        "whatsapp":    ("WhatsApp", "texto", 0),
        "email":       ("Correo de la web", "texto", 0),
        "facebook":    ("Facebook (si no está en IPIDET y consejo)", "url", 0),
        "linkedin":    ("LinkedIn (si no está en IPIDET y consejo)", "url", 0),
        "youtube":     ("YouTube (si no está en IPIDET y consejo)", "url", 0),
        "biblioteca_url": ("Biblioteca de socios", "url", 0),
        "url_base":    ("Dirección de la web (para los enlaces de los correos)", "url", 0),
    }),
    ("Inicio y Nosotros", {
        "lema":             ("Frase de la portada", "parrafo", 0),
        "descripcion":      ("Descripción corta", "parrafo", 0),
        "bienvenida_cita":  ("Cita de bienvenida", "parrafo", 0),
        "bienvenida_texto": ("Texto de bienvenida", "parrafo", 0),
        "bienvenida_foto":  ("Foto de bienvenida", "url", 0),
        "quienes_somos":    ("Quiénes somos", "parrafo", 0),
        "cifras":           ("Cifras: valor | texto", "tabla", 2),
        "objetivos":        ("Objetivos: título | texto", "tabla", 2),
        "valores":          ("Valores (uno por línea)", "lista", 0),
        "comunicados":      ("Comunicados: título | fecha | imagen", "tabla", 3),
    }),
    ("Consejo e historia", {
        "consejo_periodo":  ("Período del consejo", "texto", 0),
        "consejo_respaldo": ("Consejo mientras no se cargue en «IPIDET y consejo»: nombre | cargo | foto", "tabla", 3),
        "expresidentes":    ("Expresidentes: período | nombre", "tabla", 2),
        "honorarios":       ("Socios honorarios (uno por línea)", "lista", 0),
    }),
    ("Admisión", {
        "cuota_notas": ("Notas de la cuota (una por línea)", "lista", 0),
        "pasos":       ("Pasos: título | texto", "tabla", 2),
        "documentos":  ("Documentos: nombre | enlace", "tabla", 2),
        "preguntas":   ("Preguntas: pregunta | respuesta", "tabla", 2),
    }),
    ("Textos legales", {
        "privacidad": ("Política de privacidad (una línea por párrafo; «## » es subtítulo)", "lista", 0),
        "terminos":   ("Términos y condiciones (igual)", "lista", 0),
    }),
]
CAMPOS = {k: v for _, campos in GRUPOS for k, v in campos.items()}


def hoy() -> str:
    return datetime.now(LIMA).strftime("%Y-%m-%d")


def id_valido(valor) -> bool:
    """Solo la forma canónica (24 hex en minúsculas). ObjectId también acepta mayúsculas y 12 caracteres:
    el mismo documento tendría varias claves y se saltarían cupos y vínculos."""
    return bool(re.fullmatch(r"[0-9a-f]{24}", str(valor or "")))


def ahora() -> datetime:
    return datetime.now(timezone.utc)


def cargar() -> dict:
    def _leer():
        doc = _config.find_one({"_id": "sitio_config"}) or {}
        doc.pop("_id", None)
        return {**_INICIAL, **doc}
    return catalogos._cached("sitio_config", _leer)


def guardar(campos: dict) -> None:
    limpio = {k: v for k, v in campos.items() if k in _INICIAL}
    if limpio:
        _config.update_one({"_id": "sitio_config"}, {"$set": limpio}, upsert=True)
    catalogos.invalidar()


def publicada() -> bool:
    return bool(cargar().get("publicada"))


def url_segura(valor: str, imagen: bool = False) -> str:
    """https, una ruta de este sitio o, para imágenes, data:image. Cualquier otra cosa queda vacía."""
    v = str(valor or "").strip()
    if v.startswith("/") and not v.startswith("//") and "\\" not in v:
        return v
    if imagen and re.match(r"data:image/(png|jpeg|webp|gif);base64,", v):
        return v
    try:
        partes = urlsplit(v)
    except ValueError:
        return ""
    return v if partes.scheme in ("https", "http") and partes.netloc else ""


def a_texto(valor, formato: str) -> str:
    if formato == "lista":
        return "\n".join(str(x) for x in valor or [])
    if formato == "tabla":
        return "\n".join(" | ".join(str(c) for c in fila) for fila in valor or [])
    return str(valor if valor is not None else "")


def desde_texto(clave: str, texto: str):
    """Valida un campo del formulario. Lanza ValueError con un mensaje para el equipo."""
    etiqueta, formato, columnas = CAMPOS[clave]
    texto = (texto or "").replace("\r", "").strip()
    lineas = [x.strip() for x in texto.split("\n") if x.strip()]
    if formato == "lista":
        return lineas[:200]
    if formato == "tabla":
        filas = []
        for n, linea in enumerate(lineas[:100], 1):
            partes = [p.strip() for p in linea.split("|")]
            if len(partes) != columnas:
                raise ValueError(f"{etiqueta}: la línea {n} debe tener {columnas} partes separadas con «|».")
            filas.append(partes)
        return filas
    if formato == "url" and texto and not url_segura(texto, imagen=True):
        raise ValueError(f"{etiqueta}: usa un enlace que empiece con https://")
    return texto[:2000]


def contacto() -> dict:
    """Lo cargado en «IPIDET y consejo» manda en las redes; lo demás es propio de la web."""
    cfg, org = cargar(), catalogos.get_organizacion()
    res = {k: cfg.get(k, "") for k in ("direccion", "mapa_url", "telefonos", "whatsapp", "email", "biblioteca_url")}
    for red in ("facebook", "linkedin", "youtube", "instagram"):
        res[red] = url_segura(org.get(red) or cfg.get(red, ""))
    numero = re.sub(r"\D", "", res["whatsapp"])
    res["whatsapp_url"] = f"https://wa.me/{numero}" if numero else ""
    return res


def cuotas() -> dict:
    """Precio de la cuota del período de trabajo: {cuota_anual: 780, cuota_provincia: 350}."""
    res: dict = {}
    for p in db.productos.find({"activo": True, "tipo": {"$in": list(catalogos.TIPOS_CUOTA)},
                                "periodo": catalogos.get_periodo_actual()}, {"tipo": 1, "precio": 1}):
        if p.get("precio"):
            res.setdefault(p["tipo"], p["precio"])
    return res


def soles(valor) -> str:
    try:
        n = float(valor)
    except (TypeError, ValueError):
        return "—"
    return f"S/ {n:,.0f}" if n == int(n) else f"S/ {n:,.2f}"


def marcadores() -> dict[str, str]:
    c, f, q = contacto(), catalogos.get_datos_fiscales(), cuotas()
    return {
        "razon_social": f.get("razon_social") or "IPIDET",
        "ruc": f.get("ruc") or "",
        "direccion": c["direccion"],
        "email": c["email"],
        "whatsapp": c["whatsapp"],
        "cuota_lima": soles(q["cuota_anual"]) if q.get("cuota_anual") else "consulta el monto vigente",
        "cuota_provincia": soles(q["cuota_provincia"]) if q.get("cuota_provincia") else "consulta el monto vigente",
    }


def rellenar(texto: str, valores: dict[str, str]) -> str:
    return re.sub(r"\{([a-z_]+)\}", lambda m: str(valores.get(m.group(1), m.group(0))), str(texto or ""))
