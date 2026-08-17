import io
import re
from markupsafe import Markup, escape
from fastapi import FastAPI, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
import webapp.db as pdb
import webapp.portal_db as portal_db
import webapp.portal_router as portal_routes

app = FastAPI(title="IPIDET Admin")

# CORS: solo permite llamadas desde el servidor WordPress (server-to-server)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://ipidet.org"],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-WC-Webhook-Signature"],
)
templates = Jinja2Templates(directory="webapp/templates")

_URL_RE = re.compile(r'(https?://[^\s\|\]>\"\']+)')

def _autolink(text: str) -> Markup:
    if not text:
        return Markup("")
    def _replace(m):
        url = m.group(1)
        short = url if len(url) <= 50 else url[:47] + "…"
        return f'<a href="{escape(url)}" target="_blank" rel="noopener" class="text-blue-600 hover:underline break-all">{escape(short)}</a>'
    return Markup(_URL_RE.sub(_replace, str(escape(text))))

templates.env.filters["autolink"] = _autolink

def _format_fecha(value) -> str:
    """Formatea fecha ISO string o datetime a DD/MM/YYYY."""
    if not value:
        return '—'
    try:
        s = str(value)[:10]  # "YYYY-MM-DD"
        y, m, d = s.split('-')
        return f"{d}/{m}/{y}"
    except Exception:
        return str(value)

templates.env.filters["format_fecha"] = _format_fecha

STATUS_LABELS = {
    "pagado":        ("Pagado",        "green"),
    "debe":          ("Debe",          "red"),
    "fraccionamiento":("Fraccionamiento","amber"),
    "exonerado":     ("Exonerado",     "gray"),
    "no_aplica":     ("N/A",           "gray"),
    "pendiente":     ("Pendiente",     "yellow"),
    "retirar":       ("Retirar",       "slate"),
    "en_revision":   ("En revisión",   "purple"),
    "revisar":       ("Revisar",       "orange"),
    "parcial":       ("Parcial",       "blue"),
}

def _ctx(**kwargs):
    return {"status_labels": STATUS_LABELS, **kwargs}


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    stats = pdb.get_stats()
    return templates.TemplateResponse(request, "dashboard.html", _ctx(stats=stats))


# ── Padrón ────────────────────────────────────────────────────────────────────

