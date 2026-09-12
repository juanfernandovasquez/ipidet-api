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

knowledge_base/
  db.py              — CRUD de FAQs en MongoDB
  faq_loader.py      — Carga FAQs desde YAML → MongoDB
  faqs/              — Archivos YAML: inscripciones, eventos, membresías

telegram_bot/
  notifications.py   — Envío de alertas al admin vía Telegram

webapp/
  app.py             — Rutas FastAPI (todas las páginas y APIs)
  db.py              — Consultas MongoDB para la web admin (~2200 líneas)
  auth.py            — Autenticación con bcrypt + sessions; colección users
  mailer.py          — Envío de emails via Brevo SMTP + historial de envíos
  portal_db.py       — Consultas para el portal de socios + lógica WooCommerce
  portal_router.py   — Handlers del portal: build_member_status, handle_wc_webhook
  scheduler.py       — Tareas periódicas (reminders, alertas)
  wc_client.py       — Cliente HTTP para la API REST de WooCommerce
  templates/         — Ver sección de templates abajo

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

**Scope de x-data en tablas:** Alpine.js 3 solo comparte estado con descendientes (hijos), no con hermanos. En `billing.html`, cada pago usa un `<tbody x-data='billingRow(...)'>` propio que envuelve dos `<tr>`: el de datos y el de edición. Múltiples `<tbody>` por tabla es HTML5 válido.

**Formularios con auto-submit:** Usar `x-ref="filterForm"` en el `<form>` y `$refs.filterForm.submit()` en los handlers. **Nunca** `this.$el.submit()` — Alpine.js 3 envuelve `$el` en un Proxy que no expone `.submit()`.

**Debounce en inputs de texto:** 450ms con `clearTimeout/setTimeout`. Selects usan `@change` directo (sin debounce).

**Re-focus tras recarga:** En inputs de búsqueda: `x-init="if ($el.value) { $el.focus(); $el.setSelectionRange($el.value.length, $el.value.length) }"`.

### Bug crítico: Jinja2 + tojson + atributo HTML double-quoted

**`| tojson`** devuelve un objeto `Markup` con comillas `"` sin escapar. Cuando se usa dentro de un atributo HTML con comillas dobles, las `"` del JSON truncan el atributo. El workaround `| replace('"', '&quot;')` sobre un `Markup` genera double-escape: `&amp;quot;` en HTML, que Alpine.js ve como JS inválido y falla silenciosamente (estado vacío).

**Patrón correcto para strings simples** (num_comprobante, tipo_comprobante, etc.):
```html
<!-- Jinja2 con autoescape maneja las " correctamente en data-* -->
data-nc="{{ p.get('num_comprobante') or '' }}"
x-init="nc = $el.dataset.nc"
```

**Patrón correcto para objetos/arrays** (editData, cuotas, etc.):
```html
<!-- Atributo con comilla simple: tojson escapa ' como ', las " JSON son seguras -->
data-edit='{{ {...} | tojson }}'
x-init="editData = JSON.parse($el.dataset.edit)"
```

**Patrón correcto para funciones en x-data:**
```html
<!-- Comilla simple en el atributo x-data -->
<tbody x-data='billingRow({{ p.get("empresa_pagadora") | tojson }})'>
```

### Modal global de comprobantes (`$store.picker`)

Registrado en `base.html` via `alpine:init`. Permite buscar y seleccionar un comprobante de la BD para vincularlo a cualquier formulario.

```javascript
Alpine.store('picker', {
  open: false, formKey: '', busq: '', fecha: '', resultados: [], sel: null,
  abrir(formKey)   // abre el modal y guarda la clave del formulario destino
  cerrar()
  buscar()         // llama GET /api/comprobantes/search
  confirmar()      // dispara window CustomEvent 'comprobante-picked'
                   // con detail: { formKey, c }
                   // c tiene: numero, tipo, fecha_emision, monto_total, empresa
})
```

Para vincular en un formulario:
```html
<!-- Botón de apertura -->
<button type="button" @click="$store.picker.abrir('billing-{{ p._id }}')">
  <i class="fa-solid fa-magnifying-glass"></i>
</button>

<!-- Listener en el elemento padre con estado -->
@comprobante-picked.window="
  if ($event.detail.formKey === 'billing-{{ p._id }}') {
    nc = $event.detail.c.numero;
    tipoComp = $event.detail.c.tipo;
    fechaComp = $event.detail.c.fecha_emision || '';
  }"
```

