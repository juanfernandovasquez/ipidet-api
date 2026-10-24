"""
Zoom Server-to-Server OAuth client.
Token expires every hour; re-fetches automatically.
"""
import base64
import time
from typing import Optional
import httpx
from config.settings import ZOOM_ACCOUNT_ID, ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET

_token: Optional[str] = None
_token_expires_at: float = 0.0

_BASE = "https://api.zoom.us/v2"


def _get_token() -> str:
    global _token, _token_expires_at
    if _token and time.time() < _token_expires_at - 60:
        return _token
    creds = base64.b64encode(f"{ZOOM_CLIENT_ID}:{ZOOM_CLIENT_SECRET}".encode()).decode()
    resp = httpx.post(
        f"https://zoom.us/oauth/token?grant_type=account_credentials&account_id={ZOOM_ACCOUNT_ID}",
        headers={"Authorization": f"Basic {creds}"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    _token = data["access_token"]
    _token_expires_at = time.time() + data.get("expires_in", 3600)
    return _token


def _get(path: str, params: dict | None = None) -> dict:
    token = _get_token()
    resp = httpx.get(
        f"{_BASE}{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params or {},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def list_webinars(user_id: str = "me", page_size: int = 30) -> list[dict]:
    """Lista los webinars del account."""
    data = _get(f"/users/{user_id}/webinars", {"page_size": page_size})
    return data.get("webinars", [])


def get_webinar(webinar_id: str) -> dict:
    """Detalle de un webinar."""
    return _get(f"/webinars/{webinar_id}")


def get_webinar_registrants(webinar_id: str) -> list[dict]:
    """Lista de inscritos de un webinar (status=approved)."""
    results = []
    next_token = None
    while True:
        params: dict = {"page_size": 300}
        if next_token:
            params["next_page_token"] = next_token
        data = _get(f"/webinars/{webinar_id}/registrants", params)
        results.extend(data.get("registrants", []))
        next_token = data.get("next_page_token")
        if not next_token:
            break
    return results


def get_webinar_participants(webinar_id: str) -> list[dict]:
    """Lista de participantes reales (asistencia) via Reports API."""
    results = []
    next_token = None
    while True:
        params: dict = {"page_size": 300}
        if next_token:
            params["next_page_token"] = next_token
        data = _get(f"/report/webinars/{webinar_id}/participants", params)
        results.extend(data.get("participants", []))
        next_token = data.get("next_page_token")
        if not next_token:
            break
    return results


def is_configured() -> bool:
    return bool(ZOOM_ACCOUNT_ID and ZOOM_CLIENT_ID and ZOOM_CLIENT_SECRET)
