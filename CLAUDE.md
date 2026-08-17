# IPIDET — Guía para agentes de IA

Proyecto de automatización para la administración de IPIDET (asociación profesional peruana). Incluye un agente de email, un bot de Telegram, una web admin y una base de datos de socios.

## Cómo levantar los servicios

```bash
# Web admin (FastAPI en http://localhost:8000)
python platform_main.py

# Agente de email (loop continuo, revisa Gmail cada 60s)
python main.py

# Importar padrón desde Excel a MongoDB
python import_padron.py
```

El servidor de producción corre en Render.com (auto-deploy desde GitHub `main`). El archivo `passenger_wsgi.py` es para entornos cPanel/Passenger y no se usa en Render.

---

## Arquitectura general

```
main.py              — Agente de email: loop principal, orquesta todo
platform_main.py     — Servidor web admin (uvicorn + FastAPI)
import_padron.py     — Importación Excel → MongoDB (one-shot, destructivo)
export_mongo.py      — Exporta colecciones a JSON
import_to_atlas.py   — Migración local → Atlas

config/
  settings.py        — Todas las variables de entorno centralizadas

gmail/
  client.py          — IMAP/SMTP con Gmail (polling por UID)
  auth.py            — Configuración de autenticación

classifier/
  engine.py          — Clasifica intención de emails con Claude

workflows/
  incoming.py        — Procesa emails entrantes, decide acción
  approvals.py       — Cola de aprobaciones manuales vía Telegram
  event_manager.py   — Gestión de eventos del agente
  learning.py        — Aprende respuestas aprobadas → guarda como FAQ

billing/
  db.py              — Consultas MongoDB de cobranzas (uso del agente)
  reminders.py       — Scheduler: recordatorios y alertas de morosos
  sheets.py          — Sync con Google Sheets

knowledge_base/
  db.py              — CRUD de FAQs en MongoDB
  faq_loader.py      — Carga FAQs desde YAML → MongoDB
  faqs/              — Archivos YAML: inscripciones, eventos, membresías

telegram_bot/
  notifications.py   — Envío de alertas al admin vía Telegram

portal/
  db.py              — (módulo legado, no en uso activo)
  router.py          — (módulo legado, no en uso activo)

webapp/
  app.py             — Rutas FastAPI (dashboard, socios, cobranzas, FAQs, portal)
  db.py              — Consultas MongoDB para la web admin
  portal_db.py       — Consultas para el portal de socios + lógica WooCommerce
  portal_router.py   — Handlers del portal: build_member_status, handle_wc_webhook
  templates/
    base.html        — Layout base: sidebar, topbar, Tailwind, Alpine.js, FontAwesome
    dashboard.html   — Stats generales
    members.html     — Padrón paginado con filtros
    member.html      — Detalle de socio: emails, notas, pagos
    billing.html     — Cobranzas: tabla, edición inline, cuotas, pagos parciales
    faqs.html        — Lista y gestión de FAQs

admin.py             — Script de administración CLI (utilidades)
telegram_main.py     — Punto de entrada del bot de Telegram (independiente)
debug_imap.py        — Herramienta de diagnóstico IMAP
launcher.py          — Lanzador de procesos
```

---

## Stack tecnológico — Frontend

- **Tailwind CSS** (CDN) — estilos utility-first
- **Alpine.js 3** (CDN) — reactividad declarativa en templates
- **Jinja2** — renderizado server-side (FastAPI)
- **FontAwesome 6** — íconos

### Patrones Alpine.js críticos

**Scope de x-data en tablas:** Alpine.js 3 solo comparte estado con descendientes (hijos), no con hermanos. En `billing.html`, cada pago usa un `<tbody x-data="{ editing: false, ... }">` propio que envuelve dos `<tr>`: el de datos y el de edición. Múltiples `<tbody>` por tabla es HTML5 válido.

**Formularios con auto-submit:** Usar `x-ref="filterForm"` en el `<form>` y `$refs.filterForm.submit()` en los handlers. **Nunca** `this.$el.submit()` — Alpine.js 3 envuelve `$el` en un Proxy que no expone `.submit()`.

**Debounce en inputs de texto:** 450ms con `clearTimeout/setTimeout`. Selects usan `@change` directo (sin debounce).

**Re-focus tras recarga:** En inputs de búsqueda: `x-init="if ($el.value) { $el.focus(); $el.setSelectionRange($el.value.length, $el.value.length) }"`.

