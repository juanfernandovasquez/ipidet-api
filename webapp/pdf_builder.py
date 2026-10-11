"""Genera PDFs de comprobantes de pago usando fpdf2."""
from fpdf import FPDF, XPos, YPos
from datetime import datetime
from pathlib import Path

from config.settings import (
    IPIDET_RUC,
    IPIDET_RAZON_SOCIAL,
    IPIDET_NOMBRE_COMERCIAL,
    IPIDET_DIRECCION,
)

_TIPO_LABELS = {
    "boleta":  "BOLETA DE VENTA ELECTRÓNICA",
    "factura": "FACTURA ELECTRÓNICA",
    "recibo":  "RECIBO POR HONORARIOS",
}

_COLOR_PRIMARY   = (30,  58, 95)
_COLOR_LIGHT_BG  = (241, 245, 249)
_COLOR_BORDER    = (226, 232, 240)
_COLOR_GRAY_TEXT = (100, 116, 139)
_COLOR_BODY_TEXT = (30,  41,  59)
_COLOR_WHITE     = (255, 255, 255)

_LOGO_PATH = Path(__file__).parent / "static" / "logo.png"

# Caracteres fuera de Latin-1 que fpdf2 con fuentes core no puede encodear
_UNICODE_MAP = {
    "—": "-", "–": "-",          # em dash, en dash
    "‘": "'", "’": "'",          # comillas simples tipograficas
    "“": '"', "”": '"',          # comillas dobles tipograficas
    "…": "...",                        # ellipsis
    "·": ".", "•": "*",          # bullet points
    "º": "o", "ª": "a",          # ordinal
}


def _safe(text: object) -> str:
    """Convierte a str y reemplaza caracteres fuera de Latin-1."""
    s = str(text or "")
    out = []
    for ch in s:
        if ch in _UNICODE_MAP:
            out.append(_UNICODE_MAP[ch])
        elif ord(ch) > 255:
            out.append("?")
        else:
            out.append(ch)
    return "".join(out)


