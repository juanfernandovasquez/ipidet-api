import sys
import os

# Garantiza que el directorio raíz del proyecto esté en sys.path
# tanto en el proceso principal como en los procesos hijos de uvicorn
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "webapp.app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=[ROOT],
    )
