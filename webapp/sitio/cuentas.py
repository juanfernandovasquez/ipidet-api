"""Cuentas del sitio: sin contraseña, con un código de 6 dígitos al correo.

- La sesión es propia (cookie `ipidet_web`, colección `web_sesiones`) y NUNCA usa `session["user_email"]`:
  esa llave abre el panel del equipo.
- Socio = el correo está (no inhabilitado) en la ficha de un único socio con estado «activo». Se calcula
  en cada visita: si deja de estar activo o le quitan el correo, pierde el acceso completo al instante.
"""
import hashlib
import hmac
import re
import secrets
from datetime import timedelta, timezone

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from config.mongo_client import db
from config.settings import SECRET_KEY
from webapp.sitio import config

cuentas_col = db.web_cuentas
codigos_col = db.web_codigos
sesiones_col = db.web_sesiones
limites_col = db.web_limites

COOKIE = "ipidet_web"
DIAS_SESION = 30
MINUTOS_CODIGO = 10
MAX_INTENTOS = 5


def asegurar_indices() -> None:
    for col, clave, opciones in (
        (cuentas_col, "email", {"unique": True}),
        (cuentas_col, "created_at", {}),
        (sesiones_col, "token", {"unique": True}),
        (sesiones_col, "cuenta_id", {}),
        (sesiones_col, "expira", {"expireAfterSeconds": 0}),
        (codigos_col, "expira", {"expireAfterSeconds": 0}),
        (limites_col, "expira", {"expireAfterSeconds": 0}),
    ):
        try:
            col.create_index(clave, **opciones)
        except Exception as e:  # noqa: BLE001
            print(f"[sitio] Aviso: no se pudo crear el índice {col.name}.{clave}: {e}")


def _hash(*partes: str) -> str:
    return hmac.new(SECRET_KEY.encode(), "|".join(partes).encode(), hashlib.sha256).hexdigest()


def permitido(clave: str, maximo: int, segundos: int) -> bool:
    """Límite por ventana fija, en la base para que no se reinicie con cada despliegue."""
    ahora = config.ahora()
    ventana = int(ahora.timestamp()) // segundos
    _id = f"{_hash('limite', clave)}:{segundos}:{ventana}"
    cambio = {"$inc": {"n": 1}, "$setOnInsert": {"expira": ahora + timedelta(seconds=segundos)}}
    try:
        doc = limites_col.find_one_and_update({"_id": _id}, cambio, upsert=True, return_document=True)
    except DuplicateKeyError:
        doc = limites_col.find_one_and_update({"_id": _id}, cambio, return_document=True)
    return (doc or {}).get("n", 0) <= maximo


# ── Código de ingreso ─────────────────────────────────────────────────────────

def crear_codigo(email: str) -> tuple[str, str]:
    """(reto, código). El código no se guarda: solo su HMAC."""
    reto, codigo = secrets.token_urlsafe(24), f"{secrets.randbelow(1_000_000):06d}"
    codigos_col.insert_one({"_id": reto, "email": email, "hash": _hash(reto, codigo), "intentos": 0,
                            "expira": config.ahora() + timedelta(minutes=MINUTOS_CODIGO)})
    return reto, codigo


def verificar_codigo(reto: str, codigo: str) -> str | None:
    """Correo verificado, o None. Cada intento cuenta antes de comparar y el código sirve una vez."""
    doc = codigos_col.find_one_and_update(
        {"_id": str(reto or ""), "intentos": {"$lt": MAX_INTENTOS}, "expira": {"$gt": config.ahora()}},
        {"$inc": {"intentos": 1}})
    if not doc or not hmac.compare_digest(doc["hash"], _hash(doc["_id"], str(codigo or ""))):
        return None
    if not codigos_col.delete_one({"_id": doc["_id"]}).deleted_count:
        return None
    return doc["email"]