def _fmt_fecha(fecha: str) -> str:
    try:
        return datetime.strptime(fecha, "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        return fecha or "-"


def _hr(pdf: FPDF, lm: float, rm: float) -> None:
    pdf.set_draw_color(*_COLOR_BORDER)
    pdf.set_line_width(0.3)
    pdf.line(lm, pdf.get_y(), rm, pdf.get_y())


def _cell(pdf: FPDF, w: float, h: float, txt: str, *,
          align: str = "L", fill: bool = False, bold: bool = False) -> None:
    """Celda que avanza a la derecha (misma línea). Sanitiza texto a Latin-1."""
    if bold:
        pdf.set_font("Helvetica", "B", pdf.font_size_pt)
    pdf.cell(w, h, _safe(txt), align=align, fill=fill,
             new_x=XPos.RIGHT, new_y=YPos.TOP)


def _cell_nl(pdf: FPDF, w: float, h: float, txt: str, *,
             align: str = "L", fill: bool = False) -> None:
    """Celda que salta a la siguiente línea. Sanitiza texto a Latin-1."""
    pdf.cell(w, h, _safe(txt), align=align, fill=fill,
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def build_comprobante_pdf(
    comp: dict,
    socios_info: list,
    logo_path: str | None = None,
) -> bytes:
    if logo_path is None and _LOGO_PATH.exists():
        logo_path = str(_LOGO_PATH)

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()

    LM = 20
    RM = 20
    PW = pdf.w
    CW = PW - LM - RM

    tipo       = comp.get("tipo", "boleta")
    tipo_label = _TIPO_LABELS.get(tipo, tipo.upper())
    numero     = comp.get("numero") or ""

    # ── CABECERA ─────────────────────────────────────────────────────────────
    header_top = pdf.get_y()
    col_l = CW * 0.56   # ~95 mm — columna emisor
    col_r = CW * 0.44   # ~75 mm — columna tipo/número
    x_r   = LM + col_l  # inicio columna derecha

    # ── Columna izquierda: logo ───────────────────────────────────────────
    LOGO_W    = 38   # mm de ancho del logo
    LOGO_HMIN = 13   # altura mínima asumida si fpdf2 no avanza cursor

    logo_loaded = False
    logo_h      = 0

    if logo_path:
        try:
            pdf.image(logo_path, x=LM, y=header_top, w=LOGO_W)
            logo_loaded = True
            # fpdf2 debería avanzar el cursor al fondo del logo;
            # si no lo hace (cursor sin cambio), usamos el mínimo estimado
            y_after_img = pdf.get_y()
            logo_h = max(y_after_img - header_top, LOGO_HMIN)
        except Exception:
            pass

    if not logo_loaded:
        # Fallback: nombre IPIDET como texto si el logo no cargó
        pdf.set_xy(LM, header_top)
        pdf.set_font("Helvetica", "B", 13)
        pdf.set_text_color(*_COLOR_PRIMARY)
        _cell_nl(pdf, col_l, 8, _safe(IPIDET_NOMBRE_COMERCIAL or "IPIDET"))
        logo_h = 8

    # ── Datos emisor debajo del logo ──────────────────────────────────────
    y_company = header_top + logo_h + 2   # 2 mm de espacio tras el logo
    pdf.set_xy(LM, y_company)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*_COLOR_GRAY_TEXT)

    def _emit_text(text: str) -> None:
        """Escribe texto en la columna izquierda, wrapeando por palabras si no entra."""
        max_w = col_l - 1   # 1 mm de margen de seguridad
        words = text.split()
        line = ""
        for word in words:
            candidate = (line + " " + word).strip() if line else word
            if pdf.get_string_width(candidate) <= max_w:
                line = candidate
            else:
                if line:
                    pdf.set_x(LM); _cell_nl(pdf, col_l, 4.5, line)
                line = word
        if line:
            pdf.set_x(LM); _cell_nl(pdf, col_l, 4.5, line)

    _emit_text(_safe(IPIDET_RAZON_SOCIAL or ""))
    pdf.set_x(LM); _cell_nl(pdf, col_l, 4.5, _safe(f"RUC: {IPIDET_RUC or '-'}"))
    if IPIDET_DIRECCION:
        _emit_text(_safe(IPIDET_DIRECCION))
    pdf.set_x(LM); _cell_nl(pdf, col_l, 4.5, "administracion@ipidet.org")

    left_bottom = pdf.get_y()
    header_h    = left_bottom - header_top   # altura total de la columna izquierda

    # ── Columna derecha: tipo + número (sin relleno de color) ────────────
    BOX_H  = 22   # altura del recuadro (mm)
    box_top = header_top + (header_h - BOX_H) / 2
    if box_top < header_top:
        box_top = header_top + 2

    # Solo borde (sin relleno)
    pdf.set_draw_color(*_COLOR_PRIMARY)
    pdf.set_line_width(0.6)
    pdf.rect(x_r + 3, box_top, col_r - 5, BOX_H, "D")

    # Tipo de documento
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*_COLOR_PRIMARY)
    pdf.set_xy(x_r, box_top + 3)
    _cell_nl(pdf, col_r, 6, _safe(tipo_label), align="C")

    # Número
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(*_COLOR_BODY_TEXT)
    pdf.set_xy(x_r, box_top + 11)
    _cell_nl(pdf, col_r, 7, _safe(f"Nro. {numero}"), align="C")

    # Avanzar cursor al final de la cabecera
    pdf.set_y(left_bottom + 4)

    # ── FECHAS ────────────────────────────────────────────────────────────────
    _hr(pdf, LM, PW - RM)
    pdf.ln(3)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*_COLOR_GRAY_TEXT)
    fecha_emision = _safe(_fmt_fecha(comp.get("fecha_emision") or ""))
    fecha_pago    = _safe(_fmt_fecha(comp.get("fecha_pago") or ""))
    pdf.set_x(LM)
    _cell(pdf, CW / 3, 5.5, f"Fecha emision: {fecha_emision}")
    _cell(pdf, CW / 3, 5.5, f"Fecha pago: {fecha_pago}")
    _cell_nl(pdf, CW / 3, 5.5, "Moneda: SOLES", align="R")
    pdf.ln(4)

    # ── RECEPTOR ─────────────────────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*_COLOR_PRIMARY)
    pdf.set_x(LM)
    lbl_rec = "DATOS DEL ADQUIRIENTE" if tipo == "factura" else "DATOS DEL RECEPTOR"
    _cell_nl(pdf, CW, 5.5, lbl_rec)
    _hr(pdf, LM, PW - RM)
    pdf.ln(3)

    empresa = comp.get("empresa") or ""
    if empresa:
        _row_receptor(pdf, LM, CW, "Razón social", empresa)
        if comp.get("ruc_empresa"):
            _row_receptor(pdf, LM, CW, "RUC", comp["ruc_empresa"])
    elif socios_info:
        s = socios_info[0]
        _row_receptor(pdf, LM, CW, "Nombre", s.get("nombre") or "—")
        if s.get("dni"):
            _row_receptor(pdf, LM, CW, "Tipo / Nro. doc.", f"DNI / {s['dni']}")
        if s.get("email"):
            _row_receptor(pdf, LM, CW, "Email", s["email"])
    else:
        if comp.get("destinatario_nombre"):
            _row_receptor(pdf, LM, CW, "Nombre", comp["destinatario_nombre"])
        if comp.get("destinatario_dni"):
            _row_receptor(pdf, LM, CW, "Tipo / Nro. doc.", f"DNI / {comp['destinatario_dni']}")

    pdf.ln(5)

    # ── TABLA DE ÍTEMS ───────────────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*_COLOR_PRIMARY)
    pdf.set_x(LM)
    _cell_nl(pdf, CW, 5.5, "DESCRIPCIÓN DEL SERVICIO")
    _hr(pdf, LM, PW - RM)
    pdf.ln(2)

    c_desc  = CW * 0.50
    c_ref   = CW * 0.25
    c_cant  = CW * 0.10
    c_monto = CW * 0.15

    pdf.set_fill_color(*_COLOR_PRIMARY)
    pdf.set_text_color(*_COLOR_WHITE)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_x(LM)
    _cell(pdf, c_desc,  6, "DESCRIPCIÓN",       fill=True)
    _cell(pdf, c_ref,   6, "REFERENCIA",         fill=True)
    _cell(pdf, c_cant,  6, "CANT.",              fill=True, align="C")
    _cell_nl(pdf, c_monto, 6, "IMPORTE (S/)",    fill=True, align="R")

    items = comp.get("items") or []
    if not items:
        items = [{
            "producto_nombre": comp.get("producto_nombre") or comp.get("concepto") or "Cuota",
            "monto": comp.get("monto_total") or 0,
            "member_id": (comp.get("socios") or [""])[0],
        }]

    nombre_map = {s.get("member_id", ""): s.get("nombre", "") for s in socios_info}

    pdf.set_font("Helvetica", "", 8)
    for i, item in enumerate(items):
        bg = _COLOR_LIGHT_BG if i % 2 == 0 else _COLOR_WHITE
        pdf.set_fill_color(*bg)
        pdf.set_text_color(*_COLOR_BODY_TEXT)
        desc   = str(item.get("producto_nombre") or "—")[:50]
        mid    = str(item.get("member_id") or "")
        ref    = (nombre_map.get(mid, mid) or mid)[:28]
        monto  = float(item.get("monto") or 0)
        pdf.set_x(LM)
        _cell(pdf, c_desc,  6, desc,           fill=True)
        _cell(pdf, c_ref,   6, ref,             fill=True)
        _cell(pdf, c_cant,  6, "1",             fill=True, align="C")
        _cell_nl(pdf, c_monto, 6, f"{monto:.2f}", fill=True, align="R")

    pdf.ln(3)
    _hr(pdf, LM, PW - RM)
    pdf.ln(3)

    # ── TOTALES ──────────────────────────────────────────────────────────────
    monto_total = float(comp.get("monto_total") or 0)
    x_tot = LM + CW * 0.50
    cw_lbl = CW * 0.35
    cw_val = CW * 0.15

    def _tot(label: str, valor: str, *, resaltar: bool = False) -> None:
        pdf.set_x(x_tot)
        if resaltar:
            pdf.set_fill_color(*_COLOR_PRIMARY)
            pdf.set_text_color(*_COLOR_WHITE)
            pdf.set_font("Helvetica", "B", 9)
            _cell(pdf, cw_lbl, 7, label, fill=True, align="R")
            _cell_nl(pdf, cw_val, 7, valor, fill=True, align="R")
        else:
            pdf.set_text_color(*_COLOR_GRAY_TEXT)
            pdf.set_font("Helvetica", "", 8)
            _cell(pdf, cw_lbl, 5.5, label, align="R")
            _cell_nl(pdf, cw_val, 5.5, valor, align="R")

    _tot("Op. exoneradas / inafectas:", f"S/ {monto_total:.2f}")
    _tot("IGV (0%):", "S/ 0.00")
    _tot("TOTAL A PAGAR:", f"S/ {monto_total:.2f}", resaltar=True)

    pdf.ln(6)

    # ── LEYENDA EN LETRAS ────────────────────────────────────────────────────
    try:
        from webapp.sunat_client import _monto_a_letras
        leyenda = _monto_a_letras(monto_total)
    except Exception:
        leyenda = f"SON S/ {monto_total:.2f}"

    pdf.set_fill_color(*_COLOR_LIGHT_BG)
    pdf.set_text_color(*_COLOR_BODY_TEXT)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_x(LM)
    _cell_nl(pdf, CW, 6, f"  {leyenda}", fill=True)
    pdf.ln(8)

    # ── PIE ──────────────────────────────────────────────────────────────────
    _hr(pdf, LM, PW - RM)
    pdf.ln(3)
    pdf.set_font("Helvetica", "", 7)
    pdf.set_text_color(*_COLOR_GRAY_TEXT)

    sunat_hash = comp.get("sunat_hash") or ""
    if sunat_hash:
        pdf.set_x(LM)
        _cell_nl(pdf, CW, 4, f"Hash SUNAT: {sunat_hash[:70]}", align="C")

    ambiente = comp.get("sunat_ambiente") or ""
    if ambiente == "produccion":
        nota = "Comprobante electrónico emitido y aceptado por SUNAT."
    elif ambiente:
        nota = f"Documento de referencia — emitido en ambiente {ambiente}."
    else:
        nota = "Representación impresa del comprobante. Documento oficial: XML SUNAT."

    pdf.set_x(LM)
    _cell_nl(pdf, CW, 4, nota, align="C")
    pdf.set_x(LM)
    _cell_nl(pdf, CW, 4,
             f"RUC Emisor: {IPIDET_RUC}  ·  {IPIDET_NOMBRE_COMERCIAL}  ·  administracion@ipidet.org",
             align="C")

    return bytes(pdf.output())


def _row_receptor(pdf: FPDF, lm: float, cw: float, label: str, valor: str) -> None:
    pdf.set_x(lm)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*_COLOR_GRAY_TEXT)
    _cell(pdf, cw * 0.22, 5.5, label + ":")
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*_COLOR_BODY_TEXT)
    _cell_nl(pdf, cw * 0.78, 5.5, valor)
