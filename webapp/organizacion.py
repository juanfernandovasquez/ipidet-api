"""Sección IPIDET (/organizacion): datos de la institución, logo, firma y consejo directivo.

- Datos fiscales y de contacto: config.organizacion (catalogos.CAMPOS_FISCALES / CAMPOS_ORGANIZACION).
  Los usan los comprobantes (PDF y SUNAT), las constancias, los correos y el agente.
- Logo y firma: config.organizacion_logo / organizacion_firma (bytes). El logo es público (/org/logo)
  porque va en correos y en la web; la firma solo la ven usuarios con sesión.
- Consejo directivo: colección `consejo_directivo`, una fila por cargo y gestión, unida al padrón por
  `member_id` (R8): nombre, título, contacto y estado de pago salen siempre de `members` / `payments`.
"""
from datetime import date, datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from urllib.parse import urlencode

from config import catalogos
from config.mongo_client import db

router = APIRouter()
consejo_col = db.consejo_directivo

_TIPOS_IMAGEN = {"image/png": "png", "image/jpeg": "jpg"}
_MAX_IMAGEN = 1_000_000  # 1 MB


def _oid(x: str):
    return ObjectId(x) if ObjectId.is_valid(x or "") else None


def _hoy() -> str:
    return date.today().isoformat()


def es_vigente(fila: dict, hoy: str | None = None) -> bool:
    hoy = hoy or _hoy()
    return (fila.get("desde") or "") <= hoy and (not fila.get("hasta") or fila["hasta"] >= hoy)


def consejo(incluir_anteriores: bool = True) -> dict:
    """{vigente: [...], anteriores: [...]} con los datos del socio (de members) y su pago del período."""
    periodo = catalogos.get_periodo_actual()
    filas = list(consejo_col.find({}))
    ids = list({f["member_id"] for f in filas if f.get("member_id")})
    socios = {m["member_id"]: m for m in db.members.find(
        {"member_id": {"$in": ids}},
        {"member_id": 1, "apellidos": 1, "nombres": 1, "titulo": 1, "centro_trabajo": 1, "celular": 1,
         "emails": 1, "linkedin": 1, "estado": 1, "tipo_socio": 1, "fecha_ingreso": 1})}
    pagos = {p["member_id"]: p.get("estado") for p in db.payments.find(
        {"member_id": {"$in": ids}, "periodo": periodo}, {"member_id": 1, "estado": 1})}
    cargos = catalogos.get_catalogo("cargos_consejo")
    orden_cargo = {c: i for i, c in enumerate(cargos)}
    hoy = _hoy()
    vigente, anteriores = [], []
    for f in filas:
        m = socios.get(f.get("member_id")) or {}
        email = next((e["email"] for e in m.get("emails") or [] if e.get("principal") and e.get("estado") == "habilitado"),
                     next((e["email"] for e in m.get("emails") or [] if e.get("estado") == "habilitado"), ""))
        fila = {**f, "_id": str(f["_id"]),
                "nombre": f"{(m.get('nombres') or '').strip()} {(m.get('apellidos') or '').strip()}".strip() or f.get("member_id", ""),
                "titulo": m.get("titulo") or "", "centro_trabajo": m.get("centro_trabajo") or "",
                "email": email, "celular": m.get("celular") or "", "linkedin": m.get("linkedin") or "",
                "estado_socio": m.get("estado") or "", "existe_socio": bool(m),
                "pago_periodo": pagos.get(f.get("member_id"), "")}
        (vigente if es_vigente(f, hoy) else anteriores).append(fila)
    vigente.sort(key=lambda x: (orden_cargo.get(x.get("cargo"), 99), x.get("orden") or 0, x["nombre"]))
    anteriores.sort(key=lambda x: (x.get("hasta") or "", x.get("desde") or ""), reverse=True)
    return {"vigente": vigente, "anteriores": anteriores if incluir_anteriores else [], "periodo": periodo}


