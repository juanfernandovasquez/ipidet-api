# IPIDET — Guía para agentes de IA

Proyecto de automatización para la administración de IPIDET (asociación profesional peruana). Incluye un agente de email, un bot de Telegram, una web admin y una base de datos de socios.

---

## Tabla de contenidos

1. [Cómo levantar los servicios](#levantar)
2. [REGLAS CRÍTICAS — leer antes de editar](#criticas)
3. [Archivos más frecuentemente modificados](#frecuentes)
4. [Mapa tarea → archivos a editar](#mapa)
5. [Arquitectura general](#arquitectura)
6. [Stack tecnológico — Frontend](#frontend)
7. [Base de datos (MongoDB)](#mongodb)
8. [Variables de entorno](#env)
9. [Web admin — templates y rutas](#webapp)
10. [webapp/db.py — funciones clave](#dbpy)
11. [Flujo del agente de email](#agente)
12. [Portal de socios (WooCommerce)](#portal)
13. [Comunicaciones masivas (Brevo)](#brevo)
14. [Autenticación web](#auth)
15. [Importación del padrón](#padron)
16. [Decisiones de diseño relevantes](#diseno)

---

## 1. Cómo levantar los servicios {#levantar}

```bash
# Web admin (FastAPI en http://localhost:8000)
python platform_main.py

# Agente de email (loop continuo, revisa Gmail cada 60s)
python main.py

# Importar padrón desde Excel a MongoDB  ⚠️ DESTRUCTIVO — ver sección 15
python import_padron.py
```

El servidor de producción corre en Render.com (auto-deploy desde GitHub `main`). El archivo `passenger_wsgi.py` es para entornos cPanel/Passenger y no se usa en Render.

---

## 2. REGLAS CRÍTICAS — leer antes de editar {#criticas}

Estas reglas previenen bugs silenciosos que son difíciles de detectar. Aplican en cualquier tarea que toque templates, cobranzas o datos de socios.

### R1 — Bug tojson en atributos HTML (templates Jinja2)

`| tojson` devuelve `Markup` con comillas `"` sin escapar. Dentro de un atributo `"..."` esas comillas truncan el atributo. El workaround `| replace('"', '&quot;')` genera double-escape (`&amp;quot;`) que Alpine.js ve como JS inválido y falla silenciosamente.

**Patrón correcto según tipo de dato:**

```html
<!-- Strings simples: usar data-* con autoescape de Jinja2 -->
data-nc="{{ p.get('num_comprobante') or '' }}"
x-init="nc = $el.dataset.nc"

<!-- Objetos/arrays: atributo con comilla simple, tojson es seguro -->
data-edit='{{ {"campo": valor} | tojson }}'
x-init="editData = JSON.parse($el.dataset.edit)"

<!-- Funciones en x-data: atributo con comilla simple -->
<tbody x-data='billingRow({{ p.get("empresa_pagadora") | tojson }})'>
```

### R2 — Alpine.js: scope de x-data solo baja hacia hijos

Alpine.js 3 no comparte estado entre elementos hermanos. Para compartir estado entre dos `<tr>` consecutivos (datos + edición), hay que envolverlos en un `<tbody x-data='...'>` común. Múltiples `<tbody>` por tabla es HTML5 válido.

**Nunca usar** `this.$el.submit()` — Alpine.js envuelve `$el` en un Proxy que no expone `.submit()`. Usar en cambio `$refs.filterForm.submit()` con `x-ref="filterForm"` en el `<form>`.

### R3 — Estado de payments: NO actualizar manualmente

El campo `payments.estado` es calculado por dos funciones internas. **Nunca escribir directamente** `{"$set": {"estado": "..."}}` en cuotas o parciales sin pasar por ellas:

- Después de modificar `cuotas[]` → la función `_sync_estado_from_cuotas()` se llama automáticamente desde `update_cuota()` y `add_cuota()`. Si llamas MongoDB directamente, hazla tú también.
- Después de modificar `pagos_parciales[]` → ídem con `_sync_estado_from_parciales()`.

Comportamiento real de cada función (verificado en código):

| Función | Qué hace |
|---------|---------|
| `_sync_estado_from_cuotas` | Si hay cuotas → fuerza `"fraccionamiento"`. **Nunca cambia a `"pagado"`** — ese cambio es manual. |
| `_sync_estado_from_parciales` | Sin parciales → `"debe"`; monto_pagado ≥ monto_total → `"pagado"`; parcial → `"parcial"`. |

### R4 — _id de MongoDB en templates

`_id` es un `ObjectId` de PyMongo, no un string. En templates Jinja2 usar siempre el filtro `| string` o pasar por `_clean()`:

```html
<!-- Correcto -->
data-id="{{ p._id | string }}"

<!-- En rutas FastAPI, el _id ya viene como string si pasó por _clean() -->
```

### R5 — import_padron.py es destructivo

`import_padron.py` hace **DROP completo de las colecciones `members` y `payments`** antes de reimportar. Nunca ejecutar en producción sin backup previo.

### R6 — certifi obligatorio en Atlas

Toda conexión a MongoDB Atlas requiere `tlsCAFile=certifi.where()`. Sin esto falla con error de certificado SSL. Aplica a `webapp/db.py`, `webapp/auth.py`, y cualquier cliente nuevo que se agregue.

---

## 3. Archivos más frecuentemente modificados {#frecuentes}

El 90 % de las tareas toca estos archivos:

| Archivo | Qué contiene |
|---------|-------------|
| `webapp/app.py` | Todas las rutas FastAPI (~60 rutas) |
| `webapp/db.py` | Todas las queries MongoDB para la web (~2200 líneas) |
| `webapp/templates/billing.html` | Vista de cobranzas: la más compleja, con cuotas y parciales |
| `webapp/templates/base.html` | Layout, sidebar, modal picker global |
| `webapp/templates/member.html` | Detalle de socio |
| `webapp/templates/credito.html` | Facturas empresa en crédito |

---

## 4. Mapa tarea → archivos a editar {#mapa}

| Tarea | Archivos a modificar (en orden) |
|-------|---------------------------------|
| Nuevo campo en `payments` | `webapp/db.py` (schema + update_payment) → `webapp/app.py` (ruta POST) → `billing.html` (formulario) |
| Nuevo campo en `members` | `webapp/db.py` → `webapp/app.py` → `member.html` + `members.html` |
| Nueva ruta web | `webapp/app.py` → `webapp/templates/<nuevo>.html` (extiende `base.html`) |
| Nueva colección MongoDB | `webapp/db.py` (agregar `_col` al inicio + funciones CRUD) → `webapp/app.py` (rutas) → template |
| Cambiar lógica de estado de pago | `webapp/db.py` (funciones `_sync_*`) — **no tocar desde templates** |
| Nuevo campo en comprobantes | `webapp/db.py` (create_comprobante, update_comprobante) → `comprobantes.html` → `base.html` (modal picker si aplica) |
| Cambiar filtros de cobranzas | `webapp/db.py` (get_payments) → `webapp/app.py` (ruta GET /billing) → `billing.html` |
| Nueva integración WooCommerce | `webapp/portal_db.py` + `webapp/portal_router.py` → `webapp/app.py` (webhook route) |

---

## 5. Arquitectura general {#arquitectura}

```
main.py              — Agente de email: loop principal, orquesta todo
platform_main.py     — Servidor web admin (uvicorn + FastAPI)
import_padron.py     — Importación Excel → MongoDB (one-shot, DESTRUCTIVO)
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
  db.py              — CRUD de FAQs en MongoDB + colecciones del agente
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
  templates/         — Ver sección 9

admin.py             — Script de administración CLI (utilidades)
telegram_main.py     — Punto de entrada del bot de Telegram (independiente)
debug_imap.py        — Herramienta de diagnóstico IMAP
launcher.py          — Lanzador de procesos
```

---

## 6. Stack tecnológico — Frontend {#frontend}

- **Tailwind CSS v3** (CDN) — estilos utility-first
- **Alpine.js 3** (CDN) — reactividad declarativa en templates
- **Jinja2** — renderizado server-side (FastAPI)
- **FontAwesome 6** — íconos

### Patrones Alpine.js correctos

**Formularios con auto-submit:**
```html
<form x-ref="filterForm" method="get">
  <select @change="$refs.filterForm.submit()">...</select>
  <input @input="clearTimeout(t); t=setTimeout(()=>$refs.filterForm.submit(),450)">
</form>
```
Regla: selects usan `@change` directo; inputs de texto usan debounce de 450 ms.

**Re-focus tras recarga de página en inputs de búsqueda:**
```html
x-init="if ($el.value) { $el.focus(); $el.setSelectionRange($el.value.length, $el.value.length) }"
```

**Scope de x-data en tablas (ver también R2):**
```html
<!-- Cada pago tiene su propio tbody que envuelve los dos tr -->
<tbody x-data='billingRow({{ p | tojson_seguro }})'>
  <tr><!-- fila de datos --></tr>
  <tr x-show="editando"><!-- fila de edición --></tr>
</tbody>
```

### Bug conocido: Jinja2 + tojson + atributos HTML

Ver **Regla R1** en la sección de Reglas Críticas. Resumen:
- Strings simples → `data-campo="{{ valor }}"` + `$el.dataset.campo`
- Objetos/arrays → `data-edit='{{ obj | tojson }}'` + `JSON.parse($el.dataset.edit)`
- Argumentos de función → `x-data='fn({{ arg | tojson }})'`

### Modal global de comprobantes (`$store.picker`)

Registrado en `base.html` vía `alpine:init`. Permite buscar y seleccionar un comprobante de la BD para vincularlo a cualquier formulario.

```javascript
Alpine.store('picker', {
  open: false, formKey: '', busq: '', fecha: '', resultados: [], sel: null,
  abrir(formKey),   // abre el modal y guarda la clave del formulario destino
  cerrar(),
  buscar(),         // llama GET /api/comprobantes/search
  confirmar(),      // dispara CustomEvent 'comprobante-picked' en window
                    // detail: { formKey, c }
                    // c tiene: numero, tipo, fecha_emision, monto_total, empresa
})
```

Para vincular en un formulario:
```html
<!-- Botón de apertura -->
<button type="button" @click="$store.picker.abrir('billing-{{ p._id | string }}')">
  <i class="fa-solid fa-magnifying-glass"></i>
</button>

<!-- Listener en el elemento con estado Alpine -->
@comprobante-picked.window="
  if ($event.detail.formKey === 'billing-{{ p._id | string }}') {
    nc = $event.detail.c.numero;
    tipoComp = $event.detail.c.tipo;
    fechaComp = $event.detail.c.fecha_emision || '';
  }"
```

Ya integrado en: `billing.html` (bloque pagos principales y bloque cuotas), `fraccionamientos.html` (cuotas de fraccionamiento), `credito.html`.

---

## 7. Base de datos (MongoDB) {#mongodb}

Base: `ipidet_agent` — URI en `.env` como `MONGODB_URI`. En Atlas, el cliente usa `tlsCAFile=certifi.where()` (ver R6).

### 7.1 Colecciones — mapa completo

| Variable en código | Colección MongoDB | Módulo | Descripción |
|--------------------|------------------|--------|-------------|
| `members_col` | `members` | `webapp/db.py` | Padrón de socios |
| `payments_col` | `payments` | `webapp/db.py` | Pagos/cobranzas por período |
| `faqs_col` | `faqs` | `webapp/db.py` | Base de conocimiento del agente |
| `events_col` | `events` | `webapp/db.py` | (reservado, poco usado actualmente) |
| `companies_col` | `companies` | `webapp/db.py` | Empresas empleadoras |
| `credito_col` | `facturas_credito` | `webapp/db.py` | Crédito empresa (facturas a cobrar) |
| `productos_col` | `productos` | `webapp/db.py` | Productos facturables (cuotas, eventos) |
| `comprobantes_col` | `comprobantes` | `webapp/db.py` | Comprobantes emitidos (boletas/facturas) |
| `comunicaciones_col` | `comunicaciones` | `webapp/db.py` | Historial de emails masivos |
| `eventos_col` | `eventos_ipidet` | `webapp/db.py` | Eventos y capacitaciones |
| `pendientes_col` | `pendientes` | `webapp/db.py` | Tareas internas del equipo admin |
| `users_col` | `users` | `webapp/auth.py` | Usuarios de la plataforma web |
| `pending_approvals` | `pending_approvals` | `knowledge_base/db.py` | Cola de aprobaciones del agente vía Telegram |
| `pending_clarifications` | `pending_clarifications` | `knowledge_base/db.py` | Hilos esperando respuesta de usuario |
| `processed_emails` | `processed_emails` | `knowledge_base/db.py` | Emails ya procesados (deduplicación) |
| `bot_state` | `bot_state` | `knowledge_base/db.py` | Estado del bot de Telegram |
| `billing_pending_proofs` | `billing_pending_proofs` | `billing/db.py` | Comprobantes de pago recibidos por email |

### 7.2 Máquina de estados: `payments.estado`

```
generar_cobros_periodo()
        │
        ▼
    "pendiente"  ──────────────────────────────────────┐
        │                                               │
        │ update_payment(estado=...)  [manual]          │
        ▼                                               │
    "debe"    "pagado"    "exonerado"    "no_aplica"    │
    "en_revision"  "revisar"  "retirar"                 │
        │                                               │
        │ add_cuota()                                   │
        ▼                                               │
  "fraccionamiento"  ←── _sync_estado_from_cuotas()    │
   (mientras haya cuotas; a "pagado" solo manual)       │
        │                                               │
        │ set_monto_total()                             │
        ▼                                               │
    "parcial"  ←── _sync_estado_from_parciales()       │
    "pagado"        (auto cuando monto_pagado≥total)   │
    "debe"          (auto cuando no hay parciales)      │
        │                                               │
        │ sync_credito_to_cobranzas()                  │
        ▼                                               │
   "pagado" / "por_cobrar"   (solo si linked a crédito)┘
```

**Resumen de transiciones automáticas:**

| Función | Condición | Nuevo estado |
|---------|-----------|-------------|
| `_sync_estado_from_cuotas` | Hay cuotas (sin importar cuántas pagadas) | `"fraccionamiento"` |
| `_sync_estado_from_cuotas` | No hay cuotas | sin cambio |
| `_sync_estado_from_parciales` | No hay pagos_parciales | `"debe"` |
| `_sync_estado_from_parciales` | monto_pagado ≥ monto_total | `"pagado"` |
| `_sync_estado_from_parciales` | monto_pagado < monto_total | `"parcial"` |
| `sync_credito_to_cobranzas` | Factura de crédito cobrada | `"pagado"` en payments de socios vinculados |
| `sync_credito_to_cobranzas` | Factura pendiente | `"por_cobrar"` en payments de socios vinculados |

**Las transiciones manuales** (vía `update_payment`) son las únicas que cambian entre: `"debe"`, `"pagado"`, `"exonerado"`, `"no_aplica"`, `"en_revision"`, `"revisar"`, `"retirar"`.

### 7.3 Colección `members`

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

### 7.4 Colección `payments`

Un documento por socio por período.

```
member_id               string    FK → members.member_id
periodo                 string    "2025" | "2026"
estado                  string    ver sección 7.2 para estados válidos y transiciones
empresa_pagadora        string | null    "EY" | "BDO" | "PWC" | "KPMG" | "PPU"
pagado_por              string | null    texto libre o "WC#<order_id>" si vino de WooCommerce
fecha_pago              string | null    "YYYY-MM-DD"
medio_pago              string | null    ver MEDIOS_PAGO en webapp/db.py
banco_origen            string | null    ver BANCOS en webapp/db.py
num_comprobante         string | null    referencia a comprobantes.numero (ver sección 7.5)
tipo_comprobante        string | null    "boleta" | "factura" | "recibo"
fecha_emision_comprobante string | null  "YYYY-MM-DD"
link_constancia         string | null    URL al correo o documento de constancia del pago
comprobante_emitido     bool | null      si IPIDET ya emitió el comprobante al socio
monto_objetivo          float | null     monto esperado del fraccionamiento total
raw_original            string           valor original del Excel (solo importación)

# Solo si estado == "fraccionamiento"
cuotas             [{
                     numero:           int       (auto-incremental dentro del pago)
                     monto:            float
                     fecha_venc:       string | null   "YYYY-MM-DD"
                     fecha_pago:       string | null   "YYYY-MM-DD"
                     estado:           "pendiente" | "pagado"
                     medio_pago:       string | null
                     num_comprobante:  string | null
                     tipo_comprobante: string | null
                     link_constancia:  string | null
                   }]

# Solo si estado == "parcial"
monto_total        float | null      deuda total declarada manualmente
pagos_parciales    [{
                     numero:           int       (auto-incremental)
                     monto:            float
                     fecha_pago:       string | null
                     medio_pago:       string | null
                     num_comprobante:  string | null
                     tipo_comprobante: string | null
                     link_constancia:  string | null
                   }]
```

**Campos calculados** (no en BD, añadidos por `webapp/db.py::get_payments()`):
- `cuotas_total` / `cuotas_pagadas` — conteo de cuotas
- `monto_pagado` — suma de pagos_parciales[].monto
- `monto_pendiente` — max(0, monto_total - monto_pagado)
- `nombre_completo` — join de apellidos + nombres del miembro
- `email_principal` — primer email habilitado del miembro

### 7.5 Colección `comprobantes`

Comprobantes de pago emitidos por IPIDET (boletas/facturas) — documentos tributarios entregados al socio o empresa.
`payments.num_comprobante` referencia el campo `numero` de esta colección. El modal `$store.picker` busca en esta colección. → ver también sección 6 (modal picker).

```
numero           string    "B001-000123" — número del comprobante
tipo             string    "boleta" | "factura"
fecha_emision    string    "YYYY-MM-DD"
monto_total      float
producto_nombre  string    nombre del producto/servicio facturado
concepto         string    descripción libre
empresa          string    razón social si es a empresa; vacío si es persona natural
socios           [string]  lista de member_id asociados al comprobante
estado           string    "emitido" | "anulado"  (soft delete: nunca borrar físicamente)
created_at       datetime
```

### 7.6 Colección `companies`

```
nombre       string    nombre comercial (ej. "EY", "KPMG")
ruc          string
razon_social string
tipo         string    "auditora" | "banco" | "estudio" | "empresa" | "otro"
activo       bool
created_at   datetime
```

### 7.7 Colección `facturas_credito`

Facturas emitidas a empresas en modalidad crédito (cobrar después).
Al crear con socios+periodo, `sync_credito_to_cobranzas()` sincroniza el estado en `payments`.

```
empresa           string
numero_factura    string
monto             float
fecha_emision     string    "YYYY-MM-DD"
fecha_vencimiento string    "YYYY-MM-DD"
concepto          string
socios            [string]  member_ids beneficiarios
periodo           string    "2026" | "2025" | ""
estado            string    "pendiente" | "cobrado" | "vencido"
fecha_cobro       string | null
comentarios       [{ texto: string, fecha: "YYYY-MM-DD HH:MM" }]
cuotas            [{
                    numero:     int
                    monto:      float
                    fecha_venc: string | null
                    fecha_pago: string | null
                    estado:     "pendiente" | "pagado"
                  }]
created_at        datetime
```

### 7.8 Colección `productos`

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

Productos seeded al startup:
- Cuota anual Lima 2026 → S/780, `wc_product_id: 8882`
- Cuota anual provincia 2026 → S/350, `wc_product_id: 19105`
- Cuota de fraccionamiento → S/260, `wc_product_id: 8880`
- Entrada a evento → precio variable

### 7.9 Colección `eventos_ipidet`

```
titulo       string
descripcion  string
fecha        string    "YYYY-MM-DD"
hora         string    "HH:MM"
lugar        string
cupo_max     int | null
estado       string    "activo" | "cancelado"
inscritos    [{
               member_id: string,
               nombre:    string,
               asistio:   bool | null
             }]
created_at   datetime
```

### 7.10 Colección `pendientes`

```
titulo           string
descripcion      string
prioridad        string    "alta" | "media" | "baja"
estado           string    "pendiente" | "en_progreso" | "resuelto"
member_id        string | null
nombre_miembro   string | null
created_at       datetime
updated_at       datetime
resuelto_at      datetime | null
```

### 7.11 Colección `users`

```
email           string    (unique, lowercase)
password_hash   string    bcrypt
role            string    "admin" | "viewer"
permisos        [string]  secciones habilitadas (ver sección 14)
active          bool
created_at      datetime
```

### 7.12 Colección `faqs`

```
question    string
answer      string
category    string
active      bool      false = soft delete (preserva historial de aprendizaje)
times_used  int
created_at  datetime
```

### 7.13 Colección `pending_approvals` (agente de email)

```
approval_id       string    UUID corto (8 chars)
email_data        dict      email original completo
response_text     string    respuesta propuesta por el agente
faq_id            string | null   FAQ usada como base
telegram_msg_id   int | null      ID del mensaje en Telegram
status            string    "pending" | "sending" | "sent" | "rejected"
                            | "regenerating" | "awaiting_edit"
kind              string | null   "admin_forward" si es derivación al admin
event_id          string | null   si es respuesta a consulta de evento
is_clarification  bool | null     si es email de repregunta
created_at        datetime
resolved_at       datetime | null
```

---

## 8. Variables de entorno (.env) {#env}

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
WC_WEBHOOK_SECRET           — secret HMAC-SHA256 del webhook WooCommerce (distinto de PORTAL_SECRET)
PORTAL_API_BASE             — URL pública de FastAPI (para pruebas locales)
WC_API_URL                  — URL base de la API REST de WooCommerce
WC_CONSUMER_KEY             — Consumer key de WooCommerce
WC_CONSUMER_SECRET          — Consumer secret de WooCommerce
```

---

## 9. Web admin — templates y rutas {#webapp}

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

Templates no productivos (mockup): `finanzas_mockup.html`.

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
| `POST` | `/billing/{id}/update` | Actualiza pago principal |
| `POST` | `/billing/{id}/emitir-comprobante` | Marca el comprobante como emitido y registra número |
| `POST` | `/billing/{id}/socio/estado` | Cambia estado del socio desde la vista de cobranzas |
| `POST` | `/billing/{id}/cuotas/objetivo` | Define monto objetivo del fraccionamiento |
| `POST` | `/billing/{id}/cuotas/add` | Agrega cuota |
| `POST` | `/billing/{id}/cuotas/{n}/update` | Actualiza cuota |
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
| `GET` | `/comprobantes` | Lista paginada; filtros: search, tipo, empresa, fecha |
| `POST` | `/comprobantes/add` | Crear comprobante nuevo |
| `POST` | `/comprobantes/{id}/update` | Editar comprobante |
| `POST` | `/comprobantes/{id}/delete` | Anular (soft delete: estado="anulado") |
| `GET` | `/api/comprobantes/search` | Búsqueda para el modal picker; params: q, fecha |

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
| `GET` | `/pendientes` | Lista; filtros: estado, prioridad, search |
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
| `GET` | `/marketing` | Stats: activos, deben, por título/ubicación |
| `GET` | `/marketing/export` | Export Excel |

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

## 10. webapp/db.py — funciones clave con firmas {#dbpy}

### Socios

```python
get_stats() → dict
get_members(search="", estado="", pago="", ubicacion="", wp="", page=1, per_page=50) → (docs[], total)
get_member(member_id: str) → dict | None
create_member(apellidos, nombres, titulo="", email="", celular="",
              centro_trabajo="", ubicacion="", fecha_ingreso="",
              notas="", periodo_actual="2026") → str  # devuelve member_id
update_email_status(member_id, email, estado)
set_email_principal(member_id, email)
add_email(member_id, email)
mark_email_bounce(email, bounce_type)
update_member_notes(member_id, notas)
add_comentario(member_id, texto) → ObjectId
delete_comentario(member_id, comentario_id)
update_member_estado(member_id, estado)
update_member_tipo_socio(member_id, tipo_socio)
update_member_dni(member_id, dni)
update_member_wp_user_id(member_id, wp_user_id)
auto_set_wp_user_id(member_id, wp_user_id)
```

### Cobranzas

```python
generar_cobros_periodo(periodo: str) → dict  # {"creados": n, "existentes": n}
get_payments(periodo="", estado="", empresa="", search="",
             comprobante_emitido="", page=1) → (docs[], total)
get_payments_export(...) → docs[]  # sin paginación
update_payment(payment_id: str, estado: str, empresa=None, fecha_pago=None,
               medio=None, pagado_por=None, num_comprobante=None,
               tipo_comprobante=None, link_constancia=None, banco_origen=None,
               comprobante_emitido=None, fecha_emision_comprobante=None)
set_monto_objetivo(payment_id: str, monto_objetivo: float)
get_or_create_payment(member_id: str, periodo: str) → str  # devuelve payment_id
```

### Cuotas de fraccionamiento

```python
# Llama _sync_estado_from_cuotas() internamente — no llamarla por separado
add_cuota(payment_id: str, monto: float, fecha_venc: str = None)
update_cuota(payment_id: str, numero: int, estado: str, fecha_pago=None,
             medio_pago=None, num_comprobante=None, tipo_comprobante=None,
             link_constancia=None, banco_origen=None, monto=None, fecha_venc=None)
delete_cuota(payment_id: str, numero: int)
```

### Pagos parciales

```python
# Llama _sync_estado_from_parciales() internamente — no llamarla por separado
set_monto_total(payment_id: str, monto_total: float)
add_pago_parcial(payment_id: str, monto: float, fecha_pago=None, medio=None,
                 num_comprobante=None, tipo_comprobante=None,
                 link_constancia=None, banco_origen=None)
update_pago_parcial(payment_id: str, numero: int, monto=None, fecha_pago=None,
                    medio=None, num_comprobante=None, tipo_comprobante=None,
                    link_constancia=None, banco_origen=None)
delete_pago_parcial(payment_id: str, numero: int)
```

### Facturación

```python
get_comprobantes_pendientes(periodo: str, search: str = "") → docs[]
emitir_comprobante(payment_id: str, tipo: str,  # tipo: "principal"|"cuota"|"parcial"
                   numero: int | None, num_comprobante: str,
                   tipo_comprobante: str, fecha_emision: str = "")
emitir_comprobante_batch(items: list, num_comprobante: str,
                         tipo_comprobante: str, fecha_emision: str)
mark_payment_empresa(payment_id: str, empresa: str, num_comprobante: str,
                     tipo_comprobante: str, fecha_emision: str)
```

### Comprobantes

```python
get_comprobantes(search="", tipo="", empresa="", fecha="", page=1) → (docs[], total)
create_comprobante(numero: str, tipo: str, fecha_emision: str, monto_total: float,
                   producto_nombre: str, concepto: str, empresa: str,
                   socios: list) → str  # devuelve comprobante_id
update_comprobante(comprobante_id: str, fields: dict)
  # fields permitidos: numero, tipo, fecha_emision, monto_total,
  #   producto_nombre, concepto, empresa, socios, estado
delete_comprobante(comprobante_id: str)  # soft delete: estado="anulado"
get_comprobante_stats() → dict
```

### Crédito empresa

```python
get_facturas_credito(empresa="", estado="") → docs[]
create_factura_credito(empresa: str, numero_factura: str, monto: float,
                       fecha_emision: str, fecha_vencimiento: str,
                       concepto="", socios=None, periodo="") → str
update_factura_credito_estado(factura_id: str, estado: str, fecha_cobro="")
  # llama sync_credito_to_cobranzas() automáticamente
update_factura_credito_fields(factura_id: str, fields: dict)
delete_factura_credito(factura_id: str)
add_comentario_credito(factura_id, texto)
delete_comentario_credito(factura_id, idx)
add_cuota_credito(factura_id: str, monto: float, fecha_venc: str) → dict
update_cuota_credito(factura_id: str, numero: int, estado: str, fecha_pago="")
delete_cuota_credito(factura_id: str, numero: int)
get_credito_stats() → dict
sync_credito_to_cobranzas(factura_id: str)  # sincroniza estado en payments
```

### Vista fraccionamientos

```python
get_fraccionamientos(periodo="", alerta="", search="", page=1) → (docs[], total)
```

### Ingresos

```python
get_ingresos(fecha_desde="", fecha_hasta="", periodo="", empresa="",
             medio="", tipo_fecha="pago", ...) → dict
```

### Empresas

```python
get_all_companies() → list
get_companies(search="") → list
add_company(nombre, ruc, razon_social, tipo) → str
update_company(company_id, fields)
delete_company(company_id)
```

### Productos

```python
get_productos(tipo="", solo_activos=False) → list
create_producto(nombre, tipo, precio, periodo, descripcion, wc_product_id=None) → str
update_producto(producto_id, fields)
delete_producto(producto_id)
```

### Comunicaciones / Marketing

```python
get_marketing_stats(periodo="") → dict
get_marketing_emails(titulo="", ubicacion="", ...) → list
get_comunicacion_destinatarios(...) → list
save_comunicacion_log(asunto, plantilla, filtros, ...) → id
get_comunicaciones_history(limit=50) → list
get_bounced_emails() → list
mark_email_bounce(email, bounce_type)
```

### Eventos

```python
get_eventos(search="", estado="") → list
get_evento(evento_id) → dict | None
create_evento(titulo, descripcion, fecha, hora, lugar, cupo_max=None) → str
update_evento(evento_id, fields)
delete_evento(evento_id)  # soft delete: estado="cancelado"
inscribir_socio(evento_id, member_id, nombre)
desinscribir_socio(evento_id, member_id)
marcar_asistencia(evento_id, member_id, asistio: bool)
get_eventos_proximos() → list
get_evento_stats(evento_id) → dict
```

### Pendientes

```python
get_pendientes(estado="", prioridad="", search="") → list
create_pendiente(titulo, descripcion, prioridad, member_id=None) → str
update_pendiente(pendiente_id, fields)
delete_pendiente(pendiente_id)
get_pendientes_stats() → dict
```

### WooCommerce

```python
get_wc_payment(order_id) → dict | None
get_wc_payment_by_wp_user_id(wp_user_id, periodo) → dict | None
vincular_wp_usuario(wc_email, member_id, wp_user_id)
vincular_wc_order(order_id, member_id, wc_email, periodo, cuota_numero)
get_wc_payment_by_email(email, periodo) → dict | None
```

### FAQs

```python
get_faqs() → list
get_faq_categories() → list[str]
save_faq(question, answer, category) → str
delete_faq(faq_id)  # soft delete: active=False
```

### Utils

```python
get_or_create_payment(member_id: str, periodo: str) → str  # payment_id
get_member_titulos() → list[str]
get_member_ubicaciones() → list[str]
```

---

## 11. Flujo del agente de email {#agente}

1. `gmail/client.py` — polling IMAP cada 60s, obtiene emails nuevos por UID
2. `classifier/engine.py` — Claude clasifica intención del email (confianza 0–1)
3. `workflows/incoming.py` — decide: auto-responder (> `CONFIDENCE_THRESHOLD`), escalar a admin, o ignorar
4. `workflows/approvals.py` — si confianza baja, Telegram pide aprobación al admin; la respuesta aprobada se envía vía SMTP
5. Respuesta aprobada → `workflows/learning.py` → guarda como FAQ si es recurrente

Colecciones usadas por el agente (en `knowledge_base/db.py`): `pending_approvals`, `pending_clarifications`, `processed_emails`, `bot_state`. No compartidas con `webapp/db.py`.

---

## 12. Portal de socios (WordPress / WooCommerce) {#portal}

Flujo server-to-server: WordPress llama a FastAPI para verificar si un email es socio activo antes de dar acceso al contenido del portal.

1. WP hace `GET /api/portal/member-status?email=X` con header `Authorization: Bearer <PORTAL_SECRET>`
2. FastAPI busca el socio por email en MongoDB y devuelve su estado + historial de pagos
3. Cuando un socio paga en WooCommerce, WC envía `POST /webhook/woocommerce/order`
4. FastAPI verifica la firma HMAC-SHA256 con `WC_WEBHOOK_SECRET`, identifica el producto por `wc_product_id`, y actualiza el pago en MongoDB

`webapp/wc_client.py` maneja llamadas directas a la API REST de WooCommerce (sync de usuarios, consulta de pedidos, etc.).

**`PORTAL_SECRET` y `WC_WEBHOOK_SECRET` son dos secrets distintos** — no intercambiar.

---

## 13. Comunicaciones masivas (Brevo) {#brevo}

`webapp/mailer.py` envía emails via Brevo SMTP. Los bounces (hard y soft) llegan via webhook a `POST /webhook/brevo/bounce` y se registran en `members.emails[].estado = "rebotado"`. La vista `/comunicaciones/rebotes` muestra el historial.

El agente de email consulta solo emails con `estado: "habilitado"`. Los rebotes de Brevo marcan el email automáticamente.

Filtros disponibles al enviar: título, ubicación, estado de socio, estado de pago, periodo.

---

## 14. Autenticación web {#auth}

Toda la plataforma requiere login (excepto `/login`, `/webhook/*`). Las sesiones se almacenan en cookies firmadas con `SESSION_SECRET`. El rol `admin` tiene acceso a `/admin/users`; los usuarios con rol `viewer` solo acceden a las secciones en su lista `permisos`.

**Secciones con control de acceso:** `dashboard`, `members`, `billing`, `fraccionamientos`, `finanzas`, `faqs`, `marketing`, `comunicaciones`, `eventos`, `pendientes`.

Las rutas `/`, `/api/*`, `/webhook/*` y `/comprobantes` son accesibles a cualquier usuario autenticado.

El admin inicial se crea automáticamente al startup si `users_col` está vacía y están definidos `ADMIN_EMAIL` y `ADMIN_PASSWORD` en el entorno.

---

## 15. Importación del padrón {#padron}

> **⚠️ DESTRUCTIVO:** Hace drop completo de `members` y `payments` antes de reimportar.

`import_padron.py` lee el Excel (columnas fijas del padrón IPIDET) y reimporta todo desde cero.

Ruta hardcodeada del Excel: `C:\Users\Juan\Downloads\PADRÓN - JULIO.xlsx`. Actualizar si cambia el archivo.

Normalización que aplica:
- `member_id` ← `"IPIDET-" + No_REGISTRO`. Sin número válido → `"IPIDET-?"`.
- Emails separados por `;` o `,` → array; el primero queda `principal: true`.
- Estado de pago desde texto libre: "PAGADO EY" → `estado: "pagado", empresa: "EY"`.
- Ubicaciones y títulos se normalizan a valores canónicos.

---

## 16. Decisiones de diseño relevantes {#diseno}

- **Múltiples `<tbody>` por tabla:** En `billing.html` cada pago usa su propio `<tbody x-data='billingRow(...)'>` para compartir estado Alpine.js entre el `<tr>` de datos y el `<tr>` de edición. → ver R2.
- **`_sync_estado_from_cuotas` nunca cambia a `"pagado"`:** Ese cambio es siempre manual. → ver sección 7.2 para la máquina de estados completa.
- **Soft delete en FAQs:** `active: false` desactiva sin borrar, para mantener historial de aprendizaje del agente.
- **Soft delete en comprobantes:** `estado: "anulado"` en lugar de borrar físicamente.
- **`wp_user_id` en members:** Vincula el socio con su usuario de WordPress. Se auto-asigna cuando WooCommerce envía un pedido con `customer_id` reconocido. También se puede setear manualmente desde `/sync-usuarios`.
- **`payments.num_comprobante` vs colección `comprobantes`:** `num_comprobante` en payments es el número de comprobante recibido (texto libre o referencia). La colección `comprobantes` es el registro formal de los comprobantes que IPIDET emite. El modal picker vincula ambos. → ver sección 7.5.
- **`sync_credito_to_cobranzas`:** Se llama automáticamente desde `create_factura_credito` y `update_factura_credito_estado`. Sincroniza el estado de `payments` de los socios vinculados a la factura de crédito.
- **`certifi` en Atlas:** → ver R6.
- **Paginación por defecto:** `per_page=50` en `get_members`; `page_size=50` implícito en la mayoría de endpoints paginados. El parámetro se llama `page` (1-indexed).
- **`MEDIOS_PAGO` y `BANCOS`:** Listas canónicas definidas en `webapp/db.py` al inicio del archivo. Usarlas en cualquier formulario o validación.