Ya integrado en: `billing.html` (pagos principales), `billing.html` (cuotas), `fraccionamientos.html` (cuotas de fraccionamiento).

---

## Base de datos (MongoDB)

Base: `ipidet_agent` — URI en `.env` como `MONGODB_URI`. En Atlas, el cliente usa `tlsCAFile=certifi.where()`.

### Colecciones

| Variable en código | Colección MongoDB | Descripción |
|--------------------|------------------|-------------|
| `members_col` | `members` | Padrón de socios |
| `payments_col` | `payments` | Pagos/cobranzas por período |
| `faqs_col` | `faqs` | Base de conocimiento del agente |
| `events_col` | `events` | Eventos internos del agente de email |
| `companies_col` | `companies` | Empresas empleadoras |
| `credito_col` | `facturas_credito` | Crédito empresa (facturas a cobrar) |
| `productos_col` | `productos` | Productos facturables (cuotas, eventos) |
| `comprobantes_col` | `comprobantes` | Comprobantes de pago emitidos (boletas/facturas) |
| `comunicaciones_col` | `comunicaciones` | Historial de emails masivos enviados |
| `eventos_col` | `eventos_ipidet` | Eventos y capacitaciones |
| `pendientes_col` | `pendientes` | Tareas/pendientes del equipo admin |
| `users_col` (en auth.py) | `users` | Usuarios de la plataforma web |

---

### Colección `members`

```
member_id        string    "IPIDET-0294" — del campo "No. REGISTRO IPIDET" del Excel
apellidos        string    siempre en MAYÚSCULAS
nombres          string    en Title Case
titulo           string    normalizado: "Contador" | "Abogado" | "Economista" | etc.
centro_trabajo   string
celular          string
ubicacion        string    normalizado: "Lima" | "La Libertad" | "Arequipa" | etc.
tipo_socio       string    "ordinario" | "afiliado" | "honorario" | etc.
dni              string | null
wp_user_id       int | null   ID del usuario en WordPress/WooCommerce
fecha_ingreso    datetime | null
fecha_nacimiento datetime | null
estado           string    "activo" | "retirar"
emails           [{
                   email:     string,
                   estado:    "habilitado" | "inhabilitado" | "rebotado",
                   principal: bool
                 }]
notas            string    comentarios del Excel unidos con " | "
comentarios      [{
                   _id:   ObjectId,
                   texto: string,
                   fecha: "YYYY-MM-DD HH:MM"
                 }]
created_at       datetime
```

---

### Colección `payments`

Un documento por socio por período.

