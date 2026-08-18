import io
import re
from markupsafe import Markup, escape
from fastapi import FastAPI, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.types import ASGIApp, Scope, Receive, Send
from fastapi.templating import Jinja2Templates
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
import webapp.db as pdb
import webapp.portal_db as portal_db
import webapp.portal_router as portal_routes
import webapp.auth as auth
from config.settings import SECRET_KEY

app = FastAPI(title="IPIDET Admin")

# CORS: solo permite llamadas desde el servidor WordPress (server-to-server)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://ipidet.org"],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-WC-Webhook-Signature"],
)

_PUBLIC_PATHS = {"/login", "/logout"}
_PUBLIC_PREFIXES = ("/webhook/", "/api/portal/")

class _AuthMiddleware:
    """Middleware ASGI puro — compatible con SessionMiddleware sin interferir en la cookie."""
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path in _PUBLIC_PATHS or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            await self.app(scope, receive, send)
            return
        session = scope.get("session", {})
        if not session.get("user_email"):
            response = RedirectResponse(f"/login?next={path}", status_code=302)
            await response(scope, receive, send)
            return
        role = session.get("user_role", "viewer")
        if role != "admin":
            section = auth.section_for_path(path)
            if section == "__admin__":
                response = RedirectResponse("/", status_code=302)
                await response(scope, receive, send)
                return
            if section and section not in session.get("user_permisos", []):
                response = RedirectResponse("/?sin_acceso=1", status_code=302)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)

app.add_middleware(_AuthMiddleware)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, https_only=False, same_site="lax")
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

def _ctx(request: Request, **kwargs):
    return {
        "status_labels": STATUS_LABELS,
        "medios_pago": pdb.MEDIOS_PAGO,
        "bancos": pdb.BANCOS,
        "current_user_email":   request.session.get("user_email", ""),
        "current_user_role":    request.session.get("user_role", ""),
        "current_user_permisos":request.session.get("user_permisos", []),
        "secciones": auth.SECCIONES,
        **kwargs,
    }


@app.on_event("startup")
async def _startup():
    pdb.seed_companies()
    auth.seed_admin()


# ── Dashboard ─────────────────────────────────────────────────────────────────

