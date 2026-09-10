"""
Cliente de solo lectura para la WooCommerce REST API v3.
Nunca hace POST/PUT/DELETE — solo GET.
"""
import httpx
from config.settings import WC_API_KEY, WC_API_SECRET, WC_STORE_URL

_BASE = WC_STORE_URL.rstrip("/") + "/wp-json/wc/v3"
_AUTH = (WC_API_KEY, WC_API_SECRET)
_TIMEOUT = 15


def _get(path: str, params: dict = None) -> list | dict:
    url = f"{_BASE}{path}"
    r = httpx.get(url, auth=_AUTH, params=params or {}, timeout=_TIMEOUT, verify=True)
    r.raise_for_status()
    return r.json()


def get_orders(
    page: int = 1,
    per_page: int = 50,
    status: str = "any",
    after: str = None,
    before: str = None,
    search: str = None,
) -> tuple[list[dict], int]:
    """
    Devuelve (orders, total_pages).
    status: "completed" | "processing" | "any" | etc.
    after/before: ISO 8601 date string.
    """
    params: dict = {
        "page": page,
        "per_page": per_page,
        "orderby": "date",
        "order": "desc",
    }
    if status and status != "any":
        params["status"] = status
    if after:
        params["after"] = after
    if before:
        params["before"] = before
    if search:
        params["search"] = search

    url = f"{_BASE}/orders"
    r = httpx.get(url, auth=_AUTH, params=params, timeout=_TIMEOUT, verify=True)
    r.raise_for_status()
    total_pages = int(r.headers.get("X-WP-TotalPages", 1))
    return r.json(), total_pages


def get_order(order_id: int) -> dict:
    return _get(f"/orders/{order_id}")


def get_customers(page: int = 1, per_page: int = 100) -> tuple[list[dict], int]:
    """Devuelve (customers, total_pages). role=all para incluir subscribers, no solo customers."""
    url = f"{_BASE}/customers"
    params = {"page": page, "per_page": per_page, "orderby": "registered_date", "order": "desc", "role": "all"}
    r = httpx.get(url, auth=_AUTH, params=params, timeout=_TIMEOUT, verify=True)
    r.raise_for_status()
    total_pages = int(r.headers.get("X-WP-TotalPages", 1))
    return r.json(), total_pages