```
member_id               string    FK → members.member_id
periodo                 string    "2025" | "2026"
estado                  string    "pagado" | "debe" | "fraccionamiento" | "parcial"
                                  | "exonerado" | "no_aplica" | "pendiente"
                                  | "retirar" | "en_revision" | "revisar"
empresa_pagadora        string | null    "EY" | "BDO" | "PWC" | "KPMG" | "PPU"
pagado_por              string | null    texto libre o "WC#<order_id>" si vino de WooCommerce
fecha_pago              string | null    "YYYY-MM-DD"
medio_pago              string | null    "Transferencia bancaria" | "Efectivo" | "WooCommerce" | etc.
banco_origen            string | null    "BCP" | "Interbank" | "BBVA" | etc.
num_comprobante         string | null    número de boleta/factura del pago principal
tipo_comprobante        string | null    "boleta" | "factura" | "recibo"
fecha_emision_comprobante string | null  "YYYY-MM-DD" — fecha SUNAT del comprobante
link_constancia         string | null    URL al correo o documento de constancia del pago
comprobante_emitido     bool | null      si IPIDET ya emitió el comprobante al socio
monto_objetivo          float | null     monto esperado del fraccionamiento total
raw_original            string           valor original del Excel (solo importación)

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

### Colección `comprobantes`

Comprobantes de pago emitidos por IPIDET (boletas/facturas) — son los documentos tributarios que se entregan al socio o empresa.

```
numero           string    "B001-000123" — número del comprobante
tipo             string    "boleta" | "factura"
fecha_emision    string    "YYYY-MM-DD"
monto_total      float
producto_nombre  string    nombre del producto/servicio facturado
concepto         string    descripción libre
empresa          string    razón social si es a empresa, vacío si es a persona natural
socios           [string]  lista de member_id asociados al comprobante
estado           string    "emitido" | "anulado"
created_at       datetime
```

**Nota:** `payments.num_comprobante` referencia el campo `numero` de esta colección. El modal `$store.picker` busca en esta colección para vincular comprobantes a pagos.

---

### Colección `companies`

```
nombre      string    nombre comercial (ej. "EY", "KPMG")
ruc         string
razon_social string
tipo        string    "auditora" | "banco" | "estudio" | "empresa" | "otro"
activo      bool
created_at  datetime
```

---

### Colección `facturas_credito`

Facturas emitidas a empresas en modalidad crédito (cobrar después).

```
empresa          string
numero_factura   string
monto            float
fecha_emision    string    "YYYY-MM-DD"
fecha_vencimiento string   "YYYY-MM-DD"
concepto         string
socios           [string]  member_ids beneficiarios
estado           string    "pendiente" | "cobrado" | "vencido"
fecha_cobro      string | null
comentarios      [{ texto: string, fecha: "YYYY-MM-DD HH:MM" }]
cuotas           [{
                   numero:     int
                   monto:      float
                   fecha_venc: string | null
                   fecha_pago: string | null
                   estado:     "pendiente" | "pagado"
                 }]
created_at       datetime
```

---

### Colección `productos`

Catálogo de productos facturables de IPIDET.

```
nombre          string
tipo            string    "cuota_anual" | "cuota_provincia" | "evento" | "fraccionamiento" | "otro"
precio          float | null
periodo         string    "2026" | "2025" | "" (si aplica a todos)
descripcion     string
activo          bool
wc_product_id   int | null    ID del producto en WooCommerce
created_at      datetime
```

Productos por defecto (seeded al startup):
- Cuota anual Lima 2026 → S/780, `wc_product_id: 8882`
- Cuota anual provincia 2026 → S/350, `wc_product_id: 19105`
- Cuota de fraccionamiento → S/260, `wc_product_id: 8880`
- Entrada a evento → precio variable

---

### Colección `eventos_ipidet`

```
titulo       string
descripcion  string
fecha        string    "YYYY-MM-DD"
hora         string    "HH:MM"
lugar        string
cupo_max     int | null
estado       string    "activo" | "cancelado"
inscritos    [{
               member_id:  string,
               nombre:     string,
               asistio:    bool | null
             }]