---

## Base de datos (MongoDB)

Base: `ipidet_agent` — URI en `.env` como `MONGODB_URI`. En Atlas, el cliente usa `tlsCAFile=certifi.where()`.

Colecciones: `members`, `payments`, `faqs`, `events`.

---

### Colección `members`

```
member_id        string    "IPIDET-0294" — del campo "No. REGISTRO IPIDET" del Excel
apellidos        string
nombres          string
titulo           string    normalizado: "Contador" | "Abogado" | "Economista" | etc.
centro_trabajo   string
celular          string
ubicacion        string    normalizado: "Lima" | "La Libertad" | "Arequipa" | etc.
fecha_ingreso    datetime | null
fecha_nacimiento datetime | null
estado           string    "activo" | "retirar"
emails           [{
                   email:    string,
                   estado:   "habilitado" | "inhabilitado",
                   principal: bool
                 }]
notas            string    comentarios del Excel unidos con " | "
```

---

### Colección `payments`

Un documento por socio por período.

```
member_id          string    FK → members.member_id
periodo            string    "2025" | "2026"
estado             string    "pagado" | "debe" | "fraccionamiento" | "parcial"
                             | "exonerado" | "no_aplica" | "pendiente"
                             | "retirar" | "en_revision" | "revisar"
empresa_pagadora   string | null    "EY" | "BDO" | "PWC" | "KPMG" | "PPU"
pagado_por         string | null    texto libre o "WC#<order_id>" si vino de WooCommerce
fecha_pago         string | null    "YYYY-MM-DD"
medio_pago         string | null    "Transferencia" | "Efectivo" | "WooCommerce" | etc.
num_comprobante    string | null    número de boleta/factura del pago principal
tipo_comprobante   string | null    "boleta" | "factura" | "recibo"
link_constancia    string | null    URL al correo o documento de constancia del pago
raw_original       string           valor original del Excel (solo importación)

# Fraccionamiento — solo si estado == "fraccionamiento"
cuotas             [{
                     numero:           int       (auto-incremental dentro del pago)
                     monto:            float
                     fecha_venc:       string | null   "YYYY-MM-DD"
                     fecha_pago:       string | null   "YYYY-MM-DD"
                     estado:           "pendiente" | "pagado"
                     medio_pago:       string | null
                     num_comprobante:  string | null
                     tipo_comprobante: string | null   "boleta" | "factura" | "recibo"
                     link_constancia:  string | null
                   }]

# Pagos parciales — solo si estado == "parcial"
monto_total        float | null      deuda total declarada manualmente
pagos_parciales    [{
                     numero:           int       (auto-incremental)
                     monto:            float
                     fecha_pago:       string | null
                     medio_pago:       string | null
                     num_comprobante:  string | null
                     tipo_comprobante: string | null   "boleta" | "factura" | "recibo"
                     link_constancia:  string | null
                   }]
```

**Campos calculados** (no en BD, añadidos por `webapp/db.py::get_payments()`):
- `cuotas_total` / `cuotas_pagadas` — conteo de cuotas
- `monto_pagado` — suma de pagos_parciales[].monto
- `monto_pendiente` — max(0, monto_total - monto_pagado)
- `nombre_completo` — join de apellidos + nombres del miembro
- `email_principal` — primer email habilitado del miembro

**Auto-sync de estado** (en `webapp/db.py`):
- `_sync_estado_from_cuotas()` — si todas las cuotas pagadas → "pagado", si no → "fraccionamiento"
- `_sync_estado_from_parciales()` — si monto_pagado >= monto_total → "pagado", si no hay pagos → "debe", si hay → "parcial"

---

### Colección `faqs`

```
question    string
answer      string
category    string
active      bool      false = soft delete
times_used  int
created_at  datetime
```

### Colección `events`

Usada por el agente de email para aprobaciones pendientes de Telegram y otros eventos del sistema.

---

## Variables de entorno (.env)

