"""Prepara la cola de correos para leer varias cuentas (idempotente).

Los correos anteriores llegaron a la cuenta principal (GMAIL_ADDRESS). Se les asigna esa
`cuenta` y la clave pasa a ser el Message-ID («mid:…»), que es el mismo en las dos cuentas:
así un correo enviado a ipidet@ y administracion@ a la vez se atiende una sola vez.

    python migrate_correos_cuentas.py
"""
from pymongo.errors import DuplicateKeyError

from config.settings import GMAIL_ADDRESS
from webapp import agente_db


def main():
    principal = (GMAIL_ADDRESS or "").strip().lower()
    if not principal:
        raise SystemExit("Falta GMAIL_ADDRESS")
    agente_db.asegurar_indices()
    cuentas = claves = 0
    for doc in agente_db.correos_col.find({}, {"cuenta": 1, "clave": 1, "message_id": 1, "gm_msgid": 1, "uid": 1}):
        cambios = {}
        if not doc.get("cuenta"):
            cambios["cuenta"] = principal
        nueva = agente_db.clave_correo({**doc, "cuenta": doc.get("cuenta") or principal})
        if doc.get("clave") != nueva:
            cambios["clave"] = nueva
        if not cambios:
            continue
        try:
            agente_db.correos_col.update_one({"_id": doc["_id"]}, {"$set": cambios})
        except DuplicateKeyError:
            cambios.pop("clave")
            if cambios:
                agente_db.correos_col.update_one({"_id": doc["_id"]}, {"$set": cambios})
        cuentas += "cuenta" in cambios
        claves += "clave" in cambios
    print(f"Cuenta asignada a {cuentas} correos; clave actualizada en {claves}.")


if __name__ == "__main__":
    main()