created_at   datetime
```

---

### Colección `pendientes`

Tareas/tickets internos del equipo admin.

```
titulo           string
descripcion      string
prioridad        string    "alta" | "media" | "baja"
estado           string    "pendiente" | "en_progreso" | "resuelto"
member_id        string | null    socio relacionado (si aplica)
nombre_miembro   string | null
created_at       datetime
updated_at       datetime
resuelto_at      datetime | null
```

---

### Colección `users`

Usuarios de la plataforma web (en `webapp/auth.py`).

```
email           string    (unique, lowercase)
password_hash   string    bcrypt
role            string    "admin" | "viewer"
permisos        [string]  lista de secciones habilitadas (ver SECCIONES en auth.py)
active          bool
created_at      datetime
```

**Secciones con control de acceso:** `dashboard`, `members`, `billing`, `fraccionamientos`, `finanzas`, `faqs`, `marketing`, `comunicaciones`, `eventos`, `pendientes`. Las rutas `/`, `/api/*`, `/webhook/*` y `/comprobantes` son accesibles a cualquier usuario autenticado.

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

### Colección `comunicaciones`

Historial de emails masivos enviados (sin campo `destinatarios` al leer la lista para no sobrecargar).

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
DB_NAME                     — Nombre de la base (default: ipidet_agent)

# Gmail
GMAIL_ADDRESS               — Cuenta Gmail del agente
GMAIL_APP_PASSWORD          — App password (no la contraseña normal)

# Comportamiento del agente
ADMIN_FORWARD_EMAIL         — administracion@ipidet.org
CONFIDENCE_THRESHOLD        — 0.85 (umbral para auto-responder sin pedir aprobación)
AUTO_APPROVE_CONFIDENCE     — 0.88 (umbral para aprobar automáticamente)
CHECK_INTERVAL_SECONDS      — 60 (frecuencia de polling IMAP)
CLARIFICATION_TIMEOUT_HOURS — 24

# Auth web
ADMIN_EMAIL                 — Email del admin inicial (seeded al startup)
ADMIN_PASSWORD              — Contraseña del admin inicial
SESSION_SECRET              — Secret para firmar cookies de sesión

# Brevo (email marketing)
BREVO_SMTP_USER             — Login SMTP de Brevo
BREVO_SMTP_PASS             — Password SMTP de Brevo
BREVO_WEBHOOK_TOKEN         — Token para verificar webhooks de rebotes

# Google / Sheets
GOOGLE_CREDENTIALS_PATH     — google_credentials.json
BILLING_SHEET_ID            — ID de la hoja de Google Sheets de cobranzas

# Portal de socios (WordPress ↔ FastAPI)
PORTAL_SECRET               — secret Bearer para autenticar llamadas WP→FastAPI
WC_WEBHOOK_SECRET           — secret HMAC-SHA256 del webhook WooCommerce
PORTAL_API_BASE             — URL pública de FastAPI (para pruebas locales)
WC_API_URL                  — URL base de la API REST de WooCommerce
WC_CONSUMER_KEY             — Consumer key de WooCommerce
WC_CONSUMER_SECRET          — Consumer secret de WooCommerce
```

---

## Web admin — templates y rutas

### Templates activos

| Template | Ruta principal | Descripción |
|----------|---------------|-------------|
| `base.html` | — | Layout base: sidebar, topbar, modal picker comprobantes |
| `dashboard.html` | `/` | Stats: socios activos, pagados, deben, FAQs |
| `members.html` | `/members` | Padrón paginado con filtros |
| `member.html` | `/members/{id}` | Detalle de socio: emails, comentarios, pagos |
| `billing.html` | `/billing` | Cobranzas: tabla, edición inline, cuotas, parciales |
| `fraccionamientos.html` | `/fraccionamientos` | Vista de todos los fraccionamientos activos |
| `comprobantes.html` | `/comprobantes` | Comprobantes emitidos (boletas/facturas) |
| `credito.html` | `/billing/credito` | Facturas empresa en crédito |
| `ingresos.html` | `/billing/ingresos` | Dashboard de ingresos por período/fecha |
| `facturacion.html` | `/billing/facturacion` | Emisión de comprobantes pendientes por socio |
| `facturacion_empresa.html` | `/billing/facturacion/empresa` | Emisión batch para empresas |
| `faqs.html` | `/faqs` | FAQs del agente de email |
| `marketing.html` | `/marketing` | Stats de marketing: activos, deben, por ubicación |
| `comunicaciones.html` | `/comunicaciones` | Envío de emails masivos + historial |
| `eventos.html` | `/eventos` | Lista de eventos y capacitaciones |
| `evento_detalle.html` | `/eventos/{id}` | Detalle, inscritos, asistencia |
| `pendientes.html` | `/pendientes` | Tareas internas del equipo |
| `empresas.html` | `/empresas` | CRUD de empresas empleadoras |
| `productos.html` | `/productos` | Catálogo de productos facturables |
| `wc_orders.html` | `/wc-orders` | Pedidos WooCommerce: vincular a socios |
| `sync_usuarios.html` | `/sync-usuarios` | Sync WP users ↔ socios IPIDET |
| `rebotes.html` | `/comunicaciones/rebotes` | Emails rebotados (bounces de Brevo) |
| `login.html` | `/login` | Login de la plataforma |
| `users.html` | `/admin/users` | Gestión de usuarios (solo admin) |

Templates pendientes/mockup (no productivos): `finanzas_mockup.html`.

---

### Rutas completas — webapp/app.py

#### Dashboard y socios

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/` | Dashboard con stats |
| `GET` | `/members` | Padrón paginado; filtros: search, estado, pago, ubicacion |
| `POST` | `/members/nuevo` | Crear socio nuevo |
| `GET` | `/members/{id}` | Detalle de socio |
| `POST` | `/members/{id}/emails/toggle` | Habilitar/inhabilitar email |
| `POST` | `/members/{id}/emails/set-principal` | Marcar email como principal |
| `POST` | `/members/{id}/emails/add` | Agregar email |
| `POST` | `/members/{id}/notes` | Actualizar notas |
| `POST` | `/members/{id}/comentarios/add` | Agregar comentario |
| `POST` | `/members/{id}/comentarios/{cid}/delete` | Eliminar comentario |
| `POST` | `/members/{id}/estado` | Cambiar estado (activo/retirar) |
| `POST` | `/members/{id}/tipo_socio` | Cambiar tipo de socio |
| `POST` | `/members/{id}/dni` | Actualizar DNI |
| `POST` | `/members/{id}/wp_user_id` | Vincular WP user ID |
| `POST` | `/members/{id}/send-email` | Enviar email individual al socio |

#### Cobranzas

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/billing` | Cobranzas paginadas; filtros: periodo, estado, empresa, search, comprobante_emitido |
| `GET` | `/billing/export` | Descarga Excel con pagos filtrados |
| `POST` | `/billing/generar-periodo` | Genera registros de pago para todos los socios activos de un período |
| `POST` | `/billing/{id}/update` | Actualiza pago principal (estado, fecha, empresa, comprobante, banco, medio, etc.) |
| `POST` | `/billing/{id}/emitir-comprobante` | Marca el comprobante como emitido y registra número |
| `POST` | `/billing/{id}/socio/estado` | Cambia estado del socio desde la vista de cobranzas |
| `POST` | `/billing/{id}/cuotas/objetivo` | Define monto objetivo del fraccionamiento |
| `POST` | `/billing/{id}/cuotas/add` | Agrega cuota |
| `POST` | `/billing/{id}/cuotas/{n}/update` | Actualiza cuota (marcar pagada, agregar comprobante) |
| `POST` | `/billing/{id}/cuotas/{n}/delete` | Elimina cuota |
| `POST` | `/billing/{id}/parciales/init` | Define monto total del pago parcial |
| `POST` | `/billing/{id}/parciales/add` | Registra pago parcial |
| `POST` | `/billing/{id}/parciales/{n}/update` | Actualiza pago parcial |
| `POST` | `/billing/{id}/parciales/{n}/delete` | Elimina pago parcial |
| `GET` | `/billing/ingresos` | Dashboard de ingresos; filtros: fecha_desde, fecha_hasta, medio, empresa, modalidad |
| `GET` | `/billing/facturacion` | Socios con pago pero sin comprobante emitido |
| `GET` | `/billing/facturacion/empresa` | Pendientes de facturación para empresas |
| `POST` | `/billing/facturacion/empresa/guardar` | Emite comprobante batch para una empresa |
| `GET` | `/api/facturacion/pendientes` | JSON con pagos pendientes de comprobante |
| `GET` | `/api/facturacion/socio-info` | JSON con info del socio para modal de facturación |
| `GET` | `/fraccionamientos` | Vista de fraccionamientos activos; filtros: periodo, alerta, search |

#### Comprobantes

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/comprobantes` | Lista paginada de comprobantes; filtros: search, tipo, empresa, fecha |
| `POST` | `/comprobantes/add` | Crear comprobante nuevo |
| `POST` | `/comprobantes/{id}/update` | Editar comprobante |
| `POST` | `/comprobantes/{id}/delete` | Anular comprobante (soft delete: estado="anulado") |
| `GET` | `/api/comprobantes/search` | Búsqueda de comprobantes para el modal picker; params: q, fecha |

#### Crédito empresa

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/billing/credito` | Facturas en crédito; filtros: empresa, estado |
| `POST` | `/billing/credito/nueva` | Nueva factura de crédito |
| `POST` | `/billing/credito/{id}/estado` | Cambiar estado (cobrado/pendiente) |
| `POST` | `/billing/credito/{id}/delete` | Eliminar factura |
| `POST` | `/billing/credito/{id}/comentarios/add` | Agregar comentario |
| `POST` | `/billing/credito/{id}/comentarios/{idx}/delete` | Eliminar comentario |
| `POST` | `/billing/credito/{id}/cuotas/add` | Agregar cuota |
| `POST` | `/billing/credito/{id}/cuotas/{n}/update` | Actualizar cuota |
| `POST` | `/billing/credito/{id}/cuotas/{n}/delete` | Eliminar cuota |
| `POST` | `/billing/credito/{id}/update` | Editar datos de la factura |

#### Empresas

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/empresas` | CRUD de empresas empleadoras |
| `POST` | `/empresas/add` | Nueva empresa |
| `POST` | `/empresas/{id}/update` | Editar empresa |
| `POST` | `/empresas/{id}/delete` | Eliminar empresa |
| `GET` | `/api/companies` | Autocomplete de empresas (usado en billing) |
| `POST` | `/api/companies` | Crear empresa via API |
| `DELETE` | `/api/companies/{id}` | Eliminar empresa via API |

#### Productos

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/productos` | Lista de productos facturables |
| `POST` | `/productos/add` | Nuevo producto |
| `POST` | `/productos/{id}/update` | Editar producto |
| `POST` | `/productos/{id}/toggle` | Activar/desactivar producto |
| `POST` | `/productos/{id}/delete` | Eliminar producto |

#### Comunicaciones

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/comunicaciones` | Editor de email masivo + historial |
| `POST` | `/api/comunicaciones/preview` | Preview de destinatarios según filtros |
| `POST` | `/api/comunicaciones/preview-html` | Preview HTML del email |
| `POST` | `/api/comunicaciones/enviar` | Envío masivo via Brevo |
| `GET` | `/api/comunicaciones/buscar-miembro` | Buscar socio para envío individual |
| `GET` | `/comunicaciones/rebotes` | Emails rebotados |
| `POST` | `/webhook/brevo/bounce` | Webhook de Brevo para registrar bounces |

#### Eventos

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/eventos` | Lista de eventos; filtros: search, estado |
| `POST` | `/eventos/nuevo` | Crear evento |
| `GET` | `/eventos/{id}` | Detalle: info, inscritos, asistencia |
| `POST` | `/eventos/{id}/update` | Editar evento |
| `POST` | `/eventos/{id}/delete` | Cancelar evento (soft delete) |
| `POST` | `/eventos/{id}/inscribir` | Inscribir socio |
| `POST` | `/eventos/{id}/desinscribir/{mid}` | Desinscribir socio |
| `POST` | `/eventos/{id}/asistencia/{mid}` | Marcar asistencia |
| `GET` | `/api/eventos` | JSON con lista de eventos |
| `GET` | `/api/eventos/{id}` | JSON con detalle del evento |

#### Pendientes

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/pendientes` | Lista de pendientes; filtros: estado, prioridad, search |
| `POST` | `/pendientes/add` | Nuevo pendiente |
| `POST` | `/pendientes/{id}/update` | Editar pendiente |
| `POST` | `/pendientes/{id}/estado` | Cambiar estado rápido |
| `POST` | `/pendientes/{id}/delete` | Eliminar pendiente |

#### FAQs

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/faqs` | Lista de FAQs activas |
| `POST` | `/faqs/add` | Nueva FAQ |
| `POST` | `/faqs/{id}/delete` | Soft delete (active = false) |

#### Marketing

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/marketing` | Stats de socios para marketing: activos, deben, por título/ubicación |
| `GET` | `/marketing/export` | Export Excel de la lista de marketing |

#### Auth

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/login` | Formulario de login |
| `POST` | `/login` | Autenticar (bcrypt + cookie de sesión) |
| `GET` | `/logout` | Cerrar sesión |
| `GET` | `/admin/users` | Gestión de usuarios (solo role=admin) |
| `POST` | `/admin/users/add` | Crear usuario |
| `POST` | `/admin/users/{id}/permisos` | Editar permisos de sección |
| `POST` | `/admin/users/{id}/delete` | Eliminar usuario |

#### Portal WooCommerce

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/api/portal/member-status?email=X` | Estado del socio para WordPress; requiere `Authorization: Bearer <PORTAL_SECRET>` |
| `POST` | `/webhook/woocommerce/order` | Pedido WC completado → actualiza MongoDB; verifica firma HMAC-SHA256 |
| `GET` | `/wc-orders` | Vista admin de pedidos WC + vinculación a socios |
| `POST` | `/wc-orders/{order_id}/vincular` | Vincular pedido WC a un socio manualmente |
| `GET` | `/sync-usuarios` | Vista de sync WP users ↔ socios IPIDET |
| `POST` | `/sync-usuarios/vincular` | Vincular WP user a socio |
| `POST` | `/api/portal/update-alternative-email` | Actualizar email alternativo desde WP |
| `POST` | `/api/portal/update-dni` | Actualizar DNI desde WP |

#### API JSON

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/api/members` | Lista socios JSON; filtros: search, estado, pago |
| `GET` | `/api/members/{id}` | Detalle socio JSON |
| `GET` | `/api/payments` | Pagos JSON; filtros: periodo, estado |
| `POST` | `/api/email/test` | Test de envío de email |

**Productos WooCommerce mapeados** (en `webapp/portal_db.py::WC_PRODUCT_MAP`):
- `8882` → pago ordinario completo (cuota anual Lima)
- `19105` → cuota anual provincia
- `8880` → fraccionamiento (registra una cuota)

---

## webapp/db.py — funciones clave

```python
# Socios
get_stats()
get_members(search, estado, pago, ubicacion, page)   → (docs[], total)
get_member(member_id)                 → dict | None
create_member(apellidos, nombres, titulo, email, ...)  → member_id
update_email_status / set_email_principal / add_email / mark_email_bounce
update_member_notes / add_comentario / delete_comentario
update_member_estado / update_member_tipo_socio / update_member_dni / update_member_wp_user_id
auto_set_wp_user_id(member_id, wp_user_id)

# Cobranzas
generar_cobros_periodo(periodo)       → dict con conteos
get_payments(periodo, estado, empresa, search, comprobante_emitido, page) → (docs[], total)
get_payments_export(...)              → docs[] sin paginación
update_payment(payment_id, estado, empresa, fecha_pago, medio, banco_origen,
               num_comprobante, tipo_comprobante, fecha_emision_comprobante,
               link_constancia, comprobante_emitido)
set_monto_objetivo(payment_id, monto_objetivo)

# Cuotas de fraccionamiento
add_cuota / update_cuota / delete_cuota
_sync_estado_from_cuotas(payment_id)

# Pagos parciales
set_monto_total / add_pago_parcial / update_pago_parcial / delete_pago_parcial
_sync_estado_from_parciales(payment_id)

# Vista fraccionamientos
get_fraccionamientos(periodo, alerta, search, page)   → (docs[], total)

# Facturación
get_comprobantes_pendientes(periodo, search)           → docs pendientes de emitir
emitir_comprobante(payment_id, tipo, numero, ...)
emitir_comprobante_batch(items, num_comprobante, tipo, ...)
mark_payment_empresa(payment_id, empresa, num_comprobante, ...)

# Comprobantes
get_comprobantes(search, tipo, empresa, fecha, page)  → (docs[], total)
create_comprobante(numero, tipo, fecha_emision, monto_total, producto_nombre, concepto, empresa, socios)
update_comprobante(comprobante_id, fields)
delete_comprobante(comprobante_id)              # soft delete: estado="anulado"
get_comprobante_stats()

# Crédito empresa
get_facturas_credito(empresa, estado)           → docs[]
create_factura_credito(empresa, numero_factura, monto, fecha_emision, fecha_vencimiento, ...)
update_factura_credito_estado / delete_factura_credito
add_comentario_credito / delete_comentario_credito
add_cuota_credito / update_cuota_credito / delete_cuota_credito
update_factura_credito_fields(factura_id, fields)
get_credito_stats()

# Ingresos
get_ingresos(fecha_desde, fecha_hasta, medio, empresa, modalidad, ...)  → dict con datos

# Empresas
get_all_companies / get_companies(search)       → list
add_company / update_company / delete_company

# Productos
get_productos(tipo, solo_activos)               → list
create_producto / update_producto / delete_producto

# Comunicaciones / Marketing
get_marketing_stats(periodo)                    → dict
get_marketing_emails(titulo, ubicacion, ...)    → list
get_comunicacion_destinatarios(...)             → list
save_comunicacion_log(asunto, plantilla, filtros, ...) → id
get_comunicaciones_history(limit)               → list
get_bounced_emails()
mark_email_bounce(email, bounce_type)

# Eventos
get_eventos / get_evento / create_evento / update_evento / delete_evento
inscribir_socio / desinscribir_socio / marcar_asistencia
get_eventos_proximos / get_evento_stats

# Pendientes
get_pendientes / create_pendiente / update_pendiente / delete_pendiente
get_pendientes_stats()

# WooCommerce
get_wc_payment(order_id)
get_wc_payment_by_wp_user_id(wp_user_id, periodo)
vincular_wp_usuario(wc_email, member_id, wp_user_id)
vincular_wc_order(order_id, member_id, wc_email, periodo, cuota_numero)
get_wc_payment_by_email(email, periodo)

# FAQs
get_faqs / get_faq_categories / save_faq / delete_faq

# Utils
get_or_create_payment(member_id, periodo)       → payment_id
get_member_titulos / get_member_ubicaciones     → list[str]
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
4. FastAPI verifica la firma HMAC-SHA256, identifica el producto por `wc_product_id`, y actualiza el pago en MongoDB

`webapp/wc_client.py` maneja llamadas directas a la API REST de WooCommerce (para sync de usuarios, consulta de pedidos, etc.).

---

## Comunicaciones masivas (Brevo)

`webapp/mailer.py` envía emails via Brevo SMTP. Los bounces (hard y soft) llegan via webhook a `POST /webhook/brevo/bounce` y se registran en `members.emails[].estado = "rebotado"`. La vista `/comunicaciones/rebotes` muestra el historial.

Filtros disponibles al enviar: título, ubicación, estado de socio, estado de pago, periodo.

---

## Autenticación web

Toda la plataforma requiere login (excepto `/login`, `/webhook/*`). Las sesiones se almacenan en cookies firmadas con `SESSION_SECRET`. El rol `admin` tiene acceso a `/admin/users`; los usuarios con rol `viewer` solo acceden a las secciones en su lista `permisos`.

El admin inicial se crea automáticamente al startup si `users_col` está vacía y están definidos `ADMIN_EMAIL` y `ADMIN_PASSWORD` en el entorno.

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

- **Múltiples `<tbody>` por tabla:** En `billing.html` cada pago usa su propio `<tbody x-data='billingRow(...)'>` para compartir estado Alpine.js entre el `<tr>` de datos y el `<tr>` de edición. Alpine.js solo comparte estado hacia abajo (hijos), no entre hermanos.
- **`_sync_estado_from_cuotas` y `_sync_estado_from_parciales`:** Cada vez que se modifica una cuota o pago parcial, estas funciones recalculan el estado general (`pagado`, `fraccionamiento`, `parcial`, `debe`). No actualizar manualmente.
- **Soft delete en FAQs:** `active: false` desactiva sin borrar, para mantener historial de aprendizaje.
- **Soft delete en comprobantes:** `estado: "anulado"` en lugar de borrar físicamente.
- **Emails inhabilitados/rebotados:** El agente de email consulta solo emails con `estado: "habilitado"`. Los rebotes de Brevo marcan el email como `"rebotado"` automáticamente.
- **Portal secret vs WC secret:** Son dos secrets distintos. `PORTAL_SECRET` autentica las llamadas GET de WordPress. `WC_WEBHOOK_SECRET` firma los POST de WooCommerce con HMAC-SHA256.
- **certifi en Atlas:** La conexión a MongoDB Atlas requiere `tlsCAFile=certifi.where()` en todos los clientes MongoDB del proyecto (`webapp/db.py`, `webapp/auth.py`).
- **`wp_user_id` en members:** Vincula el socio con su usuario de WordPress. Se auto-asigna cuando WooCommerce envía un pedido con `customer_id` reconocido. También se puede setear manualmente desde `/sync-usuarios`.
- **Comprobantes vs num_comprobante en payments:** `payments.num_comprobante` es el número del comprobante recibido por el socio (texto libre o referencia a `comprobantes.numero`). La colección `comprobantes` es el registro formal de los comprobantes que IPIDET emite. El modal picker vincula ambos.
