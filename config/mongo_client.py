import atexit
import certifi
from pymongo import MongoClient
from config.settings import MONGODB_URI, DB_NAME

_client = MongoClient(
    MONGODB_URI,
    tlsCAFile=certifi.where(),
    maxPoolSize=10,        # max 10 conexiones simultaneas por proceso
    minPoolSize=0,         # no abrir conexiones al arrancar
    maxIdleTimeMS=30_000,  # cerrar conexiones inactivas a los 30 s
    serverSelectionTimeoutMS=5_000,
    connectTimeoutMS=5_000,
    socketTimeoutMS=30_000,
)
db = _client[DB_NAME]

# Cerrar el pool limpiamente cuando el proceso termina,
# para no dejar conexiones "colgadas" en Atlas durante redeploys.
atexit.register(_client.close)
