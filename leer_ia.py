"""
Lee las tareas IA pendientes desde MongoDB y las imprime en la terminal.
Uso: python leer_ia.py
"""
import os
import sys
from datetime import datetime, timezone

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from pymongo import MongoClient
    import certifi
except ImportError:
    print("Error: instala pymongo y certifi  →  pip install pymongo certifi")
    sys.exit(1)

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
DB_NAME     = os.getenv("DB_NAME", "ipidet_agent")

client = MongoClient(MONGODB_URI, tlsCAFile=certifi.where())
db     = client[DB_NAME]

tareas = list(db.ia_tareas.find({"estado": {"$ne": "completado"}}).sort("created_at", 1))

if not tareas:
    print("✓ No hay tareas IA pendientes.")
    sys.exit(0)

print(f"\n{'='*60}")
print(f"  TAREAS IA PENDIENTES  ({len(tareas)} tarea{'s' if len(tareas) != 1 else ''})")
print(f"{'='*60}\n")

for i, t in enumerate(tareas, 1):
    estado = t.get("estado", "pendiente").upper()
    fecha  = t.get("created_at")
    if hasattr(fecha, "strftime"):
        fecha_str = fecha.strftime("%Y-%m-%d")
    else:
        fecha_str = str(fecha)[:10] if fecha else "—"

    badge = {"PENDIENTE": "[*]", "EN_PROCESO": "[>]"}.get(estado, "[?]")
    print(f"[{i}] {badge} {estado}  ({fecha_str})")
    print(f"     ID: {t['_id']}")
    print()
    for linea in t.get("texto", "").splitlines():
        print(f"     {linea}")
    print()
    print("-" * 60)
    print()
