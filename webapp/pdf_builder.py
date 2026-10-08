"""Genera PDFs de comprobantes de pago usando fpdf2."""
from fpdf import FPDF

_TIPO_LABELS = {
    "boleta":  "BOLETA DE VENTA",
    "factura": "FACTURA",
    "recibo":  "RECIBO POR HONORARIOS",
}


def build_comprobante_pdf(comp: dict, socios_info: list) -> bytes:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_margins(20, 20, 20)
    W = pdf.w - 40

    # ── Header ────────────────────────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(30, 58, 95)
    pdf.cell(0, 10, "IPIDET", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(100, 116, 139)
    pdf.cell(0, 5, "Instituto Peruano de Investigacion y Desarrollo Tributario", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.cell(0, 5, "administracion@ipidet.org", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(5)

    # ── Tipo + número ─────────────────────────────────────────────────────────
    tipo = comp.get("tipo", "boleta")
    tipo_label = _TIPO_LABELS.get(tipo, tipo.upper())
    numero = comp.get("numero") or ""
    pdf.set_fill_color(30, 58, 95)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(W * 0.6, 9, tipo_label, fill=True, align="C")
    pdf.cell(W * 0.4, 9, f"N° {numero}", fill=True, align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # ── Fecha ────────────────────────────────────────────────────────────────
    pdf.set_text_color(71, 85, 105)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"Fecha de emision: {comp.get('fecha_emision') or '—'}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)

    # ── Receptor ─────────────────────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(30, 58, 95)
    pdf.cell(0, 7, "RECEPTOR", new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(226, 232, 240)
    pdf.line(20, pdf.get_y(), pdf.w - 20, pdf.get_y())
    pdf.ln(3)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(71, 85, 105)

    empresa = comp.get("empresa") or ""
    if empresa:
        pdf.cell(0, 6, f"Empresa: {empresa}", new_x="LMARGIN", new_y="NEXT")
        if comp.get("ruc_empresa"):
            pdf.cell(0, 6, f"RUC: {comp['ruc_empresa']}", new_x="LMARGIN", new_y="NEXT")
    else:
        for s in socios_info[:5]:
            nombre = s.get("nombre") or ""
            pdf.set_font("Helvetica", "B", 10)
            pdf.cell(0, 6, nombre, new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 9)
            if s.get("dni"):
                pdf.cell(0, 5, f"DNI: {s['dni']}", new_x="LMARGIN", new_y="NEXT")
            if s.get("celular"):
                pdf.cell(0, 5, f"Celular: {s['celular']}", new_x="LMARGIN", new_y="NEXT")
            if s.get("email"):
                pdf.cell(0, 5, f"Email: {s['email']}", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)

    pdf.ln(5)

    # ── Detalle ───────────────────────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(30, 58, 95)
    pdf.cell(0, 7, "DETALLE", new_x="LMARGIN", new_y="NEXT")
    pdf.line(20, pdf.get_y(), pdf.w - 20, pdf.get_y())
    pdf.ln(3)

    pdf.set_fill_color(241, 245, 249)
    pdf.set_text_color(71, 85, 105)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(W * 0.55, 7, "Descripcion", fill=True)
    pdf.cell(W * 0.25, 7, "Socio", fill=True)
    pdf.cell(W * 0.20, 7, "Monto (S/)", fill=True, align="R", new_x="LMARGIN", new_y="NEXT")

    items = comp.get("items") or []
    if not items:
        items = [{"producto_nombre": comp.get("producto_nombre") or comp.get("concepto") or "Cuota",
                  "monto": comp.get("monto_total") or 0,
                  "member_id": (comp.get("socios") or [""])[0]}]

    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(30, 41, 59)
    for i, item in enumerate(items):
        bg = (248, 250, 252) if i % 2 == 1 else (255, 255, 255)
        pdf.set_fill_color(*bg)
        desc  = str(item.get("producto_nombre") or item.get("codigo_sunat") or "—")[:55]
        mid   = str(item.get("member_id") or "")
        monto = float(item.get("monto") or 0)
        pdf.cell(W * 0.55, 6, desc, fill=True)
        pdf.cell(W * 0.25, 6, mid, fill=True)
        pdf.cell(W * 0.20, 6, f"{monto:.2f}", fill=True, align="R", new_x="LMARGIN", new_y="NEXT")

    pdf.ln(3)
    pdf.set_draw_color(226, 232, 240)
    pdf.line(20, pdf.get_y(), pdf.w - 20, pdf.get_y())
    pdf.ln(3)

    monto_total = float(comp.get("monto_total") or 0)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(30, 58, 95)
    pdf.cell(W * 0.80, 8, "TOTAL", align="R")
    pdf.cell(W * 0.20, 8, f"S/ {monto_total:.2f}", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(10)

    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(148, 163, 184)
    pdf.cell(0, 5, "Este documento es un comprobante de referencia. El documento oficial es el XML SUNAT adjunto.", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.cell(0, 5, "IPIDET  ·  administracion@ipidet.org", new_x="LMARGIN", new_y="NEXT", align="C")

    return bytes(pdf.output())
