import gspread
from datetime import date, datetime
from config.settings import GOOGLE_CREDENTIALS_PATH, BILLING_SHEET_ID

# Columnas de cada hoja (índice 1-based, para update_cell)
_COL_SOCIOS = {
    "id_socio": 1, "nombre": 2, "email": 3, "tipo": 4, "estado": 5,
    "fecha_ingreso": 6, "fecha_vencimiento": 7, "plan_pago": 8, "monto_total": 9,
}
_COL_CUOTAS = {
    "id_cuota": 1, "id_socio": 2, "nro_cuota": 3, "total_cuotas": 4,
    "monto": 5, "vencimiento": 6, "estado": 7, "fecha_pago": 8,
    "medio_pago": 9, "constancia": 10, "notas": 11,
}

_sheet = None


def _get_sheet():
    global _sheet
    if _sheet is None:
        client = gspread.service_account(filename=GOOGLE_CREDENTIALS_PATH)
        _sheet = client.open_by_key(BILLING_SHEET_ID)
    return _sheet


def _parse_date(val) -> date | None:
    if not val:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(str(val).strip(), fmt).date()
        except ValueError:
            continue
    return None


def get_socios() -> list[dict]:
    return _get_sheet().worksheet("Socios").get_all_records()


def get_cuotas() -> list[dict]:
    return _get_sheet().worksheet("Cuotas").get_all_records()


def get_socio_by_email(email: str) -> dict | None:
    email = email.lower().strip()
    for s in get_socios():
        if s.get("email", "").lower().strip() == email:
            return s
    return None


def get_cuotas_for_socio(id_socio: str) -> list[dict]:
    return [c for c in get_cuotas() if c.get("id_socio") == id_socio]


def get_pending_cuotas(id_socio: str) -> list[dict]:
    return [
        c for c in get_cuotas_for_socio(id_socio)
        if c.get("estado", "").lower() in ("pendiente", "vencido")
    ]


def get_upcoming_due_cuotas(days_ahead: int) -> list[dict]:
    today = date.today()
    result = []
    for c in get_cuotas():
        if c.get("estado", "").lower() != "pendiente":
            continue
        due = _parse_date(c.get("vencimiento"))
        if due and 0 <= (due - today).days <= days_ahead:
            result.append(c)
    return result


def get_overdue_cuotas() -> list[dict]:
    today = date.today()
    result = []
    for c in get_cuotas():
        if c.get("estado", "").lower() != "pendiente":
            continue
        due = _parse_date(c.get("vencimiento"))
        if due and due < today:
            result.append(c)
    return result


def mark_cuota_paid(id_cuota: str, fecha_pago: str, medio_pago: str) -> bool:
    ws = _get_sheet().worksheet("Cuotas")
    try:
        cell = ws.find(id_cuota, in_column=_COL_CUOTAS["id_cuota"])
    except gspread.exceptions.CellNotFound:
        return False
    row = cell.row
    ws.update_cell(row, _COL_CUOTAS["estado"], "pagado")
    ws.update_cell(row, _COL_CUOTAS["fecha_pago"], fecha_pago)
    ws.update_cell(row, _COL_CUOTAS["medio_pago"], medio_pago)
    ws.update_cell(row, _COL_CUOTAS["constancia"], "si")
    return True


def mark_cuota_overdue(id_cuota: str) -> bool:
    ws = _get_sheet().worksheet("Cuotas")
    try:
        cell = ws.find(id_cuota, in_column=_COL_CUOTAS["id_cuota"])
    except gspread.exceptions.CellNotFound:
        return False
    ws.update_cell(cell.row, _COL_CUOTAS["estado"], "vencido")
    return True


def update_socio_estado(id_socio: str, estado: str) -> bool:
    ws = _get_sheet().worksheet("Socios")
    try:
        cell = ws.find(id_socio, in_column=_COL_SOCIOS["id_socio"])
    except gspread.exceptions.CellNotFound:
        return False
    ws.update_cell(cell.row, _COL_SOCIOS["estado"], estado)
    return True