```
# Claude / IA
ANTHROPIC_API_KEY           — API de Claude

# Telegram
TELEGRAM_BOT_TOKEN          — Token del bot admin
TELEGRAM_CHAT_ID            — ID del chat del administrador

# MongoDB
MONGODB_URI                 — URI completa (default: mongodb://localhost:27017)

# Gmail
GMAIL_ADDRESS               — Cuenta Gmail del agente
GMAIL_APP_PASSWORD          — App password (no la contraseña normal)

# Comportamiento del agente
ADMIN_FORWARD_EMAIL         — administracion@ipidet.org
CONFIDENCE_THRESHOLD        — 0.85 (umbral para auto-responder sin pedir aprobación)
AUTO_APPROVE_CONFIDENCE     — 0.88 (umbral para aprobar automáticamente)
CHECK_INTERVAL_SECONDS      — 60 (frecuencia de polling IMAP)
CLARIFICATION_TIMEOUT_HOURS — 24

# Cobranzas / Google
GOOGLE_CREDENTIALS_PATH     — google_credentials.json
BILLING_SHEET_ID            — ID de la hoja de Google Sheets de cobranzas
BILLING_REMINDER_DAYS       — 7
BILLING_OVERDUE_ALERT_HOURS — 24
BILLING_CHECK_INTERVAL_HOURS — 6

# Portal de socios (WordPress ↔ FastAPI)
PORTAL_SECRET               — secret Bearer para autenticar llamadas WP→FastAPI
WC_WEBHOOK_SECRET           — secret HMAC-SHA256 del webhook WooCommerce
PORTAL_API_BASE             — URL pública de FastAPI (para pruebas locales)
```

---

## Web admin — rutas completas

### Dashboard y socios

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/` | Dashboard: stats (activos, pagados, deben, FAQs) |
| `GET` | `/members` | Padrón paginado; filtros: search, estado, pago, ubicacion |
| `GET` | `/members/{id}` | Detalle: emails, notas, estado, historial de pagos |
| `POST` | `/members/{id}/emails/toggle` | Habilitar/inhabilitar un email |
| `POST` | `/members/{id}/emails/add` | Agregar nuevo email |
| `POST` | `/members/{id}/notes` | Actualizar campo notas |
| `POST` | `/members/{id}/estado` | Cambiar estado del socio |

### Cobranzas

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/billing` | Cobranzas paginadas; filtros: periodo, estado, empresa, search |
| `GET` | `/billing/export` | Descarga Excel con los pagos filtrados |
| `POST` | `/billing/{id}/update` | Actualiza el pago principal (estado, fecha, empresa, comprobante, etc.) |
| `POST` | `/billing/{id}/cuotas/add` | Agrega cuota al fraccionamiento |
| `POST` | `/billing/{id}/cuotas/{n}/update` | Actualiza cuota (marcar pagada o deshacer) |
| `POST` | `/billing/{id}/cuotas/{n}/delete` | Elimina cuota |
| `POST` | `/billing/{id}/parciales/init` | Define o actualiza el monto total del pago parcial |
| `POST` | `/billing/{id}/parciales/add` | Registra un pago parcial |
| `POST` | `/billing/{id}/parciales/{n}/delete` | Elimina un pago parcial |

### FAQs

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/faqs` | Lista de FAQs activas; filtros: search, category |
| `POST` | `/faqs/add` | Nueva FAQ |
| `POST` | `/faqs/{id}/delete` | Soft delete (active = false) |

### API JSON (para el agente de email y el bot)

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/api/members` | Lista de socios en JSON; filtros: search, estado, pago |
| `GET` | `/api/members/{id}` | Detalle de socio en JSON |
| `GET` | `/api/payments` | Pagos en JSON; filtros: periodo, estado |

### Portal de socios (WordPress ↔ FastAPI)

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/api/portal/member-status?email=X` | Devuelve estado del socio para el portal WP; requiere header `Authorization: Bearer <PORTAL_SECRET>` |
| `POST` | `/webhook/woocommerce/order` | Recibe pedido WC completado, actualiza MongoDB; verifica firma HMAC-SHA256 con `WC_WEBHOOK_SECRET` |

**Productos WooCommerce mapeados** (en `webapp/portal_db.py::WC_PRODUCT_MAP`):
- `8882` → pago ordinario completo
- `19105` → cuota anual provincia
- `8880` → fraccionamiento (registra una cuota)

---

## webapp/db.py — funciones clave

```python
get_stats()                           → dict con conteos del dashboard
get_members(search, estado, pago, ubicacion, page)   → (docs[], total)
get_member(member_id)                 → dict | None (incluye historial de pagos)
update_email_status(member_id, email, estado)
add_email(member_id, email)
update_member_notes(member_id, notas)
update_member_estado(member_id, estado)

