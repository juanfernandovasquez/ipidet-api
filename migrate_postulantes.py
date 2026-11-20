"""Pone al día Postulantes y Público con lo que ya existía (idempotente: se puede correr varias veces).

1. Personas de Público con estado «postulante» o con la antigua `postulacion` y sin postulación
   abierta → se les abre una (origen «migracion»), con el tipo y la nota que tenían.
2. Correos ya analizados de consultas (evento, admisión, consulta general) de personas que no son
   socias y no están en Público → se registran en Público; los de admisión, también en Postulantes.
   Es lo mismo que el agente hace ahora con cada correo nuevo.

    python migrate_postulantes.py --probar     # solo cuenta lo que haría, no escribe
    python migrate_postulantes.py
"""
import sys

from config import catalogos
from webapp import agente_correo, agente_db
from webapp import db as pdb


def main(probar: bool) -> None:
    if not probar:
        pdb.postulantes_col.create_index([("prospecto_id", 1), ("etapa", 1)])
        pdb.postulantes_col.create_index("fecha_seguimiento")

    # 1. Postulaciones que vivían como un campo en Público
    abiertas = omitidas = 0
    for p in pdb.publico_col.find({"$or": [{"estado": "postulante"}, {"postulacion": {"$nin": [None, {}]}}]},
                                  {"estado": 1, "postulacion": 1, "email": 1}):
        pid = str(p["_id"])
        if p.get("estado") == "convertido" or pdb.postulantes_col.find_one({"prospecto_id": pid}):
            continue
        anterior = p.get("postulacion") or {}
        if probar:
            abiertas += 1
            continue
        try:
            pdb.crear_postulacion(pid, origen="migracion", usuario="migración",
                                  tipo_socio=anterior.get("tipo") or "", nota=anterior.get("notas") or "",
                                  detalle=f"Postulación registrada en Público el {anterior.get('fecha') or 's/f'}")
            abiertas += 1
        except ValueError as e:
            omitidas += 1
            print(f"  · {p.get('email')}: {e}")
    print(f"Postulaciones abiertas desde Público: {abiertas}" + (f" (omitidas: {omitidas})" if omitidas else ""))

    # 2. Consultas que llegaron por correo antes de que el agente registrara a las personas
    cfg = {**catalogos.get_agente_config(), "matricular_publico": True}
    q = {"categoria": {"$in": list(catalogos.CATEGORIAS_PUBLICO_AUTO)}, "member_id": None,
         "posible_member_id": None, "prospecto_id": None, "filtrado_sin_ia": {"$ne": True}}
    correos = list(agente_db.correos_col.find(q, {"_id": 1, "de_email": 1, "categoria": 1}).sort("fecha", 1))
    personas = {c.get("de_email") for c in correos if c.get("de_email")}
    admision = {c.get("de_email") for c in correos if c.get("categoria") == "admision"}
    if probar:
        print(f"Correos de consultas sin ficha en Público: {len(correos)} de {len(personas)} personas "
              f"({len(admision)} quieren asociarse)")
        return
    nuevas = postulaciones = 0
    for c in correos:
        res = agente_correo.matricular(str(c["_id"]), cfg)
        nuevas += bool(res.get("publico_creado"))
        postulaciones += bool(res.get("postulacion_creada"))
    print(f"Registradas en Público: {nuevas} personas; postulaciones abiertas por correo: {postulaciones}")


if __name__ == "__main__":
    main(probar="--probar" in sys.argv)
