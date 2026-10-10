"""
Generación de PDF para Constancia de Asociado — IPIDET.
Usa fpdf2 (ya en requirements.txt).
"""
import os
from datetime import date as _date

from fpdf import FPDF

_LOGO_PATH = os.path.join(os.path.dirname(__file__), "..", "mockup_assets", "logo.png")

_MESES = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril",
    5: "mayo", 6: "junio", 7: "julio", 8: "agosto",
    9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre",
}


def _fecha_larga(d: _date) -> str:
    return f"{d.day} de {_MESES[d.month]} de {d.year}"


def generar_constancia_pdf(
    numero: str,
    nombre_completo: str,   # en MAYÚSCULAS
    titulo: str,            # "Contador", "Abogada", etc.
    dni: str | None,
    tipo_socio: str,        # "ordinario", "filial", etc.
    tratamiento: str,       # "el señor" | "la señora"
    fecha_emision: _date | None = None,
) -> bytes:
    """Devuelve los bytes del PDF de constancia."""
    fecha = fecha_emision or _date.today()

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_margins(left=25, top=25, right=25)
    pdf.set_auto_page_break(auto=False)
    pdf.add_page()

    # ── Logo ──────────────────────────────────────────────────────────────
    logo_w = 52
    if os.path.exists(_LOGO_PATH):
        logo_x = (210 - logo_w) / 2
        pdf.image(_LOGO_PATH, x=logo_x, y=25, w=logo_w)
    pdf.ln(42)

    # ── Título ─────────────────────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 15)
    pdf.cell(0, 8, "CERTIFICADO DE ASOCIADO", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 7, f"Nº {numero}", align="C", new_x="LMARGIN", new_y="NEXT")

    pdf.ln(6)

    # Línea divisoria
    pdf.set_draw_color(34, 139, 34)  # verde
    pdf.set_line_width(0.8)
    pdf.line(25, pdf.get_y(), 185, pdf.get_y())
    pdf.ln(8)

    # ── Cuerpo ──────────────────────────────────────────────────────────────
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(30, 30, 30)

    tipo_label = tipo_socio.lower() if tipo_socio else "ordinario"

    dni_text = ""
    if dni:
        id_verb = "identificada" if tratamiento.startswith("la") else "identificado"
        dni_text = f", {id_verb} con DNI N° {dni},"

    parrafo1 = (
        "El Instituto Peruano de Investigación y Desarrollo Tributario - IPIDET, "
        f"certifica que {tratamiento} {nombre_completo}{dni_text} es asociado(a) "
        f"{tipo_label} de esta institución."
    )

    parrafo2 = (
        "Extendemos el presente certificado a solicitud del interesado(a), "
        "para los fines que estime conveniente."
    )

    pdf.set_font("Helvetica", "", 11)
    pdf.multi_cell(0, 7, parrafo1, align="J")
    pdf.ln(5)
    pdf.multi_cell(0, 7, parrafo2, align="J")
    pdf.ln(12)

    # ── Fecha ───────────────────────────────────────────────────────────────
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 7, f"Lima, {_fecha_larga(fecha)}", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(14)

    # ── Institución ─────────────────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "INSTITUTO PERUANO DE INVESTIGACION Y DESARROLLO TRIBUTARIO", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(22)

    # ── Firma (línea) ────────────────────────────────────────────────────────
    pdf.set_draw_color(80, 80, 80)
    pdf.set_line_width(0.4)
    firma_w = 80
    firma_x = (210 - firma_w) / 2
    pdf.line(firma_x, pdf.get_y(), firma_x + firma_w, pdf.get_y())
    pdf.ln(4)

    # ── Nombre y cargo ──────────────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "KATARZYNA DUNIN BORKOWSKI", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, "Presidenta del Consejo Directivo", align="C", new_x="LMARGIN", new_y="NEXT")

    # ── Footer ──────────────────────────────────────────────────────────────
    footer_y = 267
    pdf.set_y(footer_y)
    pdf.set_draw_color(34, 139, 34)
    pdf.set_line_width(1.5)
    pdf.line(25, footer_y, 25, footer_y + 12)
    pdf.set_line_width(0.3)
    pdf.set_draw_color(200, 200, 200)
    pdf.rect(27, footer_y, 158, 12)

    pdf.set_xy(30, footer_y + 2)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(80, 80, 80)
    pdf.cell(0, 4, "administracion@ipidet.org  |  www.ipidet.org  |  (01) 422-6048", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(30)
    pdf.cell(0, 4, "Lima, Perú")

    return bytes(pdf.output())