def cuenta_de_email(email: str) -> dict:
    """La cuenta del correo ya verificado; la crea si no existe (perfil por completar)."""
    ahora = config.ahora()
    filtro = {"email": email}
    cambio = {"$set": {"ultimo_ingreso": ahora},
              "$setOnInsert": {"nombre": "", "apellido": "", "celular": "", "empresa": "", "ciudad": "",
                               "perfil_completo": False, "acepta_avisos": False, "bloqueada": False,
                               "created_at": ahora}}
    try:
        return cuentas_col.find_one_and_update(filtro, cambio, upsert=True, return_document=True)
    except DuplicateKeyError:
        return cuentas_col.find_one_and_update(filtro, cambio, return_document=True)


# ── Sesión ────────────────────────────────────────────────────────────────────

def abrir_sesion(cuenta_id) -> str:
    token = secrets.token_urlsafe(32)
    sesiones_col.insert_one({"token": _hash("sesion", token), "cuenta_id": str(cuenta_id),
                             "expira": config.ahora() + timedelta(days=DIAS_SESION)})
    return token


def cerrar_sesion(token: str) -> None:
    if token:
        sesiones_col.delete_one({"token": _hash("sesion", token)})


def cerrar_todas(cuenta_id: str) -> None:
    sesiones_col.delete_many({"cuenta_id": str(cuenta_id)})


# ── Quién es ──────────────────────────────────────────────────────────────────

def socio_de(email: str) -> dict | None:
    """El socio activo dueño del correo. Con el mismo correo en dos fichas activas no se adivina: None."""
    fichas = activas_con(email)
    return fichas[0] if len(fichas) == 1 else None


def activas_con(email: str) -> list[dict]:
    if not email:
        return []
    return list(db.members.find(
        {"estado": "activo",
         "emails": {"$elemMatch": {"email": {"$regex": rf"^\s*{re.escape(email)}\s*$", "$options": "i"},
                                   "estado": {"$ne": "inhabilitado"},
                                   # Lo que llegó por el portal de WordPress no prueba identidad.
                                   "origen": {"$ne": "portal"}}}},
        {"member_id": 1, "apellidos": 1, "nombres": 1, "titulo": 1, "tipo_socio": 1, "dni": 1,
         "centro_trabajo": 1, "celular": 1, "ubicacion": 1, "emails": 1, "fecha_ingreso": 1},
    ).limit(3))


def persona(request) -> dict | None:
    """Quien visita, o None. Es lo único que deciden los permisos del sitio."""
    if hasattr(request.state, "persona"):
        return request.state.persona
    request.state.persona = _persona(request.cookies.get(COOKIE) or "")
    return request.state.persona


def _persona(token: str) -> dict | None:
    if not token or len(token) > 100:
        return None
    ses = sesiones_col.find_one({"token": _hash("sesion", token)})
    if not ses:
        return None
    if ses["expira"].replace(tzinfo=timezone.utc) <= config.ahora() or not ObjectId.is_valid(ses["cuenta_id"]):
        return None
    cuenta = cuentas_col.find_one({"_id": ObjectId(ses["cuenta_id"])})
    if not cuenta or cuenta.get("bloqueada"):
        return None
    return de_cuenta(cuenta)


def de_cuenta(cuenta: dict) -> dict:
    socio = socio_de(cuenta["email"])
    nombre, apellido = cuenta.get("nombre") or "", cuenta.get("apellido") or ""
    if socio and not (nombre or apellido):
        nombre, apellido = (socio.get("nombres") or "").title(), (socio.get("apellidos") or "").title()
    return {
        "id": str(cuenta["_id"]),
        "email": cuenta["email"],
        "nombre": nombre, "apellido": apellido,
        "nombre_completo": f"{nombre} {apellido}".strip() or cuenta["email"],
        "celular": cuenta.get("celular") or "", "empresa": cuenta.get("empresa") or "",
        "ciudad": cuenta.get("ciudad") or "",
        "acepta_avisos": bool(cuenta.get("acepta_avisos")),
        "registrada": bool(cuenta.get("perfil_completo")),
        # Socio completo solo con el perfil terminado (aceptó términos y privacidad).
        "socio": socio if cuenta.get("perfil_completo") else None,
        "socio_detectado": socio,
        "member_id": socio["member_id"] if socio and cuenta.get("perfil_completo") else None,
    }
