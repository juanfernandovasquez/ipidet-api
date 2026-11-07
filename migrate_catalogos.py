"""
migrate_catalogos.py — Lleva a MongoDB los datos que antes estaban fijos en el código.

Qué hace (idempotente, seguro correr varias veces):
  1. config: crea los catálogos y los datos de la organización que aún no existan.
     Nunca pisa lo que ya se haya editado desde /configuracion.
  2. productos: agrega los productos de WooCommerce que faltan (cuotas 2027 y los dos
     genéricos), verificados contra la API de WC el 2026-10-31.
  3. productos: quita el ID 8882 de "Cuota anual Lima 2024" (inactivo): ese ID es el
     producto genérico «Pago Ordinario», no el de 2024.
  4. payments: limpia empresa_pagadora con el texto literal "None".

También sirve para inicializar una base nueva. Uso:
  python migrate_catalogos.py          # aplica
  python migrate_catalogos.py --dry    # solo muestra qué haría
"""
import sys
from datetime import datetime, timezone

from config.mongo_client import db

DRY = "--dry" in sys.argv

CATALOGOS = {
    "medios_pago": ["Transferencia bancaria", "Depósito bancario", "Efectivo", "Yape / Plin",
                    "Niubiz", "WooCommerce", "Cheque", "Otro"],
    "bancos": ["BCP", "Interbank", "BBVA", "Scotiabank", "BanBif", "Banco de la Nación", "Otro"],
    "tipos_socio": [
        {"valor": "ordinario",  "label": "Ordinario"},
        {"valor": "filial",     "label": "Filial"},
        {"valor": "afiliado",   "label": "Afiliado"},
        {"valor": "honorario",  "label": "Honorario"},
        {"valor": "asociado",   "label": "Asociado"},
        {"valor": "jubilado",   "label": "Jubilado"},
        {"valor": "estudiante", "label": "Estudiante"},
        {"valor": "retirado",   "label": "Retirado"},
    ],
    "tipos_empresa": [
        {"valor": "auditora",    "label": "Auditora"},
        {"valor": "firma_legal", "label": "Firma legal"},
        {"valor": "empresa",     "label": "Empresa"},
        {"valor": "otro",        "label": "Otro"},
    ],
    "checklist_evento_pre": [
        {"id": "flyer",          "label": "Afiche / flyer diseñado"},
        {"id": "inscripciones",  "label": "Página de inscripción activa"},
        {"id": "zoom_config",    "label": "Sesión Zoom / plataforma configurada"},
        {"id": "invit_socios",   "label": "Invitaciones enviadas a socios"},
        {"id": "invit_publico",  "label": "Invitaciones enviadas a público general"},
        {"id": "material",       "label": "Material de presentación recibido"},
        {"id": "confirmaciones", "label": "Confirmaciones de asistencia enviadas"},
        {"id": "recordatorio",   "label": "Recordatorio enviado (24h antes)"},
        {"id": "prueba_tecnica", "label": "Prueba técnica realizada"},
    ],
    "checklist_evento_post": [
        {"id": "asistencia",     "label": "Asistencia marcada en plataforma"},
        {"id": "grabacion",      "label": "Grabación disponible"},
        {"id": "link_grabacion", "label": "Link de grabación compartido"},
        {"id": "certificados",   "label": "Certificados de participación enviados"},
        {"id": "agradecimiento", "label": "Email de agradecimiento enviado"},
        {"id": "rrss",           "label": "Post publicado en redes sociales"},
        {"id": "memoria",        "label": "Memoria / resumen del evento redactado"},
    ],
    "tipos_institucion": [
        {"valor": "universidad",    "label": "Universidad"},
        {"valor": "investigacion",  "label": "Centro de investigación"},
        {"valor": "organismo",      "label": "Organismo internacional"},
        {"valor": "instituto_fiscal", "label": "Instituto o asociación fiscal"},
        {"valor": "colegio_profesional", "label": "Colegio profesional"},
        {"valor": "gremio",         "label": "Gremio empresarial"},
        {"valor": "publica",        "label": "Entidad pública"},
        {"valor": "firma",          "label": "Firma profesional"},
        {"valor": "otra",           "label": "Otra"},
    ],
}

# Punto de partida de la búsqueda de alianzas. Se edita en /alianzas.
ALIANZAS_CONFIG = {
    "perfil": (
        "Instituto Peruano de Investigación y Desarrollo Tributario (IPIDET): asociación peruana que reúne a "
        "profesionales del ámbito tributario, como contadores, abogados y economistas, en Lima y provincias. "
        "Promueve la investigación, el análisis y la difusión del derecho tributario peruano e internacional, "
        "organiza eventos académicos y capacitaciones, y mantiene una membresía anual de socios."
    ),
    "criterios": (
        "Priorizar instituciones que ayuden a IPIDET a: producir investigación tributaria de mayor alcance, "
        "organizar eventos conjuntos con expositores de nivel, ofrecer beneficios concretos a los socios, "
        "atraer nuevos socios en Lima y provincias, y proyectarse en la red tributaria latinoamericana e "
        "internacional. Cuidar la independencia técnica de IPIDET: señalar cualquier posible conflicto de "
        "interés, en especial con entidades que fiscalizan o con firmas que compiten por los mismos socios."
    ),
}