def cargos_de_socio(member_id: str) -> list[dict]:
    """Cargos del socio en el consejo (para su ficha), vigentes primero."""
    filas = list(consejo_col.find({"member_id": member_id}, {"cargo": 1, "desde": 1, "hasta": 1}))
    for f in filas:
        f["_id"] = str(f["_id"])
        f["vigente"] = es_vigente(f)
    return sorted(filas, key=lambda f: (not f["vigente"], f.get("desde") or ""), reverse=False)


def _volver(**q) -> RedirectResponse:
    return RedirectResponse("/organizacion" + ("?" + urlencode(q) if q else ""), status_code=303)


@router.get("/organizacion")
async def organizacion_page(request: Request):
    from webapp.app import templates, _ctx
    return templates.TemplateResponse(request, "organizacion.html", _ctx(
        request,
        org=catalogos.get_organizacion(),
        fiscales=catalogos.get_datos_fiscales(),
        campos_org=catalogos.CAMPOS_ORGANIZACION,
        campos_fiscales=catalogos.CAMPOS_FISCALES,
        tiene_logo=bool(catalogos.get_imagen_org("logo")),
        tiene_firma=bool(catalogos.get_imagen_org("firma")),
        consejo=consejo(),
        cargos=catalogos.get_catalogo("cargos_consejo"),
        hoy=_hoy(),
    ))


@router.post("/organizacion/datos")
async def organizacion_datos(request: Request):
    form = await request.form()
    campos = {k: form.get(k, "") for k in (*catalogos.CAMPOS_ORGANIZACION, *catalogos.CAMPOS_FISCALES) if k in form}
    if campos.get("ruc") and not (len(campos["ruc"]) == 11 and campos["ruc"].isdigit()):
        return _volver(error="El RUC debe tener 11 dígitos.")
    catalogos.set_organizacion(campos)
    return _volver(ok="Datos de IPIDET guardados.")


@router.post("/organizacion/imagen/{clave}")
async def organizacion_imagen(clave: str, archivo: UploadFile | None = File(None), quitar: str = Form("")):
    if clave not in ("logo", "firma"):
        return _volver(error="Imagen no válida.")
    if quitar:
        catalogos.set_imagen_org(clave, None)
        return _volver(ok="Se quitó la imagen: se usa la de siempre.")
    if not archivo or not archivo.filename:
        return _volver(error="Elige un archivo PNG o JPG.")
    datos = await archivo.read(_MAX_IMAGEN + 1)
    tipo = (archivo.content_type or "").lower()
    if tipo not in _TIPOS_IMAGEN or not (datos.startswith(b"\x89PNG") or datos.startswith(b"\xff\xd8")):
        return _volver(error="El archivo debe ser una imagen PNG o JPG.")
    if len(datos) > _MAX_IMAGEN:
        return _volver(error="La imagen pesa más de 1 MB.")
    catalogos.set_imagen_org(clave, datos, tipo)
    return _volver(ok="Logo actualizado." if clave == "logo" else "Firma actualizada.")


def _imagen(clave: str, por_defecto: str) -> Response:
    cargado = catalogos.get_imagen_org(clave)
    if cargado:
        return Response(cargado[0], media_type=cargado[1], headers={"Cache-Control": "private, max-age=300"})
    try:
        with open(por_defecto, "rb") as f:
            return Response(f.read(), media_type="image/png", headers={"Cache-Control": "private, max-age=300"})
    except OSError:
        return Response(status_code=404)


@router.get("/org/logo")
async def logo_publico():
    """Público: va en correos y páginas."""
    import os
    return _imagen("logo", os.path.join(os.path.dirname(__file__), "static", "logo.png"))


@router.get("/organizacion/firma")
async def firma_privada():
    """Solo con sesión: la firma no se publica."""
    import os
    return _imagen("firma", os.path.join(os.path.dirname(__file__), "..", "mockup_assets", "firma_presidenta.png"))


# ── Consejo directivo ──────────────────────────────────────────────────────────

