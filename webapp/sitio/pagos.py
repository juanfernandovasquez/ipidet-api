"""Medios de pago de la web, configurables en /sitio/metodos.

Hoy todos son manuales: la persona paga fuera de la web, informa su operación y el equipo confirma.

PASARELAS es la estructura para cobrar con tarjeta más adelante (decisión del usuario, 2026-11-22:
Niubiz se integra después). Al integrarla:
  1. implementar la creación del cobro y la verificación firmada del servidor de la pasarela;
  2. guardar su referencia en `web_ordenes.pasarela {proveedor, referencia}`;
  3. confirmar SIEMPRE con `ordenes.confirmar()`, el único camino que toca Cobranzas;
  4. recién entonces poner `disponible: True`.
La web nunca pide ni guarda números de tarjeta.
"""
import re
import uuid

from config import catalogos
from config.mongo_client import db

TIPOS = {
    "deposito":   "Depósito o transferencia",
    "billetera":  "Billetera digital (Yape, Plin…)",
    "presencial": "Pago presencial",
    "otro":       "Otro medio manual",
}
PASARELAS = {
    "niubiz": {"nombre": "Niubiz (tarjetas)", "disponible": False, "nota": "Estructura lista. Falta la integración."},
}
MONEDAS = {"PEN": "Soles", "USD": "Dólares"}
PARA = {"eventos": "Eventos", "cuotas": "Cuotas de socios"}
_ID = "sitio_metodos_pago"


def todos() -> list[dict]:
    return catalogos._cached(_ID, lambda: (db.config.find_one({"_id": _ID}) or {}).get("valores") or [])


def disponibles(para: str) -> list[dict]:
    """Medios activos para «eventos» o «cuotas». Los cobros de la web son en soles."""
    return [m for m in todos()
            if m.get("activo") and m.get("tipo") in TIPOS and m.get("moneda") == "PEN" and para in (m.get("para") or [])]


def por_id(metodo_id: str) -> dict | None:
    return next((m for m in todos() if m.get("id") == metodo_id), None)


def validar(datos: dict) -> dict:
    """Devuelve el medio limpio o lanza ValueError."""
    t = lambda k, n=120: re.sub(r"\s+", " ", str(datos.get(k) or "")).strip()[:n]  # noqa: E731
    m = {
        "tipo": t("tipo"), "nombre": t("nombre", 80), "banco": t("banco", 80), "titular": t("titular", 160),
        "moneda": t("moneda") or "PEN", "numero": t("numero", 40), "cci": re.sub(r"[\s-]", "", t("cci", 40)),
        "instrucciones": str(datos.get("instrucciones") or "").strip()[:600],
        "para": [p for p in datos.get("para") or [] if p in PARA],
        "activo": bool(datos.get("activo")),
    }
    if m["tipo"] not in TIPOS:
        raise ValueError("Elige un tipo de medio de pago disponible.")
    if not m["nombre"]:
        raise ValueError("Falta el nombre que verá la persona.")
    if m["moneda"] not in MONEDAS:
        raise ValueError("Moneda no válida.")
    if m["numero"] and not re.fullmatch(r"[0-9 +-]{5,40}", m["numero"]):
        raise ValueError("El número solo puede tener dígitos, espacios y guiones.")
    if m["cci"] and not re.fullmatch(r"\d{20}", m["cci"]):
        raise ValueError("El CCI debe tener 20 dígitos.")
    if m["tipo"] == "deposito" and not ((m["numero"] or m["cci"]) and m["banco"] and m["titular"]):
        raise ValueError("Un depósito necesita banco, titular y número de cuenta o CCI.")
    if m["tipo"] == "billetera" and not (m["numero"] and m["titular"]):
        raise ValueError("Una billetera necesita número y titular.")
    if not m["para"]:
        raise ValueError("Marca si sirve para eventos, para cuotas o para ambos.")
    return m


def guardar(datos: dict, metodo_id: str = "") -> dict:
    m = validar(datos)
    lista = [dict(x) for x in todos()]
    pos = next((n for n, x in enumerate(lista) if metodo_id and x.get("id") == metodo_id), None)
    if metodo_id and pos is None:
        raise ValueError("Ese medio de pago ya no existe.")
    m["id"] = metodo_id or uuid.uuid4().hex[:10]
    if pos is None:
        lista.append(m)
    else:
        lista[pos] = m
    db.config.replace_one({"_id": _ID}, {"_id": _ID, "valores": lista}, upsert=True)
    catalogos.invalidar()
    return m


def eliminar(metodo_id: str) -> None:
    db.config.update_one({"_id": _ID}, {"$pull": {"valores": {"id": metodo_id}}})
    catalogos.invalidar()