# Punto de partida de la búsqueda de ponentes. Se edita en /ponentes.
PONENTES_CONFIG = {
    "publico": (
        "Profesionales del ámbito tributario en el Perú, en su mayoría contadores, además de abogados "
        "tributaristas y economistas. Muchos trabajan en firmas de auditoría y consultoría, en áreas "
        "tributarias de empresas y en la administración tributaria. También asiste público general a los "
        "webinars de actualización. Buscan exposiciones técnicas y aplicadas, con casos y novedades normativas."
    ),
    "temas": (
        "Precios de transferencia, NIIF y su efecto tributario, impuesto mínimo global (Pilar 2), tributación "
        "internacional y convenios para evitar la doble imposición, fiscalización y litigio ante la "
        "administración tributaria y el Tribunal Fiscal, responsabilidad penal tributaria."
    ),
}

ORGANIZACION = {
    "email": "administracion@ipidet.org",
    "web": "www.ipidet.org",
    "telefono": "(01) 422-6048",
    "ciudad": "Lima, Perú",
    "firmante_nombre": "KATARZYNA DUNIN BORKOWSKI",
    "firmante_cargo": "Presidenta del Consejo Directivo",
}

# Genéricos: `activo: False` los deja fuera de los selectores de facturación y del
# portal, pero el webhook los sigue reconociendo (se venden en la tienda).
PRODUCTOS_WC = [
    {"wc_product_id": 19363, "nombre": "Cuota anual Lima 2027", "tipo": "cuota_anual", "precio": 780.0,
     "periodo": "2027", "activo": True, "codigo_sunat": "CUOTA-2027",
     "descripcion": "Membresía ordinaria Lima — período 2027"},
    {"wc_product_id": 19362, "nombre": "Cuota anual provincia 2027", "tipo": "cuota_provincia", "precio": 350.0,
     "periodo": "2027", "activo": True, "codigo_sunat": "CUOTA-F-2027",
     "descripcion": "Membresía socios en provincia — período 2027"},
    {"wc_product_id": 8882, "nombre": "Pago Ordinario (genérico WC)", "tipo": "cuota_anual", "precio": 780.0,
     "periodo": "", "activo": False,
     "descripcion": "Producto genérico de la tienda: se aplica al período pendiente más antiguo del socio."},
    {"wc_product_id": 19105, "nombre": "Cuota anual provincia (genérico WC)", "tipo": "cuota_provincia", "precio": 350.0,
     "periodo": "", "activo": False,
     "descripcion": "Producto genérico de la tienda: se aplica al período pendiente más antiguo del socio."},
]


def _log(msg: str) -> None:
    print(("[dry] " if DRY else "") + msg)


def migrar_config() -> None:
    for key, valores in CATALOGOS.items():
        if db.config.find_one({"_id": key}):
            _log(f"config.{key}: ya existe, no se toca")
            continue
        _log(f"config.{key}: se crea con {len(valores)} valores")
        if not DRY:
            db.config.insert_one({"_id": key, "valores": valores})
    if db.config.find_one({"_id": "alianzas_config"}):
        _log("config.alianzas_config: ya existe, no se toca")
    else:
        _log("config.alianzas_config: se crea")
        if not DRY:
            db.config.insert_one({"_id": "alianzas_config", **ALIANZAS_CONFIG})
    _log("alianzas: índice único por nombre normalizado")
    if not DRY:
        db.alianzas.create_index("clave", unique=True)
    if db.config.find_one({"_id": "ponentes_config"}):
        _log("config.ponentes_config: ya existe, no se toca")
    else:
        _log("config.ponentes_config: se crea")
        if not DRY:
            db.config.insert_one({"_id": "ponentes_config", **PONENTES_CONFIG})
    _log("ponentes: índice único por nombre normalizado")
    if not DRY:
        db.ponentes.create_index("clave", unique=True)
    if db.config.find_one({"_id": "organizacion"}):
        _log("config.organizacion: ya existe, no se toca")
    else:
        _log("config.organizacion: se crea")
        if not DRY:
            db.config.insert_one({"_id": "organizacion", **ORGANIZACION})


def migrar_productos() -> None:
    quitar = db.productos.count_documents(
        {"tipo": "cuota_anual", "periodo": "2024", "wc_product_id": 8882}
    )
    _log(f"productos: Lima 2024 con ID 8882 a limpiar: {quitar}")
    if quitar and not DRY:
        db.productos.update_many(
            {"tipo": "cuota_anual", "periodo": "2024", "wc_product_id": 8882},
            {"$set": {"wc_product_id": None}},
        )
    for prod in PRODUCTOS_WC:
        if db.productos.find_one({"wc_product_id": prod["wc_product_id"], "periodo": prod["periodo"]}):
            _log(f"productos: WC #{prod['wc_product_id']} ya existe")
            continue
        _log(f"productos: se agrega WC #{prod['wc_product_id']} — {prod['nombre']}")
        if not DRY:
            db.productos.insert_one({**prod, "created_at": datetime.now(timezone.utc)})


def limpiar_empresa_none() -> None:
    filtro = {"empresa_pagadora": "None"}
    total = db.payments.count_documents(filtro)
    _log(f'payments: empresa_pagadora = "None" a limpiar: {total}')
    if total and not DRY:
        db.payments.update_many(filtro, {"$set": {"empresa_pagadora": None, "empresa_id": None}})


if __name__ == "__main__":
    migrar_config()
    migrar_productos()
    limpiar_empresa_none()
    _log("Listo.")