get_payments(periodo, estado, empresa, search, page) → (docs[], total)
  # Cada doc incluye campos calculados: cuotas_total, cuotas_pagadas,
  # monto_pagado, monto_pendiente, nombre_completo, email_principal
get_payments_export(periodo, estado, empresa, search) → docs[] (sin paginación)
update_payment(payment_id, estado, empresa, fecha_pago, medio, pagado_por,
               num_comprobante, tipo_comprobante, link_constancia)

add_cuota(payment_id, monto, fecha_venc)
update_cuota(payment_id, numero, estado, fecha_pago, medio_pago,
             num_comprobante, tipo_comprobante, link_constancia)
delete_cuota(payment_id, numero)

set_monto_total(payment_id, monto_total)
add_pago_parcial(payment_id, monto, fecha_pago, medio, num_comprobante,
                 tipo_comprobante, link_constancia)
delete_pago_parcial(payment_id, numero)

get_faqs(search, category)            → docs[]
get_faq_categories()                  → list[str]
save_faq(question, answer, category)
delete_faq(faq_id)
```

---

## Flujo del agente de email

1. `gmail/client.py` — polling IMAP cada 60s, obtiene emails nuevos por UID
2. `classifier/engine.py` — Claude clasifica intención del email (confianza 0–1)
3. `workflows/incoming.py` — decide: responder automáticamente (> `CONFIDENCE_THRESHOLD`), escalar a admin o ignorar
4. `workflows/approvals.py` — si confianza baja, Telegram pide aprobación al admin
5. Respuesta aprobada → `workflows/learning.py` → guarda como FAQ si es recurrente

---

## Portal de socios (WordPress / WooCommerce)

Flujo server-to-server: WordPress llama a FastAPI para saber si un email es socio activo antes de dar acceso al contenido del portal.

1. WP hace `GET /api/portal/member-status?email=X` con header `Authorization: Bearer <PORTAL_SECRET>`
2. FastAPI busca el socio por email en MongoDB y devuelve su estado + historial de pagos
3. Cuando un socio paga en WooCommerce, WC envía `POST /webhook/woocommerce/order`
4. FastAPI verifica la firma HMAC-SHA256, identifica el producto, y actualiza el pago en MongoDB

---

## Importación del padrón

`import_padron.py` lee el Excel (columnas fijas del padrón IPIDET), hace **drop de `members` y `payments`** y reimporta todo. Ejecutar con cuidado — es destructivo.

Ruta hardcodeada del Excel: `C:\Users\Juan\Downloads\PADRÓN - JULIO.xlsx`. Actualizar si cambia el archivo.

Normalización que aplica:
- `member_id` ← `"IPIDET-" + No_REGISTRO`. Sin número válido → `"IPIDET-?"`.
- Emails separados por `;` o `,` → array; el primero queda `principal: true`.
- Estado de pago desde texto libre: "PAGADO EY" → `estado: "pagado", empresa: "EY"`.
- Ubicaciones y títulos se normalizan a valores canónicos.

---

## Decisiones de diseño relevantes

- **Múltiples `<tbody>` por tabla:** En `billing.html` cada pago usa su propio `<tbody x-data="...">` para poder tener estado Alpine.js compartido entre el `<tr>` de datos y el `<tr>` de edición (hermanos en HTML, hijos del mismo tbody). Alpine.js solo comparte estado hacia abajo (hijos), no entre hermanos.
- **`_sync_estado_from_cuotas` y `_sync_estado_from_parciales`:** Cada vez que se modifica una cuota o un pago parcial, estas funciones recalculan el estado general del pago (`pagado`, `fraccionamiento`, `parcial`, `debe`). No hay que actualizarlo manualmente.
- **Soft delete en FAQs:** El campo `active: false` desactiva la FAQ sin borrarla, para mantener historial de aprendizaje del agente.
- **Emails inhabilitados:** El agente de email consulta solo emails con `estado: "habilitado"`. Inhabilitar es reversible y no borra el email.
- **Portal secret vs WC secret:** Son dos secrets distintos. `PORTAL_SECRET` autentica las llamadas GET de WordPress. `WC_WEBHOOK_SECRET` firma los POST de WooCommerce con HMAC-SHA256.
- **certifi en Atlas:** La conexión a MongoDB Atlas requiere `tlsCAFile=certifi.where()` para que funcione en entornos sin CA bundle del sistema (Render, etc.).
