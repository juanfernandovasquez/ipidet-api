import sys
import os

# Asegura que el directorio raíz esté en el path
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Carga variables de entorno desde .env
from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

# Importa la app FastAPI (ASGI) y la envuelve como WSGI para Passenger
from webapp.app import app as _asgi_app
from a2wsgi import ASGIMiddleware

application = ASGIMiddleware(_asgi_app)