_SECTION_PATHS = [
    ("members", "/members"), ("billing", "/billing"),
    ("fraccionamientos", "/fraccionamientos"), ("finanzas", "/finanzas"),
    ("faqs", "/faqs"), ("marketing", "/marketing"),
]

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    role     = request.session.get("user_role", "viewer")
    permisos = request.session.get("user_permisos", [])
    if role != "admin" and "dashboard" not in permisos:
        for sec, path in _SECTION_PATHS:
            if sec in permisos:
                return RedirectResponse(path, status_code=302)
        return HTMLResponse("<h2 style='font-family:sans-serif;padding:2rem'>Sin acceso asignado. Contacta al administrador.</h2>")
    stats = pdb.get_stats()
    return templates.TemplateResponse(request, "dashboard.html", _ctx(request, stats=stats))


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
    return templates.TemplateResponse(request, "members.html", _ctx(request,
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
    return templates.TemplateResponse(request, "member.html", _ctx(request, member=doc))


@app.post("/members/{member_id}/emails/toggle")
async def toggle_email(member_id: str, email: str = Form(...), estado: str = Form(...)):
    pdb.update_email_status(member_id, email, estado)
    return RedirectResponse(f"/members/{member_id}", status_code=303)


@app.post("/members/{member_id}/emails/set-principal")
async def set_email_principal(member_id: str, email: str = Form(...)):
    pdb.set_email_principal(member_id, email)
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


@app.post("/members/{member_id}/comentarios/add")
async def add_comentario(member_id: str, texto: str = Form(...)):
    if texto.strip():
        pdb.add_comentario(member_id, texto)
    return RedirectResponse(f"/members/{member_id}", status_code=303)


@app.post("/members/{member_id}/comentarios/{comentario_id}/delete")
async def delete_comentario(member_id: str, comentario_id: str):
    pdb.delete_comentario(member_id, comentario_id)
    return RedirectResponse(f"/members/{member_id}", status_code=303)


@app.post("/members/{member_id}/estado")
async def update_member_estado(member_id: str, estado: str = Form(...)):
    pdb.update_member_estado(member_id, estado)
    return RedirectResponse(f"/members/{member_id}", status_code=303)


# ── Cobranzas ─────────────────────────────────────────────────────────────────

@app.get("/billing/facturacion", response_class=HTMLResponse)
async def facturacion(
    request: Request,
    periodo: str = "2026",
    search: str = "",
):
    pendientes = pdb.get_comprobantes_pendientes(periodo, search)
    return templates.TemplateResponse(request, "facturacion.html", _ctx(request,
        pendientes=pendientes, periodo=periodo, search=search,
        total=len(pendientes),
    ))


@app.post("/billing/{payment_id}/emitir-comprobante")
async def emitir_comprobante(
    payment_id: str,
    tipo: str             = Form(...),
    numero: int | None    = Form(None),
    num_comprobante: str  = Form(...),
    tipo_comprobante: str = Form(""),
    fecha_emision: str    = Form(""),
    periodo: str          = Form("2026"),
    search: str           = Form(""),
):
    pdb.emitir_comprobante(payment_id, tipo, numero, num_comprobante.strip(),
                           tipo_comprobante, fecha_emision)
    return RedirectResponse(f"/billing/facturacion?periodo={periodo}&search={search}", status_code=303)

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
    docs, total = pdb.get_payments(periodo, estado, empresa, search, page, comprobante_emitido=comprobante_emitido)
    return templates.TemplateResponse(request, "billing.html", _ctx(request,
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
    fecha_emision_comprobante: str = Form(""),
    link_constancia: str = Form(""),
    banco_origen: str = Form(""),
    comprobante_emitido: str = Form(""),
    redirect_to: str = Form("/billing"),
):
    comp_emit = True if comprobante_emitido == "true" else (False if comprobante_emitido == "false" else None)
    pdb.update_payment(payment_id, estado, empresa or None, fecha_pago or None,
                       medio or None, pagado_por or None, num_comprobante or None,
                       tipo_comprobante or None, link_constancia or None,
                       banco_origen or None, comp_emit,
                       fecha_emision_comprobante or None)
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


# ── Marketing ─────────────────────────────────────────────────────────────────

@app.get("/marketing", response_class=HTMLResponse)
async def marketing(
    request: Request,
    titulo: str = "",
    ubicacion: str = "",
    estado_pago: str = "",
    empresa: str = "",
    periodo: str = "2026",
):
    stats = pdb.get_marketing_stats(periodo)
    emails = pdb.get_marketing_emails(titulo, ubicacion, estado_pago, empresa, periodo)
    titulos = sorted(set(t for t in pdb.members_col.distinct("titulo") if t))
    ubicaciones = sorted(set(u for u in pdb.members_col.distinct("ubicacion") if u))
    return templates.TemplateResponse(request, "marketing.html", _ctx(request,
        stats=stats, emails=emails,
        titulos=titulos, ubicaciones=ubicaciones,
        titulo=titulo, ubicacion=ubicacion,
        estado_pago=estado_pago, empresa=empresa, periodo=periodo,
    ))


@app.get("/marketing/export")
async def marketing_export(
    titulo: str = "",
    ubicacion: str = "",
    estado_pago: str = "",
    empresa: str = "",
    periodo: str = "2026",
):
    emails = pdb.get_marketing_emails(titulo, ubicacion, estado_pago, empresa, periodo)
    buf = io.StringIO()
    buf.write("Nombre,Email,Título,Ubicación,Centro de Trabajo,Estado Pago\n")
    for r in emails:
        nombre   = r["nombre"].replace('"', '""')
        email    = r["email"].replace('"', '""')
        tit      = r["titulo"].replace('"', '""')
        ubic     = r["ubicacion"].replace('"', '""')
        ct       = r["centro_trabajo"].replace('"', '""')
        ep       = r["estado_pago"].replace('"', '""')
        buf.write(f'"{nombre}","{email}","{tit}","{ubic}","{ct}","{ep}"\n')
    csv_bytes = buf.getvalue().encode("utf-8-sig")
    return StreamingResponse(
        io.BytesIO(csv_bytes),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=marketing_{periodo}.csv"},
    )


# ── Finanzas (mockup) ─────────────────────────────────────────────────────────

@app.get("/finanzas", response_class=HTMLResponse)
async def finanzas_mockup(request: Request):
    return templates.TemplateResponse(request, "finanzas_mockup.html", _ctx(request))


# ── Fraccionamientos ─────────────────────────────────────────────────────────

@app.get("/fraccionamientos", response_class=HTMLResponse)
async def fraccionamientos(
    request: Request,
    periodo: str = "2026",
    alerta: str = "",
    search: str = "",
    page: int = 1,
):
    docs, total, stats = pdb.get_fraccionamientos(periodo, alerta, search, page)
    return templates.TemplateResponse(request, "fraccionamientos.html", _ctx(request,
        fraccionamientos=docs, total=total, stats=stats,
        periodo=periodo, alerta=alerta, search=search,
        page=page, per_page=50,
        total_pages=max(1, (total + 49) // 50),
    ))


# ── FAQs ──────────────────────────────────────────────────────────────────────

@app.get("/faqs", response_class=HTMLResponse)
async def faqs_list(request: Request, search: str = "", category: str = ""):
    docs = pdb.get_faqs(search, category)
    categories = pdb.get_faq_categories()
    return templates.TemplateResponse(request, "faqs.html", _ctx(request,
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


# ── API empresas ─────────────────────────────────────────────────────────────

@app.get("/api/companies")
async def api_companies(q: str = ""):
    return pdb.get_companies(q)


@app.post("/api/companies")
async def api_add_company(nombre: str = Form(...), ruc: str = Form(""), tipo: str = Form("empresa")):
    pdb.add_company(nombre, ruc, tipo)
    return {"ok": True}


@app.delete("/api/companies/{company_id}")
async def api_delete_company(company_id: str):
    pdb.delete_company(company_id)
    return {"ok": True}


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
    from config.settings import PORTAL_SECRET
    auth_header = request.headers.get("authorization", "")
    if PORTAL_SECRET and auth_header != f"Bearer {PORTAL_SECRET}":
        return JSONResponse({"error": "No autorizado"}, status_code=401)
    if not email or "@" not in email:
        return JSONResponse({"error": "Email inválido"}, status_code=400)
    return portal_routes.build_member_status(email)


@app.post("/webhook/woocommerce/order")
async def portal_wc_webhook(request: Request):
    return await portal_routes.handle_wc_webhook(request)


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/", error: str = ""):
    if request.session.get("user_email"):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(request, "login.html", {"next": next, "error": error})


@app.post("/login")
async def login_post(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    user = auth.get_user(email)
    if not user or not auth.verify_password(password, user["password_hash"]):
        return templates.TemplateResponse(request, "login.html", {
            "next": next,
            "error": "Correo o contraseña incorrectos.",
        })
    request.session["user_email"]   = user["email"]
    request.session["user_role"]    = user["role"]
    request.session["user_permisos"]= user.get("permisos", auth.ALL_SECTIONS)
    return RedirectResponse(next if next.startswith("/") else "/", status_code=302)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


# ── Admin: gestión de usuarios ────────────────────────────────────────────────

@app.get("/admin/users", response_class=HTMLResponse)
async def admin_users(request: Request):
    if request.session.get("user_role") != "admin":
        return RedirectResponse("/", status_code=302)
    users = auth.list_users()
    return templates.TemplateResponse(request, "users.html", _ctx(request, users=users))


@app.post("/admin/users/add")
async def admin_add_user(request: Request):
    if request.session.get("user_role") != "admin":
        return RedirectResponse("/", status_code=302)
    form = await request.form()
    email    = form.get("email", "")
    password = form.get("password", "")
    role     = form.get("role", "viewer")
    permisos = form.getlist("permisos")
    if not permisos and role == "viewer":
        permisos = auth.ALL_SECTIONS
    auth.create_user(email, password, role, permisos)
    return RedirectResponse("/admin/users", status_code=303)


@app.post("/admin/users/{user_id}/permisos")
async def admin_update_permisos(request: Request, user_id: str):
    if request.session.get("user_role") != "admin":
        return RedirectResponse("/", status_code=302)
    form = await request.form()
    permisos = form.getlist("permisos")
    auth.update_user_permisos(user_id, permisos)
    return RedirectResponse("/admin/users", status_code=303)


@app.post("/admin/users/{user_id}/delete")
async def admin_delete_user(request: Request, user_id: str):
    if request.session.get("user_role") != "admin":
        return RedirectResponse("/", status_code=302)
    auth.delete_user(user_id)
    return RedirectResponse("/admin/users", status_code=303)