@app.get("/members", response_class=HTMLResponse)
async def members_list(
    request: Request,
    search: str = "",
    estado: str = "",
    pago: str = "",
    ubicacion: str = "",
    page: int = 1,
):
    docs, total = pdb.get_members(search, estado, pago, ubicacion, page)
    return templates.TemplateResponse(request, "members.html", _ctx(
        members=docs, total=total,
        search=search, estado=estado, pago=pago, ubicacion=ubicacion,
        page=page, per_page=50,
        total_pages=max(1, (total + 49) // 50),
    ))


@app.get("/members/{member_id}", response_class=HTMLResponse)
async def member_detail(request: Request, member_id: str):
    doc = pdb.get_member(member_id)
    if not doc:
        return RedirectResponse("/members")
    return templates.TemplateResponse(request, "member.html", _ctx(member=doc))


@app.post("/members/{member_id}/emails/toggle")
async def toggle_email(member_id: str, email: str = Form(...), estado: str = Form(...)):
    pdb.update_email_status(member_id, email, estado)
    return RedirectResponse(f"/members/{member_id}", status_code=303)


@app.post("/members/{member_id}/emails/add")
async def add_email(member_id: str, new_email: str = Form(...)):
    if new_email.strip():
        pdb.add_email(member_id, new_email.strip())
    return RedirectResponse(f"/members/{member_id}", status_code=303)


@app.post("/members/{member_id}/notes")
async def update_notes(member_id: str, notas: str = Form(...)):
    pdb.update_member_notes(member_id, notas)
    return RedirectResponse(f"/members/{member_id}", status_code=303)


@app.post("/members/{member_id}/estado")
async def update_member_estado(member_id: str, estado: str = Form(...)):
    pdb.update_member_estado(member_id, estado)
    return RedirectResponse(f"/members/{member_id}", status_code=303)


# ── Cobranzas ─────────────────────────────────────────────────────────────────

@app.get("/billing", response_class=HTMLResponse)
async def billing(
    request: Request,
    periodo: str = "2026",
    estado: str = "",
    empresa: str = "",
    search: str = "",
    comprobante_emitido: str = "",
    page: int = 1,
):
    docs, total = pdb.get_payments(periodo, estado, empresa, search, page, comprobante_emitido)
    return templates.TemplateResponse(request, "billing.html", _ctx(
        payments=docs, total=total,
        periodo=periodo, estado=estado, empresa=empresa, search=search,
        comprobante_emitido=comprobante_emitido,
        page=page, per_page=50,
        total_pages=max(1, (total + 49) // 50),
    ))


@app.get("/billing/export")
async def billing_export(
    periodo: str = "2026",
    estado: str = "",
    empresa: str = "",
    search: str = "",
):
    docs = pdb.get_payments_export(periodo, estado, empresa, search)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"Cobranzas {periodo}"

    headers = ["ID Socio", "Apellidos", "Nombres", "Centro de Trabajo", "Email",
               "Período", "Estado", "Empresa", "Pagado por", "Fecha Pago", "Medio Pago", "Cuotas"]
    header_fill = PatternFill("solid", fgColor="1E3A5F")
    header_font = Font(bold=True, color="FFFFFF")
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")

    STATUS_ES = {
        "pagado": "Pagado", "debe": "Debe", "fraccionamiento": "Fraccionamiento",
        "exonerado": "Exonerado", "no_aplica": "N/A", "pendiente": "Pendiente",
        "retirar": "Retirar", "en_revision": "En revisión", "revisar": "Revisar",
    }
    for row, d in enumerate(docs, 2):
        cuotas = d.get("cuotas", [])
        cuotas_pagadas = sum(1 for c in cuotas if c.get("estado") == "pagado")
        cuotas_str = f"{cuotas_pagadas}/{len(cuotas)} pagadas" if cuotas else ""
        ws.cell(row=row, column=1,  value=d.get("member_id", ""))
        ws.cell(row=row, column=2,  value=d.get("apellidos", ""))
        ws.cell(row=row, column=3,  value=d.get("nombres", ""))
        ws.cell(row=row, column=4,  value=d.get("centro_trabajo", ""))
        ws.cell(row=row, column=5,  value=d.get("email_principal", ""))
        ws.cell(row=row, column=6,  value=d.get("periodo", ""))
        ws.cell(row=row, column=7,  value=STATUS_ES.get(d.get("estado", ""), d.get("estado", "")))
        ws.cell(row=row, column=8,  value=d.get("empresa_pagadora") or "")
        ws.cell(row=row, column=9,  value=d.get("pagado_por") or "")
        ws.cell(row=row, column=10, value=d.get("fecha_pago") or "")
        ws.cell(row=row, column=11, value=d.get("medio_pago") or "")
        ws.cell(row=row, column=12, value=cuotas_str)

    for col in ws.columns:
        max_len = max((len(str(c.value or "")) for c in col), default=10)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 40)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    filename = f"cobranzas_{periodo}"
    if estado:
        filename += f"_{estado}"
    filename += ".xlsx"

    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/billing/{payment_id}/update")
async def update_payment(
    payment_id: str,
    estado: str = Form(...),
    empresa: str = Form(""),
    fecha_pago: str = Form(""),
    medio: str = Form(""),
    pagado_por: str = Form(""),
    num_comprobante: str = Form(""),
    tipo_comprobante: str = Form(""),
    link_constancia: str = Form(""),
    banco_origen: str = Form(""),
    comprobante_emitido: str = Form(""),
    redirect_to: str = Form("/billing"),
):
    comp_emit = True if comprobante_emitido == "true" else (False if comprobante_emitido == "false" else None)
    pdb.update_payment(payment_id, estado, empresa or None, fecha_pago or None,
                       medio or None, pagado_por or None, num_comprobante or None,
                       tipo_comprobante or None, link_constancia or None,
                       banco_origen or None, comp_emit)
    return RedirectResponse(redirect_to, status_code=303)


@app.post("/billing/{payment_id}/cuotas/add")
async def add_cuota(
    payment_id: str,
    monto: float = Form(...),
    fecha_venc: str = Form(""),
    redirect_to: str = Form("/billing"),
):
    pdb.add_cuota(payment_id, monto, fecha_venc or None)
    return RedirectResponse(redirect_to, status_code=303)


@app.post("/billing/{payment_id}/cuotas/{numero}/update")
async def update_cuota(
    payment_id: str,
    numero: int,
    estado: str = Form(...),
    fecha_pago: str = Form(""),
    medio: str = Form(""),
    num_comprobante: str = Form(""),
    tipo_comprobante: str = Form(""),
    link_constancia: str = Form(""),
    banco_origen: str = Form(""),
    redirect_to: str = Form("/billing"),
):
    pdb.update_cuota(payment_id, numero, estado, fecha_pago or None,
                     medio or None, num_comprobante or None,
                     tipo_comprobante or None, link_constancia or None,
                     banco_origen or None)
    return RedirectResponse(redirect_to, status_code=303)


@app.post("/billing/{payment_id}/cuotas/{numero}/delete")
async def delete_cuota(
    payment_id: str,
    numero: int,
    redirect_to: str = Form("/billing"),
):
    pdb.delete_cuota(payment_id, numero)
    return RedirectResponse(redirect_to, status_code=303)


@app.post("/billing/{payment_id}/parciales/init")
async def init_parcial(
    payment_id: str,
    monto_total: float = Form(...),
    redirect_to: str = Form("/billing"),
):
    pdb.set_monto_total(payment_id, monto_total)
    return RedirectResponse(redirect_to, status_code=303)


@app.post("/billing/{payment_id}/parciales/add")
async def add_pago_parcial(
    payment_id: str,
    monto: float = Form(...),
    fecha_pago: str = Form(""),
    medio: str = Form(""),
    num_comprobante: str = Form(""),
    tipo_comprobante: str = Form(""),
    link_constancia: str = Form(""),
    banco_origen: str = Form(""),
    redirect_to: str = Form("/billing"),
):
    pdb.add_pago_parcial(payment_id, monto, fecha_pago or None, medio or None,
                         num_comprobante or None, tipo_comprobante or None,
                         link_constancia or None, banco_origen or None)
    return RedirectResponse(redirect_to, status_code=303)


@app.post("/billing/{payment_id}/parciales/{numero}/update")
async def update_pago_parcial(
    payment_id: str,
    numero: int,
    monto: float = Form(...),
    fecha_pago: str = Form(""),
    medio: str = Form(""),
    num_comprobante: str = Form(""),
    tipo_comprobante: str = Form(""),
    link_constancia: str = Form(""),
    banco_origen: str = Form(""),
    redirect_to: str = Form("/billing"),
):
    pdb.update_pago_parcial(payment_id, numero, monto, fecha_pago or None,
                            medio or None, num_comprobante or None,
                            tipo_comprobante or None, link_constancia or None,
                            banco_origen or None)
    return RedirectResponse(redirect_to, status_code=303)


@app.post("/billing/{payment_id}/parciales/{numero}/delete")
async def delete_pago_parcial(
    payment_id: str,
    numero: int,
    redirect_to: str = Form("/billing"),
):
    pdb.delete_pago_parcial(payment_id, numero)
    return RedirectResponse(redirect_to, status_code=303)


# ── FAQs ──────────────────────────────────────────────────────────────────────

@app.get("/faqs", response_class=HTMLResponse)
async def faqs_list(request: Request, search: str = "", category: str = ""):
    docs = pdb.get_faqs(search, category)
    categories = pdb.get_faq_categories()
    return templates.TemplateResponse(request, "faqs.html", _ctx(
        faqs=docs, categories=categories,
        search=search, category=category,
    ))


@app.post("/faqs/add")
async def add_faq(
    question: str = Form(...),
    answer: str = Form(...),
    category: str = Form(...),
):
    pdb.save_faq(question, answer, category)
    return RedirectResponse("/faqs", status_code=303)


@app.post("/faqs/{faq_id}/delete")
async def delete_faq(faq_id: str):
    pdb.delete_faq(faq_id)
    return RedirectResponse("/faqs", status_code=303)


# ── API (para uso del bot) ────────────────────────────────────────────────────

@app.get("/api/members")
async def api_members(search: str = "", estado: str = "", pago: str = ""):
    docs, total = pdb.get_members(search, estado, pago)
    return {"total": total, "members": docs}


@app.get("/api/members/{member_id}")
async def api_member(member_id: str):
    doc = pdb.get_member(member_id)
    return doc or JSONResponse({"error": "not found"}, status_code=404)


@app.get("/api/payments")
async def api_payments(periodo: str = "2026", estado: str = ""):
    docs, total = pdb.get_payments(periodo, estado)
    return {"total": total, "payments": docs}


# ── Portal de socios ──────────────────────────────────────────────────────────
@app.get("/api/portal/member-status")
async def portal_member_status(
    request: Request,
    email: str = Query(...),
):
    from fastapi import Header as _Header
    from config.settings import PORTAL_SECRET
    auth = request.headers.get("authorization", "")
    if PORTAL_SECRET and auth != f"Bearer {PORTAL_SECRET}":
        return JSONResponse({"error": "No autorizado"}, status_code=401)
    if not email or "@" not in email:
        return JSONResponse({"error": "Email inválido"}, status_code=400)
    return portal_routes.build_member_status(email)


@app.post("/webhook/woocommerce/order")
async def portal_wc_webhook(request: Request):
    return await portal_routes.handle_wc_webhook(request)