def _fila_desde_form(member_id: str, cargo: str, desde: str, hasta: str, notas: str,
                     foto_url: str = "") -> dict | str:
    member_id = (member_id or "").strip().upper()
    if not db.members.find_one({"member_id": member_id}, {"_id": 1}):
        return f"No existe el socio {member_id or '(vacío)'}: elígelo del buscador."
    cargo = (cargo or "").strip()
    if not cargo:
        return "Falta el cargo."
    desde, hasta = (desde or "").strip(), (hasta or "").strip()
    if hasta and desde and hasta < desde:
        return "La fecha de fin es anterior a la de inicio."
    foto_url = (foto_url or "").strip()
    if foto_url and not foto_url.startswith("https://"):
        return "La foto debe ser un enlace https:// (por ejemplo, la foto publicada en ipidet.org)."
    return {"member_id": member_id, "cargo": cargo[:80], "desde": desde or _hoy(), "hasta": hasta,
            "notas": (notas or "").strip()[:500], "foto_url": foto_url[:500]}


@router.post("/organizacion/consejo/nuevo")
async def consejo_nuevo(request: Request, member_id: str = Form(""), cargo: str = Form(""),
                        desde: str = Form(""), hasta: str = Form(""), notas: str = Form(""),
                        foto_url: str = Form("")):
    fila = _fila_desde_form(member_id, cargo, desde, hasta, notas, foto_url)
    if isinstance(fila, str):
        return _volver(error=fila)
    ahora = datetime.now(timezone.utc)
    consejo_col.insert_one({**fila, "created_at": ahora, "updated_at": ahora,
                            "creado_por": request.session.get("user_email", "")})
    return _volver(ok="Agregado al consejo directivo.")


@router.post("/organizacion/consejo/{fila_id}/update")
async def consejo_update(fila_id: str, member_id: str = Form(""), cargo: str = Form(""),
                         desde: str = Form(""), hasta: str = Form(""), notas: str = Form(""),
                         foto_url: str = Form("")):
    fila = _fila_desde_form(member_id, cargo, desde, hasta, notas, foto_url)
    if isinstance(fila, str):
        return _volver(error=fila)
    consejo_col.update_one({"_id": _oid(fila_id)}, {"$set": {**fila, "updated_at": datetime.now(timezone.utc)}})
    return _volver(ok="Cargo actualizado.")


@router.post("/organizacion/consejo/{fila_id}/foto")
async def consejo_foto(fila_id: str, foto_url: str = Form("")):
    """Foto que muestra ipidet.org (enlace https). Vacío la quita."""
    foto_url = foto_url.strip()
    if foto_url and not foto_url.startswith("https://"):
        return _volver(error="La foto debe ser un enlace https://.")
    consejo_col.update_one({"_id": _oid(fila_id)}, {"$set": {"foto_url": foto_url[:500], "updated_at": datetime.now(timezone.utc)}})
    return _volver(ok="Foto actualizada.")


@router.post("/organizacion/consejo/{fila_id}/terminar")
async def consejo_terminar(fila_id: str, hasta: str = Form("")):
    consejo_col.update_one({"_id": _oid(fila_id)}, {"$set": {"hasta": (hasta or _hoy()).strip(),
                                                            "updated_at": datetime.now(timezone.utc)}})
    return _volver(ok="Cargo cerrado: pasa a gestiones anteriores.")


@router.post("/organizacion/consejo/{fila_id}/delete")
async def consejo_delete(fila_id: str):
    consejo_col.delete_one({"_id": _oid(fila_id)})
    return _volver(ok="Registro eliminado.")


@router.post("/organizacion/consejo/{fila_id}/firmante")
async def consejo_firmante(fila_id: str):
    """Usa a este directivo como firmante de constancias (nombre y cargo salen del padrón)."""
    f = consejo_col.find_one({"_id": _oid(fila_id)})
    m = db.members.find_one({"member_id": (f or {}).get("member_id")}, {"nombres": 1, "apellidos": 1, "titulo": 1})
    if not f or not m:
        return _volver(error="No se encontró al directivo.")
    nombre = f"{(m.get('nombres') or '').strip()} {(m.get('apellidos') or '').strip().title()}".strip()
    catalogos.set_organizacion({"firmante_nombre": nombre, "firmante_cargo": f["cargo"]})
    return _volver(ok=f"{nombre} firma ahora las constancias. Sube su firma en «Logo y firma».")
