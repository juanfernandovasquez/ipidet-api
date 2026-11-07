"""
investigacion.py — Puente entre Claude Code y la plataforma para buscar ponentes y alianzas.

La búsqueda la hace Claude Code en esta computadora, con la suscripción, a través de los
comandos /buscar-ponentes y /buscar-alianzas (carpeta .claude/skills). No usa la API.

  python investigacion.py contexto ponentes [evento]        # qué buscar: sale de la base de datos
  python investigacion.py contexto alianzas
  python investigacion.py importar ponentes ARCHIVO.json [--evento X] [--probar]
  python investigacion.py importar alianzas ARCHIVO.json [--probar]

`--probar` valida sin guardar. Cada fuente se descarga y debe mencionar a la persona o
institución; lo que no se puede verificar se descarta y se explica por qué.
"""
import argparse
import io
import json
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from webapp import alianzas_ia, ponentes_ia


def _leer_items(ruta: str, clave: str) -> list:
    with open(ruta, encoding="utf-8") as f:
        datos = json.load(f)
    items = datos.get(clave, []) if isinstance(datos, dict) else datos
    if not isinstance(items, list):
        raise SystemExit(f"El archivo debe tener una lista en «{clave}».")
    return items


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("accion", choices=["contexto", "importar"])
    ap.add_argument("tipo", choices=["ponentes", "alianzas"])
    ap.add_argument("valor", nargs="?", default="", help="contexto: evento (ID o parte del título). importar: archivo JSON")
    ap.add_argument("--evento", default="", help="importar ponentes: evento al que se sugieren")
    ap.add_argument("--probar", action="store_true", help="valida sin guardar")
    a = ap.parse_args()

    if a.accion == "contexto":
        if a.tipo == "alianzas":
            salida = alianzas_ia.contexto()
        else:
            evento = ponentes_ia.buscar_evento(a.valor) if a.valor else None
            if a.valor and not evento:
                raise SystemExit(f"No encontré un evento que coincida con «{a.valor}».")
            salida = ponentes_ia.contexto(evento)
        print(json.dumps(salida, ensure_ascii=False, indent=1))
        return

    if not a.valor:
        raise SystemExit("Falta el archivo JSON con los resultados.")
    if a.tipo == "alianzas":
        r = alianzas_ia.importar(_leer_items(a.valor, "instituciones"), guardar=not a.probar)
        aceptadas, descartadas = r["aceptadas"], r["descartadas"]
        resumen = f"{r['nuevas']} nuevas y {r['actualizadas']} actualizadas"
    else:
        evento = ponentes_ia.buscar_evento(a.evento) if a.evento else None
        if a.evento and not evento:
            raise SystemExit(f"No encontré un evento que coincida con «{a.evento}».")
        r = ponentes_ia.importar(_leer_items(a.valor, "ponentes"), evento, guardar=not a.probar)
        aceptadas, descartadas = r["aceptados"], r["descartados"]
        resumen = f"{r['nuevos']} nuevos y {r['actualizados']} actualizados"

    print("PRUEBA: no se guardó nada." if a.probar else f"Guardado en la plataforma: {resumen}.")
    print(f"\nPasan la verificación ({len(aceptadas)}):")
    for n in aceptadas:
        print(f"  - {n}")
    print(f"\nDescartados ({len(descartadas)}):")
    for m in descartadas:
        print(f"  - {m}")


if __name__ == "__main__":
    main()
