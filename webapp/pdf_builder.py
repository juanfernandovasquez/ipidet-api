"""Genera PDFs de comprobantes de pago usando fpdf2."""
from fpdf import FPDF
from datetime import datetime

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

_COLOR_PRIMARY   = (30,  58, 95)   # azul IPIDET
_COLOR_LIGHT_BG  = (241, 245, 249)
_COLOR_BORDER    = (226, 232, 240)
_COLOR_GRAY_TEXT = (100, 116, 139)
_COLOR_BODY_TEXT = (30,  41,  59)
_COLOR_WHITE     = (255, 255, 255)


def _fmt_fecha(fecha: str) -> str:
    """'2026-10-11' → '11/10/2026'"""
    try:
        return datetime.strptime(fecha, "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        return fecha or "—"


def _hr(pdf: FPDF, lm: float, rm: float) -> None:
    pdf.set_draw_color(*_COLOR_BORDER)
    pdf.set_line_width(0.3)
    pdf.line(lm, pdf.get_y(), rm, pdf.get_y())


def build_comprobante_pdf(
    comp: dict,
    socios_info: list,
    logo_path: str | None = None,
) -> bytes:
    """Genera el PDF del comprobante. Devuelve bytes."""

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()

    LM = 20          # left margin
    RM = 20          # right margin
    PW = pdf.w       # page width
    CW = PW - LM - RM  # content width

    tipo       = comp.get("tipo", "boleta")
    tipo_label = _TIPO_LABELS.get(tipo, tipo.upper())
    numero     = comp.get("numero") or ""

    # ════════════════════════════════════════════════════════════
    # CABECERA — dos columnas: emisor (izq) | tipo+número (der)
    # ════════════════════════════════════════════════════════════
    header_top = pdf.get_y()

    # Columna izquierda: logo + datos del emisor
    col_left  = CW * 0.55
    col_right = CW * 0.45
    x_right   = LM + col_left

    pdf.set_xy(LM, header_top)

    if logo_path:
        try:
            pdf.image(logo_path, x=LM, y=header_top, w=30, h=12)
            pdf.set_xy(LM, header_top + 14)
        except Exception:
            pass

    # Nombre comercial
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(*_COLOR_PRIMARY)
    pdf.set_x(LM)
    pdf.cell(col_left, 7, IPIDET_NOMBRE_COMERCIAL or "IPIDET", ln=False)
    pdf.ln(7)

    # Razón social
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*_COLOR_GRAY_TEXT)
    pdf.set_x(LM)
    razon = IPIDET_RAZON_SOCIAL or ""
    # Partir en dos líneas si es muy largo
    if len(razon) > 48:
        mid = razon.rfind(" ", 0, 48)
        pdf.cell(col_left, 4.5, razon[:mid], ln=False)
        pdf.ln(4.5)
        pdf.set_x(LM)
        pdf.cell(col_left, 4.5, razon[mid+1:], ln=False)
        pdf.ln(4.5)
    else:
        pdf.cell(col_left, 4.5, razon, ln=False)
        pdf.ln(4.5)

    pdf.set_x(LM)
    pdf.cell(col_left, 4.5, f"RUC: {IPIDET_RUC or '—'}", ln=False)
    pdf.ln(4.5)

    if IPIDET_DIRECCION:
        pdf.set_x(LM)
        pdf.cell(col_left, 4.5, IPIDET_DIRECCION, ln=False)
        pdf.ln(4.5)

    pdf.set_x(LM)
    pdf.cell(col_left, 4.5, "administracion@ipidet.org", ln=False)
    pdf.ln(4.5)

    header_bottom_left = pdf.get_y()

    # Columna derecha: recuadro azul con tipo y número
    box_h = header_bottom_left - header_top + 2
    if box_h < 28:
        box_h = 28
    pdf.set_fill_color(*_COLOR_PRIMARY)
    pdf.rect(x_right, header_top, col_right, box_h, "F")

    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*_COLOR_WHITE)
    pdf.set_xy(x_right, header_top + 5)
    pdf.cell(col_right, 6, tipo_label, align="C", ln=False)
    pdf.set_xy(x_right, header_top + 13)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(col_right, 7, f"N° {numero}", align="C", ln=False)

    pdf.set_y(max(header_bottom_left, header_top + box_h) + 5)

    # ════════════════════════════════════════════════════════════
    # FILA DE FECHAS
    # ════════════════════════════════════════════════════════════
    _hr(pdf, LM, PW - RM)
    pdf.ln(3)

    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*_COLOR_GRAY_TEXT)
    fecha_emision = _fmt_fecha(comp.get("fecha_emision") or "")
    fecha_pago    = _fmt_fecha(comp.get("fecha_pago") or "")

    pdf.set_x(LM)
    pdf.cell(CW / 3, 5.5, f"Fecha de emisión: {fecha_emision}")
    pdf.cell(CW / 3, 5.5, f"Fecha de pago: {fecha_pago}")
    pdf.cell(CW / 3, 5.5, "Moneda: SOLES", align="R")
    pdf.ln(5.5)
    pdf.ln(4)

    # ════════════════════════════════════════════════════════════
    # DATOS DEL ADQUIRIENTE / DESTINATARIO
    # ════════════════════════════════════════════════════════════
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*_COLOR_PRIMARY)
    pdf.set_x(LM)
    label_receptor = "DATOS DEL ADQUIRIENTE / USUARIO"
    if tipo == "factura":
        label_receptor = "DATOS DEL ADQUIRIENTE"
    pdf.cell(CW, 5.5, label_receptor)
    pdf.ln(5.5)
    _hr(pdf, LM, PW - RM)
    pdf.ln(3)

    empresa = comp.get("empresa") or ""
    if empresa:
        # Receptor empresa (factura)
        _row_receptor(pdf, LM, CW, "Razón social", empresa)
        if comp.get("ruc_empresa"):
            _row_receptor(pdf, LM, CW, "RUC", comp["ruc_empresa"])
    else:
        # Receptor persona (boleta)
        for s in socios_info[:3]:
            nombre = s.get("nombre") or "—"
            _row_receptor(pdf, LM, CW, "Nombre", nombre)
            if s.get("dni"):
                _row_receptor(pdf, LM, CW, "DNI", s["dni"])
            if s.get("email"):
                _row_receptor(pdf, LM, CW, "Email", s["email"])
            break  # solo primera persona si es boleta individual

        # Fallback si no hay socios_info pero hay destinatario en el comprobante
        if not socios_info:
            if comp.get("destinatario_nombre"):
                _row_receptor(pdf, LM, CW, "Nombre", comp["destinatario_nombre"])
            if comp.get("destinatario_dni"):
                _row_receptor(pdf, LM, CW, "DNI", comp["destinatario_dni"])

    pdf.ln(5)

    # ════════════════════════════════════════════════════════════
    # TABLA DE ÍTEMS
    # ════════════════════════════════════════════════════════════
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*_COLOR_PRIMARY)
    pdf.set_x(LM)
    pdf.cell(CW, 5.5, "DESCRIPCIÓN DEL SERVICIO")
    pdf.ln(5.5)
    _hr(pdf, LM, PW - RM)
    pdf.ln(2)

    # Encabezado de tabla
    col_desc  = CW * 0.50
    col_socio = CW * 0.25
    col_cant  = CW * 0.10
    col_pu    = CW * 0.15

    pdf.set_fill_color(*_COLOR_PRIMARY)
    pdf.set_text_color(*_COLOR_WHITE)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_x(LM)
    pdf.cell(col_desc,  6, "DESCRIPCIÓN",      fill=True)
    pdf.cell(col_socio, 6, "SOCIO/REFERENCIA", fill=True)
    pdf.cell(col_cant,  6, "CANT.",             fill=True, align="C")
    pdf.cell(col_pu,    6, "PRECIO (S/)",       fill=True, align="R")
    pdf.ln(6)

    items = comp.get("items") or []
    if not items:
        items = [{
            "producto_nombre": comp.get("producto_nombre") or comp.get("concepto") or "Cuota",
            "monto": comp.get("monto_total") or 0,
            "member_id": (comp.get("socios") or [""])[0],
        }]

    # Mapa member_id → nombre de socios_info
    nombre_map = {s.get("member_id", ""): s.get("nombre", "") for s in socios_info}

    pdf.set_font("Helvetica", "", 8)
    for i, item in enumerate(items):
        bg = _COLOR_LIGHT_BG if i % 2 == 0 else _COLOR_WHITE
        pdf.set_fill_color(*bg)
        pdf.set_text_color(*_COLOR_BODY_TEXT)

        desc   = str(item.get("producto_nombre") or item.get("codigo_sunat") or "—")
        mid    = str(item.get("member_id") or "")
        nombre = nombre_map.get(mid, mid)[:28]
        monto  = float(item.get("monto") or 0)

        pdf.set_x(LM)
        pdf.cell(col_desc,  6, desc[:48],  fill=True)
        pdf.cell(col_socio, 6, nombre,     fill=True)
        pdf.cell(col_cant,  6, "1",        fill=True, align="C")
        pdf.cell(col_pu,    6, f"{monto:.2f}", fill=True, align="R")
        pdf.ln(6)

    pdf.ln(3)
    _hr(pdf, LM, PW - RM)
    pdf.ln(3)

    # ════════════════════════════════════════════════════════════
    # TOTALES
    # ════════════════════════════════════════════════════════════
    monto_total = float(comp.get("monto_total") or 0)
    x_total = LM + CW * 0.50

    def _total_row(label: str, valor: str, bold: bool = False) -> None:
        pdf.set_x(x_total)
        if bold:
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_fill_color(*_COLOR_PRIMARY)
            pdf.set_text_color(*_COLOR_WHITE)
            pdf.cell(CW * 0.35, 7, label, fill=True, align="R")
            pdf.cell(CW * 0.15, 7, valor, fill=True, align="R")
        else:
            pdf.set_font("Helvetica", "", 8)
            pdf.set_text_color(*_COLOR_GRAY_TEXT)
            pdf.cell(CW * 0.35, 5.5, label, align="R")
            pdf.cell(CW * 0.15, 5.5, valor, align="R")
        pdf.ln(7 if bold else 5.5)

    _total_row("Op. inafectas / exoneradas:", f"S/ {monto_total:.2f}")
    _total_row("IGV (0%):", "S/ 0.00")
    _total_row("TOTAL A PAGAR:", f"S/ {monto_total:.2f}", bold=True)

    pdf.ln(6)

    # ════════════════════════════════════════════════════════════
    # LEYENDA EN LETRAS
    # ════════════════════════════════════════════════════════════
    from webapp.sunat_client import _monto_a_letras
    leyenda = _monto_a_letras(monto_total)
    pdf.set_fill_color(*_COLOR_LIGHT_BG)
    pdf.set_text_color(*_COLOR_BODY_TEXT)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_x(LM)
    pdf.cell(CW, 6, f"  {leyenda}", fill=True)
    pdf.ln(10)

    # ════════════════════════════════════════════════════════════
    # PIE — hash SUNAT y nota legal
    # ════════════════════════════════════════════════════════════
    _hr(pdf, LM, PW - RM)
    pdf.ln(3)

    pdf.set_font("Helvetica", "", 7)
    pdf.set_text_color(*_COLOR_GRAY_TEXT)
    pdf.set_x(LM)

    sunat_hash = comp.get("sunat_hash") or ""
    if sunat_hash:
        pdf.cell(CW, 4, f"Hash SUNAT: {sunat_hash[:60]}", align="C")
        pdf.ln(4)
        pdf.set_x(LM)

    ambiente = comp.get("sunat_ambiente") or ""
    if ambiente == "produccion":
        sunat_nota = "Comprobante electrónico emitido y aceptado por SUNAT."
    elif ambiente:
        sunat_nota = f"Documento de referencia — emitido en ambiente {ambiente}."
    else:
        sunat_nota = "Representación impresa del comprobante. El documento oficial es el XML SUNAT."

    pdf.cell(CW, 4, sunat_nota, align="C")
    pdf.ln(4)
    pdf.set_x(LM)
    pdf.cell(CW, 4,
             f"RUC Emisor: {IPIDET_RUC}  ·  {IPIDET_NOMBRE_COMERCIAL}  ·  administracion@ipidet.org",
             align="C")

    return bytes(pdf.output())


def _row_receptor(pdf: FPDF, lm: float, cw: float, label: str, valor: str) -> None:
    pdf.set_x(lm)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*_COLOR_GRAY_TEXT)
    pdf.cell(cw * 0.22, 5.5, label + ":", ln=False)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*_COLOR_BODY_TEXT)
    pdf.cell(cw * 0.78, 5.5, valor, ln=False)
    pdf.ln(5.5)
