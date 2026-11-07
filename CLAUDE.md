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
17. [Público / Prospectos CRM](#publico)
18. [Redes Sociales](#redes)
19. [Facturación electrónica SUNAT](#sunat)
20. [Zoom](#zoom)
21. [Campañas y envíos programados](#campanias)
22. [Plantillas de email guardadas](#plantillas)
23. [Constancias de membresía](#constancias)
24. [IA Tareas](#ia-tareas)
25. [Alianzas](#alianzas)
26. [Ponentes](#ponentes)

---

## 1. Cómo levantar los servicios {#levantar}

```bash
# Web admin (FastAPI en http://localhost:8000)
python platform_main.py

# Agente de email (loop continuo, revisa Gmail cada 60s)
python main.py

# Importar padrón desde Excel a MongoDB  ⚠️ DESTRUCTIVO — ver sección 15
python import_padron.py

# Backfill de FKs en documentos históricos (idempotente, seguro correr varias veces)
python migrate_fks.py

# Crea catálogos, organización y productos WC faltantes (idempotente; no pisa ediciones)
python migrate_catalogos.py
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

**Patrón en `portal_db.py` (cross-module, sin acceso a las funciones privadas de `webapp/db.py`):**

`portal_db.py` escribe directamente en MongoDB sin poder llamar a `_sync_estado_from_cuotas()`. La solución correcta es hacer el sync condicionalmente en dos operaciones separadas:

```python
# 1. Primero push la cuota
payments_col.update_one(
    {"member_id": member_id, "periodo": periodo},
    {"$push": {"cuotas": nueva_cuota}},
)
# 2. Luego sincronizar estado — solo si aún no es "fraccionamiento"
pay_doc = payments_col.find_one(
    {"member_id": member_id, "periodo": periodo},
    {"_id": 1, "estado": 1, "cuotas": 1},
)
if pay_doc and pay_doc.get("cuotas") and pay_doc.get("estado") != "fraccionamiento":
    payments_col.update_one(
        {"_id": pay_doc["_id"]},
        {"$set": {"estado": "fraccionamiento"}},
    )
```

Nunca combinar `$push` y `$set: {estado: ...}` en una sola operación atómica — eso viola R3 porque fuerza el estado sin verificar las cuotas existentes.

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

### R8 — Sin inferencias: toda sincronización via FK directa

`sync_comprobante_to_payments` **solo opera via FK directa**. Cada ítem debe incluir `payment_id` + `tipo_pago` explícitos. Sin ellos, el ítem queda sin sincronizar y se devuelve `status: "no_fk"` — **nunca se infiere el payment por nombre de producto, código SUNAT ni período**.

El slow path de inferencia fue eliminado. Si al registrar un comprobante la UI muestra ítems con `status: "no_fk"`, significa que el comprobante no está vinculado a ningún pago — el operador debe ir a Cobranzas y vincular manualmente con el picker de comprobantes.

Esta regla aplica a cualquier función que llame `sync_comprobante_to_payments`, a cualquier nueva ruta que cree comprobantes, y a cualquier migración o script de backfill.

### R7 — FKs en colecciones: siempre pasar `comprobante_id` y `empresa_id`

Los campos de texto `num_comprobante` y `empresa_pagadora` tienen ahora un campo FK paralelo (`comprobante_id` y `empresa_id`) que apunta al `_id` del documento relacionado. Al escribir en estas funciones, siempre pasar el FK cuando se conoce:

```python
# ✓ Correcto — FK directa
pdb.update_payment(payment_id, estado="pagado",
                   num_comprobante="B001-001", comprobante_id=comp_id,
                   empresa="EY")  # empresa_id se resuelve automáticamente

pdb.update_cuota(payment_id, numero=1, estado="pagado",
                 num_comprobante="B001-001", comprobante_id=comp_id)

# ✗ Evitar — solo texto, sin FK
pdb.update_payment(payment_id, estado="pagado", num_comprobante="B001-001")
```

- `empresa_id` se auto-resuelve dentro de `update_payment` y `create_comprobante` cuando se pasa `empresa`.
- `comprobante_id` debe pasarse explícitamente desde la ruta que acaba de crear el comprobante.
- Si hay documentos históricos sin FK, correr `python migrate_fks.py` para backfill antes de sincronizar.

### R10 — Nada de datos de negocio en el código: todo sale de la base de datos

Si un valor puede cambiar sin que cambie la lógica (período, precio, producto, ID de WooCommerce, banco, medio de pago, tipo de socio, tipo de empresa, checklist, email de contacto, firmante...), **no se escribe en Python ni en un template**. Se lee de su colección o de `config/catalogos.py`, y se edita desde la plataforma (`/configuracion`, `/productos`, `/empresas`).

| Necesitas | Usa | Nunca |
|-----------|-----|-------|
| Período de trabajo | `catalogos.get_periodo_actual()` / `{{ periodo_actual }}` | `"2026"`, `pago_2026`, `periodo: str = "2026"` |
| Lista de períodos | `{{ periodos }}` (todos) o `{{ periodos_cobro }}` (con cuota activa) | `["2025","2026"]` en un template |
| Producto de WooCommerce | `portal_db.get_wc_product_map()` / `get_portal_products()` | diccionarios de IDs en el código |
| Nombre del producto de un pago | `pdb.get_nombre_producto_de_pago(pago)` | `"Cuota anual"` escrito a mano |
| Bancos, tipos de socio, etc. | `catalogos.get_catalogo(key)` / `{{ bancos }}`, `{{ tipos_socio }}`, `{{ tipos_empresa }}` | `<option>` fijos o listas `_TIPOS_*` |
| Email, web, teléfono, firmante | `catalogos.get_organizacion()` / `{{ org.email }}` | `"administracion@ipidet.org"` |
| Etiqueta de un estado de pago | `{{ status_labels[estado][0] }}` | otro diccionario de estados |

**Qué sí va en código:** la lógica que el programa ejecuta — la máquina de estados (`catalogos.ESTADOS_PAGO`), los tipos de producto y las reglas asociadas. Se define una sola vez.

**Tampoco se permiten:** seeds que pisen datos al arrancar, datos de ejemplo en pantallas productivas, ni dos colecciones para lo mismo. Para cargar datos nuevos en bloque se usa un script `migrate_*.py` idempotente.

Un filtro de período se resuelve con `periodo: str | None = None`: `None` es el período de trabajo y `""` significa «todos».

### R11 — Sistema de diseño: usar los componentes compartidos

Los estilos viven en `webapp/static/ipidet.css` y la configuración de Tailwind en `webapp/templates/_head.html`. **Nunca usar `@apply`**: un `<style>` normal no lo procesa y todos los botones, campos y etiquetas pierden su estilo.

| Necesitas | Usa | Nunca |
|-----------|-----|-------|
| Acción principal | `btn-primary` (o `bg-brand-700 hover:bg-brand-800`) | `bg-blue-600`, `bg-violet-600`, tarjetas gigantes como botón |
| Acción secundaria / destructiva | `btn-ghost` / `btn-danger` | otro color por pantalla |
| Campo de formulario | `input` | bordes y anillos escritos a mano |
| Estado | `badge badge-green\|red\|amber\|blue\|gray...` | pastillas con colores sueltos |
| Dinero | `{{ monto \| soles }}` o `{{ monto \| soles(0) }}` | `S/ {{ monto }}`, `"%.2f"\|format` |
| Fecha en pantalla | `{{ fecha \| format_fecha }}` / `fechaPe(fecha)` en Alpine | mostrar `2026-10-31` |
| Confirmación | `<form data-confirm="¿Seguro?">` o `await confirmar(texto)` | `confirm()` |
| Pedir un dato | `await solicitar(texto, valor)` | `prompt()` |
| Avisar | `avisar(texto)` / `avisar(texto, 'error')` | `alert()` (queda redirigido a `avisar`) |
| Título de página | `{% block heading %}` y acciones en `{% block header_actions %}` | `<h1>` dentro del contenido |
| Pestañas de cobranzas | `{% include "_tabs_cobranzas.html" %}` | copiar la barra de pestañas |

**Significado del color:** verde marca = acción; azul = enlace e información; ámbar = advertencia o pendiente; rojo = error, deuda o acción destructiva. Los iconos de título van en gris: el color se reserva para los estados.

**Otras reglas:**
- Solo iconos gratuitos de Font Awesome 6.5: los de pago se ven vacíos sin dar error.
- Una tabla ancha va en un contenedor `overflow-x-auto`, nunca `overflow-hidden`. Con filtros desplegables dentro: `overflow-x-auto lg:overflow-visible`.
- Un POST que redirige (303) muestra «Cambios guardados» automáticamente; una ruta que quiera su propio mensaje redirige con `?ok=`, `?msg=` o `?error=`.
- Los contadores deben mostrar el total real, no solo los registros de la página visible.

---

## 3. Archivos más frecuentemente modificados {#frecuentes}

El 90 % de las tareas toca estos archivos:

| Archivo | Qué contiene |
|---------|-------------|
| `webapp/app.py` | Todas las rutas FastAPI (~60 rutas) |
| `webapp/db.py` | Todas las queries MongoDB para la web (~2300 líneas) |
| `webapp/templates/billing.html` | Vista de cobranzas: la más compleja, con cuotas y parciales |
| `webapp/templates/fraccionamientos.html` | Vista de fraccionamientos: creación, calendario, emitir comprobante |
| `webapp/templates/base.html` | Layout, menú, búsqueda global, diálogos, avisos, modal picker |
| `webapp/static/ipidet.css` / `ipidet.js` | Sistema de diseño y componentes compartidos (ver R11) |
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
| Nuevo período o producto WooCommerce | Solo datos: crear el producto en `/productos` con su ID de WC. **No requiere código** |
| Nuevo catálogo editable | `config/catalogos.py` (`CATALOGOS`) → se edita solo en `/configuracion` |
| Nueva integración WooCommerce | `webapp/portal_db.py` + `webapp/portal_router.py` → `webapp/app.py` (webhook route) |
| Crear/emitir comprobante desde payment | Crear comprobante primero → pasar `comp_id` a `emitir_comprobante()` / `update_cuota()` — ver R7 |
| Nuevo fraccionamiento | `webapp/app.py` (`POST /fraccionamientos/nuevo`) → `fraccionamientos.html` (modal) |

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
  catalogos.py       — Períodos, catálogos editables y datos de la organización (ver R10)

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
  app.py             — Rutas FastAPI (todas las páginas y APIs; ~170 rutas)
  db.py              — Consultas MongoDB para la web admin (~5000 líneas)
  auth.py            — Autenticación con bcrypt + sessions; colección users
  mailer.py          — Envío de emails via Brevo API + historial de envíos
  portal_db.py       — Consultas para el portal de socios + lógica WooCommerce
  portal_router.py   — Handlers del portal: build_member_status, handle_wc_webhook
  scheduler.py       — Tareas periódicas (reminders, alertas, envíos programados)
  wc_client.py       — Cliente HTTP para la API REST de WooCommerce
  zoom_client.py     — Server-to-Server OAuth Zoom: listar webinars, sincronizar registrantes
  sunat_client.py    — Facturación electrónica SUNAT vía APISPERU (UBL 2.1)
  pdf_builder.py     — Generación de PDFs de comprobantes con fpdf2
  xml_parser.py      — Parseo de XMLs de comprobantes SUNAT (para importar pre-comprobantes)
  constancia.py      — Generación de constancias de membresía en PDF
  mongo_client.py    — Singleton del cliente MongoDB (importado por db.py y auth.py)
  templates/         — Ver sección 9

sync_redes.py        — Scrapper Playwright (usa Brave) para métricas de RRSS; corre localmente
                       vía Windows Task Scheduler (no en Render). Setup: python sync_redes.py --setup
admin.py             — Script de administración CLI (utilidades)
migrate_fks.py       — Backfill de FKs en documentos históricos (idempotente)
telegram_main.py     — Punto de entrada del bot de Telegram (independiente)
debug_imap.py        — Herramienta de diagnóstico IMAP
launcher.py          — Lanzador de procesos
```

---

## 6. Stack tecnológico — Frontend {#frontend}

- **Tailwind CSS 3.4.17** (CDN, versión fija) — estilos utility-first; colores en `_head.html` (ver R11)
- **Alpine.js 3.14.9** (CDN, versión fija) — reactividad declarativa en templates
- **IBM Plex Sans / Mono y Newsreader** (Google Fonts) — interfaz, códigos y títulos
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
| `companies_col` | `companies` | `webapp/db.py` | Empresas empleadoras |
| `credito_col` | `facturas_credito` | `webapp/db.py` | Crédito empresa (facturas a cobrar) |
| `productos_col` | `productos` | `webapp/db.py` | Productos facturables (cuotas, eventos) |
| `comprobantes_col` | `comprobantes` | `webapp/db.py` | Comprobantes emitidos (boletas/facturas) |
| `comunicaciones_col` | `comunicaciones` | `webapp/db.py` | Historial de emails masivos |
| `eventos_col` | `eventos_ipidet` | `webapp/db.py`, `knowledge_base/db.py` | Eventos y capacitaciones (web, agente y Telegram usan esta misma colección) |
| `pendientes_col` | `pendientes` | `webapp/db.py` | Tareas internas del equipo admin |
| `config_col` | `config` | `config/catalogos.py` | Catálogos editables, organización y período de trabajo (ver R10) |
| `publico_col` | `publico` | `webapp/db.py` | CRM de prospectos/público general |
| `alianzas_col` | `alianzas` | `webapp/db.py` | Instituciones para posibles alianzas (ver sección 25) |
| `alianzas_corridas_col` | `alianzas_corridas` | `webapp/db.py` | Historial de búsquedas: resultado, uso y costo estimado |
| `ponentes_col` | `ponentes` | `webapp/db.py` | Directorio de ponentes, internos y de otras instituciones (ver sección 26) |
| `ponentes_corridas_col` | `ponentes_corridas` | `webapp/db.py` | Historial de búsquedas de ponentes |
| `redes_cuentas_col` | `redes_cuentas` | `webapp/db.py` | Cuentas de redes sociales registradas |
| `redes_metricas_col` | `redes_metricas` | `webapp/db.py` | Métricas históricas de RRSS (seguidores, etc.) |
| `redes_posts_col` | `redes_posts` | `webapp/db.py` | Posts publicados en RRSS |
| `redes_cronograma_col` | `redes_cronograma` | `webapp/db.py` | Calendario de publicaciones planificadas |
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
linkedin         string    opcional; URL normalizada por db.normalizar_linkedin
created_at       datetime
```

### 7.4 Colección `payments`

Un documento por socio por período.

```
member_id               string    FK → members.member_id
periodo                 string    "2025" | "2026"
producto_id             string | null    FK → productos._id  (cuota_anual o cuota_provincia del período; asignado por generar_cobros_periodo y backfill migrate_fks.py)
estado                  string    ver sección 7.2 para estados válidos y transiciones
empresa_pagadora        string | null    "EY" | "BDO" | "PWC" | "KPMG" | "PPU"
empresa_id              string | null    FK → companies._id  (auto-resuelto al setear empresa_pagadora)
pagado_por              string | null    texto libre o "WC#<order_id>" si vino de WooCommerce
fecha_pago              string | null    "YYYY-MM-DD"
medio_pago              string | null    catálogo `medios_pago` en /configuracion
banco_origen            string | null    catálogo `bancos` en /configuracion
num_comprobante         string | null    número del comprobante (texto display)
comprobante_id          string | null    FK → comprobantes._id  (ver R7)
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
                     comprobante_id:   string | null   FK → comprobantes._id
                     tipo_comprobante: string | null
                     link_constancia:  string | null
                     producto_nombre:  string | null   nombre del producto asociado
                   }]

# Solo si estado == "parcial"
monto_total        float | null      deuda total declarada manualmente
pagos_parciales    [{
                     numero:           int       (auto-incremental)
                     monto:            float
                     fecha_pago:       string | null
                     medio_pago:       string | null
                     num_comprobante:  string | null
                     comprobante_id:   string | null   FK → comprobantes._id
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
`payments.comprobante_id` apunta al `_id` de esta colección (FK directa). `payments.num_comprobante` mantiene el número como texto para display. El modal `$store.picker` busca en esta colección. → ver también sección 6 (modal picker).

```
numero           string    "B001-000123" — número del comprobante
tipo             string    "boleta" | "factura" | "recibo"
fecha_emision    string    "YYYY-MM-DD" — fecha del documento tributario
fecha_pago       string    "YYYY-MM-DD" — fecha en que se recibió el pago (puede diferir de fecha_emision)
fecha_carga      string    "YYYY-MM-DD" — fecha en que se registró en la plataforma
monto_total      float
producto_nombre  string    nombre del producto/servicio facturado (denormalizado)
concepto         string    descripción libre
empresa          string    razón social si es a empresa; vacío si es persona natural
empresa_id       string | null    FK → companies._id  (auto-resuelto al crear/editar)
ruc_empresa      string    RUC de la empresa (denormalizado desde companies)
socios           [string]  lista de member_id (denormalizado desde items)
items            [{        líneas de detalle del comprobante
                   member_id:    string | null   FK → members.member_id
                   producto_nombre: string
                   monto:        float
                   payment_id:   string | null   FK → payments._id  (ver R7)
                   tipo_pago:    string | null   "principal" | "cuota" | "parcial"
                   cuota_numero: int | null      número de cuota si tipo_pago == "cuota"
                   codigo_sunat: string | null   código SUNAT del producto
                 }]
estado              string    "emitido" | "anulado" | "pre_registrado"
                              "pre_registrado" = borrador sin número SUNAT (se completa al importar XML)
destinatario_id     string    member_id del receptor (solo boleta, para pre-registro con DNI)
destinatario_nombre string    nombre del receptor persona natural
destinatario_dni    string    DNI del receptor (para matching XML → pre-comprobante)
created_at          datetime
```

**Comportamiento de sync (`sync_comprobante_to_payments`):**
- **Fast path**: si `items[].payment_id` + `items[].tipo_pago` están presentes → FK directa, O(1). Único camino correcto para documentos nuevos.
- **Slow path** (legacy para `migrate_fks.py`): si faltan → inferencia por `codigo_sunat` / `producto_nombre` → producto → período → payment.
- Propaga `fecha_pago` del comprobante al campo `fecha_pago` del payment vinculado. Si `fecha_pago` está vacío, usa `fecha_emision` como fallback.
- Se llama automáticamente al crear (`/comprobantes/add`, `/comprobantes/import-xml`) y al editar (`/comprobantes/{id}/update`).
- **No hay botón "Sincronizar" en la UI** — el sync es siempre automático.

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
empresa_id        string | null    FK → companies._id  (auto-resuelto al crear/editar)
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

No hay seeds al arrancar: los productos se administran en `/productos`. `wc_product_id` es la única correspondencia con WooCommerce — un producto con `periodo` vacío es genérico (se aplica al período pendiente más antiguo del socio). Los genéricos van con `activo: false`: quedan fuera de los selectores de facturación y del portal, pero el webhook los reconoce.

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
BREVO_API_KEY               — HTTP API key de Brevo (canal principal en producción; ver R9)
BREVO_SMTP_USER             — Login SMTP de Brevo (fallback local, no configurado en Render)
BREVO_SMTP_PASS             — Password SMTP de Brevo (fallback local)
BREVO_WEBHOOK_TOKEN         — Token para verificar webhooks de rebotes
BREVO_FROM_EMAIL            — Remitente (default: administracion@ipidet.org)
BREVO_FROM_NAME             — Nombre del remitente (default: IPIDET)

# Portal de socios (WordPress ↔ FastAPI)
PORTAL_SECRET               — secret Bearer para autenticar llamadas WP→FastAPI
WC_WEBHOOK_SECRET           — secret HMAC-SHA256 del webhook WooCommerce (distinto de PORTAL_SECRET)
PORTAL_API_BASE             — URL pública de FastAPI (para pruebas locales)
WC_API_KEY                  — Consumer key WooCommerce REST API
WC_API_SECRET               — Consumer secret WooCommerce REST API
WC_STORE_URL                — URL tienda (default: https://ipidet.org)

# SUNAT / APISPERU — facturación electrónica (UBL 2.1)
APISPERU_TOKEN              — Bearer token company-level (no expira)
IPIDET_RUC                  — RUC de IPIDET (11 dígitos)
IPIDET_RAZON_SOCIAL         — Razón social legal
IPIDET_NOMBRE_COMERCIAL     — Nombre comercial
IPIDET_DIRECCION            — Dirección fiscal
IPIDET_UBIGUEO              — Código ubigeo (default: 150101 Lima Centro)
SUNAT_ENVIRONMENT           — "beta" | "produccion"

# Zoom Server-to-Server OAuth (webinars)
ZOOM_ACCOUNT_ID             — Account ID de la app Zoom
ZOOM_CLIENT_ID              — Client ID de la app Zoom
ZOOM_CLIENT_SECRET          — Client Secret de la app Zoom
```

---

## 9. Web admin — templates y rutas {#webapp}

### Templates activos

| Template | Ruta principal | Descripción |
|----------|---------------|-------------|
| `base.html` | — | Layout base: menú, barra superior, búsqueda global (Ctrl+K), diálogos, avisos, modal picker |
| `_head.html` | — | Fuentes, Tailwind y estilos; lo incluyen `base.html` y `login.html` |
| `_tabs_cobranzas.html` | — | Pestañas compartidas de cobranzas |
| `dashboard.html` | `/` | Inicio: avance de cobranza, lo que requiere atención, próximos eventos |
| `members.html` | `/members` | Padrón paginado con filtros |
| `member.html` | `/members/{id}` | Detalle de socio: emails, comentarios, pagos |
| `billing.html` | `/billing` | Cobranzas: tabla, edición inline, cuotas, parciales |
| `fraccionamientos.html` | `/fraccionamientos` | Fraccionamientos agrupados por socio; períodos como sub-filas; modal de cuotas por período |
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
| `configuracion.html` | `/configuracion` | Período de trabajo, organización y catálogos editables |
| `wc_orders.html` | `/wc-orders` | Pedidos WooCommerce: vincular a socios |
| `sync_usuarios.html` | `/sync-usuarios` | Sync WP users ↔ socios IPIDET |
| `rebotes.html` | `/comunicaciones/rebotes` | Emails rebotados (bounces de Brevo) |
| `campanas.html` | `/comunicaciones/campanas` | Lista de campañas de email por lotes |
| `campana_detalle.html` | `/comunicaciones/campanas/{id}` | Detalle + envío por lotes de una campaña |
| `comprobantes_preparar.html` | `/comprobantes/preparar` | Pre-registro y completado de comprobantes SUNAT |
| `publico.html` | `/publico` | CRM de prospectos: tabla con filtros en encabezados |
| `publico_detalle.html` | `/publico/{id}` | Detalle de prospecto: tags, historial, postulación, conversión |
| `redes_dashboard.html` | `/redes` | Dashboard de redes sociales: métricas por cuenta |
| `redes_posts.html` | `/redes/posts` | Posts publicados con filtros |
| `redes_cronograma.html` | `/redes/cronograma` | Calendario de publicaciones planificadas |
| `inbox.html` | `/inbox` | Bandeja unificada: aprobaciones del agente + comprobantes de pago recibidos por email |
| `alianzas.html` | `/alianzas` | Buscador de instituciones, sustento y seguimiento de alianzas |
| `ponentes.html` | `/ponentes` | Directorio y búsqueda de ponentes; seguimiento y vínculo con eventos |
| `login.html` | `/login` | Login de la plataforma |
| `users.html` | `/admin/users` | Gestión de usuarios (solo admin) |

### Rutas completas — webapp/app.py

#### Dashboard y socios

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/` | Dashboard con stats |
| `GET` | `/members` | Padrón paginado; filtros: search, estado, pago, ubicacion |
| `POST` | `/members/nuevo` | Crear socio nuevo (LinkedIn opcional) |
| `POST` | `/members/{id}/update` | Editar la ficha; 400 si el LinkedIn no es un enlace de perfil |
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
| `GET` | `/fraccionamientos` | Vista agrupada por socio; filtros: search, alerta, periodo (opcional — default = todos) |
| `POST` | `/fraccionamientos/nuevo` | Crea fraccionamiento + cuotas en batch desde el wizard del modal |
| `POST` | `/fraccionamientos/{id}/cuotas/generar` | Genera calendario de cuotas automático para un fraccionamiento existente |
| `POST` | `/fraccionamientos/{id}/cuotas/{n}/emitir-comprobante` | Crea comprobante formal + marca cuota como pagada (atómico) |
| `GET` | `/api/fraccionamientos/recordatorios` | JSON con preview de recordatorios para socios con cuotas vencidas; param: periodo |
| `POST` | `/fraccionamientos/enviar-recordatorios` | Envía recordatorios vía Brevo; body: `{selected: [member_ids], periodo}` |

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
| `GET` | `/configuracion` | Período de trabajo, organización y catálogos (permiso `billing`) |
| `POST` | `/configuracion/catalogos/{key}` | Guarda un catálogo; body: `{valores}` |
| `POST` | `/configuracion/organizacion` | Guarda los datos de la organización |
| `POST` | `/configuracion/periodo` | Fija el período de trabajo; vacío = automático |
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

#### Alianzas

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/alianzas` | Buscador; filtros: search, tipo, ambito, estado, orden |
| `GET` | `/alianzas/estado` | JSON: si hay una búsqueda en curso |
| `POST` | `/alianzas/buscar` | Lanza una búsqueda en segundo plano; consume créditos de la API |
| `POST` | `/alianzas/nueva` | Agrega una institución a mano |
| `POST` | `/alianzas/configuracion` | Perfil, criterios, frecuencia y activación |
| `POST` | `/alianzas/{id}/seguimiento` | Etapa, responsable, fecha y notas |

#### Ponentes

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/ponentes` | Directorio; filtros: search, estado, ambito, evento, origen, orden |
| `GET` | `/ponentes/estado` | JSON: si hay una búsqueda en curso |
| `POST` | `/ponentes/buscar` | Búsqueda en segundo plano; con `evento_id` busca para ese evento |
| `POST` | `/ponentes/nuevo` | Agrega un ponente a mano |
| `POST` | `/ponentes/configuracion` | Público, temas, frecuencia y activación |
| `POST` | `/ponentes/{id}/seguimiento` | Etapa, responsable, fecha y notas |
| `POST` | `/ponentes/{id}/agregar-evento` | Lo suma como expositor por confirmar |

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
| `GET` | `/api/buscar?q=` | Búsqueda global: socios, empresas, comprobantes y público |
| `GET` | `/api/members` | Lista socios JSON; filtros: search, estado, pago |
| `GET` | `/api/members/{id}` | Detalle socio JSON |
| `GET` | `/api/payments` | Pagos JSON; filtros: periodo, estado |
| `POST` | `/api/email/test` | Test de envío de email |

**Productos WooCommerce:** la correspondencia sale de `productos.wc_product_id` (`portal_db.get_wc_product_map()`). El tipo decide la acción: `cuota_anual` / `cuota_provincia` marcan el pago; `fraccionamiento` registra una cuota.

Los productos con `periodo` lo usan directamente. Los genéricos buscan el año pendiente más antiguo del socio entre `catalogos.get_periodos_cobro()` — los períodos con un producto de cuota activo. Ese mismo conjunto define los botones de pago del portal.

---

## 10. webapp/db.py — funciones clave con firmas {#dbpy}

### Socios

```python
get_stats() → dict
get_members(search="", estado="", pago="", ubicacion="", wp="", page=1, per_page=50) → (docs[], total)
get_member(member_id: str) → dict | None
create_member(apellidos, nombres, titulo="", email="", celular="",
              centro_trabajo="", ubicacion="", fecha_ingreso="",
              notas="", periodo_actual=None) → str  # devuelve member_id
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
get_payments(periodo=None, estado="", empresa="", search="",
             comprobante_emitido="", page=1) → (docs[], total)
get_payments_export(...) → docs[]  # sin paginación
update_payment(payment_id: str, estado: str, empresa=None, fecha_pago=None,
               medio=None, pagado_por=None, num_comprobante=None,
               tipo_comprobante=None, link_constancia=None, banco_origen=None,
               comprobante_emitido=None, fecha_emision_comprobante=None,
               comprobante_id=None)   # ← FK a comprobantes._id (ver R7)
               # auto-resuelve empresa_id cuando se pasa empresa
set_monto_objetivo(payment_id: str, monto_objetivo: float)
get_or_create_payment(member_id: str, periodo: str) → str  # devuelve payment_id
```

### Cuotas de fraccionamiento

```python
# Llama _sync_estado_from_cuotas() internamente — no llamarla por separado
add_cuota(payment_id: str, monto: float, fecha_venc: str = None)
add_cuotas_batch(payment_id: str, cuotas: list) → list
  # cuotas = [{"monto": float, "fecha_venc": str|None, "producto_nombre": str|None}, ...]
  # Agrega múltiples cuotas de una vez y llama _sync una sola vez al final
update_cuota(payment_id: str, numero: int, estado: str, fecha_pago=None,
             medio_pago=None, num_comprobante=None, tipo_comprobante=None,
             link_constancia=None, banco_origen=None, monto=None, fecha_venc=None,
             comprobante_id=None)   # ← FK a comprobantes._id (ver R7)
delete_cuota(payment_id: str, numero: int)
```

### Pagos parciales

```python
# Llama _sync_estado_from_parciales() internamente — no llamarla por separado
set_monto_total(payment_id: str, monto_total: float)
add_pago_parcial(payment_id: str, monto: float, fecha_pago=None, medio=None,
                 num_comprobante=None, tipo_comprobante=None,
                 link_constancia=None, banco_origen=None,
                 comprobante_id=None)   # ← FK a comprobantes._id (ver R7)
update_pago_parcial(payment_id: str, numero: int, monto=None, fecha_pago=None,
                    medio=None, num_comprobante=None, tipo_comprobante=None,
                    link_constancia=None, banco_origen=None,
                    comprobante_id=None)   # ← FK a comprobantes._id (ver R7)
delete_pago_parcial(payment_id: str, numero: int)
```

### Facturación

```python
get_comprobantes_pendientes(periodo: str, search: str = "") → docs[]
emitir_comprobante(payment_id: str, tipo: str,  # tipo: "principal"|"cuota"|"parcial"
                   numero: int | None, num_comprobante: str,
                   tipo_comprobante: str, fecha_emision: str = "",
                   comprobante_id: str = None)   # ← FK a comprobantes._id (ver R7)
  # ORDEN REQUERIDO: crear comprobante primero → pasar comp_id → llamar emitir_comprobante
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
                   socios: list, items: list = None, ruc: str = "",
                   fecha_pago: str = "") → str
  # items[]: [{member_id, producto_nombre, monto, payment_id, tipo_pago, cuota_numero}]
  # auto-resuelve empresa_id desde companies; llama sync_comprobante_to_payments() si items
update_comprobante(comprobante_id: str, fields: dict)
  # fields permitidos: numero, tipo, fecha_emision, fecha_pago, monto_total,
  #   producto_nombre, concepto, empresa, empresa_id, socios, estado
  # auto-resuelve empresa_id si se pasa "empresa"
  # llama sync_comprobante_to_payments() automáticamente si hay items
sync_comprobante_to_payments(items: list, numero: str, tipo: str,
                              fecha_emision: str, empresa: str = "",
                              comprobante_id: str = None,
                              fecha_pago: str = "") → list[dict]
  # Fast path: item.payment_id + item.tipo_pago → FK directa, O(1)
  # Propaga fecha_pago al payment; si vacío usa fecha_emision como fallback
  # Slow path (solo migrate_fks.py): inferencia por codigo_sunat/producto_nombre
  # Llamada automáticamente por create_comprobante y update_comprobante
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
# Vista plana (usada internamente por los endpoints de recordatorios)
get_fraccionamientos(periodo="", alerta="", search="", page=1) → (docs[], total, stats)

# Vista agrupada por socio (usada por GET /fraccionamientos)
get_fraccionamientos_agrupados(search="", alerta="", periodo="",
                                page=1, per_page=50) → (miembros[], total, stats)
  # miembros[]: [{
  #   member_id, nombre_completo, apellidos, titulo, email_principal,
  #   fraccionamientos: [payment_doc enriquecido],   ← ordenados por periodo desc
  #   worst_alerta, total_pendiente, total_cobrado, cuotas_vencidas, periodos_count
  # }]
  # Ordenados: worst_alerta primero (_ALERTA_ORDER), luego apellidos
  # Paginación por socio (no por fraccionamiento)

# Helper privado — enriquece un doc de payments con campos calculados
_enrich_fracc_doc(d: dict, m: dict, today) → dict
  # Agrega: nombre_completo, email_principal, cuotas_total, cuotas_pagadas_count,
  #         total_cobrado, total_pendiente, cuotas_vencidas_count,
  #         proxima_cuota, proxima_cuota_fecha_iso, proxima_cuota_dias, alerta

# Constante de orden de alertas (menor = más urgente)
_ALERTA_ORDER = {"vencida": 0, "proxima_7d": 1, "proxima_30d": 2, "sin_fechas": 3, "al_dia": 4}
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

Colecciones propias del agente (en `knowledge_base/db.py`): `pending_approvals`, `pending_clarifications`, `processed_emails`, `bot_state`.

Colecciones compartidas con la web: `faqs` y `eventos_ipidet`. `knowledge_base/db.py::_evento_para_agente()` traduce cada evento a los campos que usa el agente; precio, cierre de inscripción y link de registro se editan en `/eventos/{id}`.

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

`webapp/mailer.py` envía emails via Brevo. Los bounces (hard y soft) llegan via webhook a `POST /webhook/brevo/bounce` y se registran en `members.emails[].estado = "rebotado"`. La vista `/comunicaciones/rebotes` muestra el historial.

El agente de email consulta solo emails con `estado: "habilitado"`. Los rebotes de Brevo marcan el email automáticamente.

Filtros disponibles al enviar: título, ubicación, estado de socio, estado de pago, periodo.

### Arquitectura de transporte en `webapp/mailer.py`

| Función | Canal | Cuándo se usa |
|---------|-------|---------------|
| `send_email(...)` | **Brevo HTTP API** (`BREVO_API_KEY`) | Emails individuales: constancia, boleta, cualquier envío one-shot |
| `send_bulk(...)` | Brevo HTTP API si `BREVO_API_KEY`; sino SMTP | Comunicaciones masivas a listas de socios |
| `_send_sync(...)` | SMTP (`smtp-relay.brevo.com:587`) | Solo se llama como fallback de `send_bulk` si no hay API key |

**Credenciales en Render (producción):**
- `BREVO_API_KEY` — API/MCP key de Brevo "ERP - IPIDET" (activa, sin vencimiento). **Esta es la key que hace funcionar todos los emails individuales.**
- `BREVO_SMTP_USER` / `BREVO_SMTP_PASSWORD` — NO están configuradas en Render. El SMTP relay no se usa en producción.

### R9 — No cambiar el canal de transporte de `send_email`

`send_email` usa `BREVO_API_KEY` (HTTP API). **Nunca cambiar esto a SMTP** — las credenciales SMTP no están en Render y no son el canal de producción. Si un email no llega, diagnosticar en este orden:

1. ¿`BREVO_API_KEY` está activa en Render? (Render → Environment)
2. ¿El adjunto es `bytes` y no `bytearray`? (`isinstance(raw, (bytes, bytearray))` en `_send_api`)
3. ¿La API devuelve error HTTP? (ver bloque `except` en `_send_api`)

No tocar el transporte hasta haber descartado los tres puntos anteriores.

---

## 14. Autenticación web {#auth}

Toda la plataforma requiere login (excepto `/login`, `/webhook/*`). Las sesiones se almacenan en cookies firmadas con `SESSION_SECRET`. El rol `admin` tiene acceso a `/admin/users`; los usuarios con rol `viewer` solo acceden a las secciones en su lista `permisos`.

**Secciones con control de acceso:** `dashboard`, `members`, `billing`, `fraccionamientos`, `finanzas`, `faqs`, `marketing`, `comunicaciones`, `eventos`, `pendientes`, `redes`, `alianzas`.

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

- **Múltiples `<tbody>` por tabla:** En `billing.html` cada pago usa su propio `<tbody x-data='billingRow(...)'>` para compartir estado Alpine.js entre el `<tr>` de datos y el `<tr>` de edición. En `fraccionamientos.html` cada socio usa su propio `<tbody>` que agrupa sus filas de período. Múltiples `<tbody>` por tabla es HTML5 válido. → ver R2.
- **`_sync_estado_from_cuotas` nunca cambia a `"pagado"`:** Ese cambio es siempre manual. → ver sección 7.2 para la máquina de estados completa.
- **Soft delete en FAQs:** `active: false` desactiva sin borrar, para mantener historial de aprendizaje del agente.
- **Soft delete en comprobantes:** `estado: "anulado"` en lugar de borrar físicamente.
- **`wp_user_id` en members:** Vincula el socio con su usuario de WordPress. Se auto-asigna cuando WooCommerce envía un pedido con `customer_id` reconocido. También se puede setear manualmente desde `/sync-usuarios`.
- **`payments.num_comprobante` vs `payments.comprobante_id`:** `num_comprobante` es el número de texto para display (legacy, mantenido por compatibilidad). `comprobante_id` es la FK ObjectId a `comprobantes._id` — úsalo para joins. Ambos se escriben juntos. → ver R7 y sección 7.5.
- **FK pattern — comprobantes ↔ payments ↔ companies:** Todas las relaciones entre colecciones usan ObjectId FKs (`comprobante_id`, `empresa_id`, `payment_id`) almacenados como strings. Los campos de texto (`empresa_pagadora`, `num_comprobante`) se mantienen como fallback de display pero no deben usarse como join key.
- **Auto-resolución de `empresa_id`:** `update_payment` y `create_comprobante` resuelven `empresa_id` automáticamente cuando se pasa `empresa` (nombre). El llamador no necesita pasar `empresa_id` explícitamente.
- **`comprobante_id` debe pasarse explícitamente:** A diferencia de `empresa_id`, `comprobante_id` no se puede auto-resolver en el momento de escribir el pago (el comprobante se crea en la misma request). Orden obligatorio: (1) crear comprobante con `create_comprobante()` → obtener `comp_id`, (2) pasar `comp_id` a `emitir_comprobante()` o `update_cuota()`. → ver R7.
- **`sync_comprobante_to_payments` — fast vs slow path:** Si `items[].payment_id` + `items[].tipo_pago` presentes → FK directa O(1). Si no → inferencia por nombre de producto (frágil, solo para documentos históricos). El fast path es el único camino correcto para documentos nuevos.
- **Auto-sync en `update_comprobante`:** Editar un comprobante propaga los cambios a los `payments` vinculados automáticamente. No hay botón "Sincronizar" en la UI.
- **`migrate_fks.py` para datos históricos:** Backfill idempotente de todos los campos FK en documentos pre-existentes. Corre solo cuando hay datos sin migrar; no toca estados ni montos.
- **`sync_credito_to_cobranzas`:** Se llama automáticamente desde `create_factura_credito` y `update_factura_credito_estado`. Sincroniza el estado de `payments` de los socios vinculados a la factura de crédito.
- **`certifi` en Atlas:** → ver R6.
- **Paginación por defecto:** `per_page=50` en `get_members`; `page_size=50` implícito en la mayoría de endpoints paginados. El parámetro se llama `page` (1-indexed).
- **Catálogos editables:** medios de pago, bancos, tipos de socio y de empresa y checklists de eventos están en la colección `config` y se editan en `/configuracion`. → ver R10.
- **Período de trabajo:** `catalogos.get_periodo_actual()` usa el valor fijado en `/configuracion`; si no hay, el año en curso cuando ya tiene cobros y, si no, el último período con cobros. Así el cambio de año ocurre al generar los cobros del año nuevo.
- **`fecha_pago` vs `fecha_emision` en comprobantes:** Son tres fechas distintas — `fecha_emision` (fecha del documento tributario), `fecha_pago` (fecha en que se recibió el dinero, puede ser anterior o posterior), `fecha_carga` (fecha de registro en la plataforma). El sync a `payments` usa `fecha_pago or fecha_emision` para rellenar la fecha del pago en cobranzas.
- **Vista de fraccionamientos agrupada por socio:** `fraccionamientos.html` usa `miembros` (no `fraccionamientos`) como variable principal. Cada socio aparece como cabecera `<tr>` de color gris que agrupa sus períodos fraccionados como sub-filas indentadas. El filtro de período es opcional (default vacío = todos). Los modales de cuotas se generan en un loop anidado `{% for m in miembros %}{% for f in m.fraccionamientos %}`. → Ver `get_fraccionamientos_agrupados()` y `_enrich_fracc_doc()`.
- **Recordatorios de fraccionamientos:** El email de recordatorio se genera server-side en `_build_recordatorio_email()` (en `app.py`). Usa `_tratamiento_titulo()` para el saludo, ajusta el tono según días vencidos (≤7d suave, 8-30d medio, >30d urgente), e incluye tabla de cuotas vencidas, bloque empresa pagadora, progreso y próxima cuota. El preview en el modal usa `iframe srcdoc` para aislar el HTML sin riesgo XSS.
- **Filtros en encabezados de tabla (`publico.html`):** El patrón `<select name="X" @change="$refs.filterForm.submit()">` dentro de un `<th>` es parte del `<form method="get" x-ref="filterForm">` que envuelve toda la tabla. No usar sub-componentes Alpine.js para filtros en la misma tabla — perderían acceso a `$refs.filterForm`.

---

## 17. Público / Prospectos CRM {#publico}

`/publico` — CRM para personas que no son socios IPIDET pero están en el radar (leads de webinars, ferias, eventos, referidos).

### Flujo de estados

```
nuevo → interesado → postulante → convertido
                                    ↓
                              /members/{id}  (convert_prospecto_to_member)
```

`inactivo` = baja voluntaria o descarte.

### Colección `publico`

```
nombre          string
apellido        string
email           string    (único — upsert por email)
celular         string
titulo          string    mismo vocabulario controlado que members.titulo
empresa         string
ciudad          string
notas           string
fuente          string    "manual"|"excel"|"zoom"|"portal"|"evento"|"referido"
fuente_detalle  string    detalle libre de la fuente
estado          string    "nuevo"|"interesado"|"postulante"|"convertido"|"inactivo"
es_socio        bool      True si ya fue convertido a member
tags            [string]  etiquetas libres
zoom_eventos    [{
                  evento_id:     string   ID del webinar Zoom
                  evento_titulo: string
                  evento_fecha:  string
                  asistio:       bool | null
                }]
historial_comunicaciones  [{asunto, plantilla_nombre, fecha}]
postulacion     {tipo_postulacion, notas_postulacion, fecha} | null
created_at      datetime
```

### Funciones clave en db.py

```python
get_prospectos(search, estado, ciudad, titulo, empresa, fuente,
               tags, es_socio, zoom_evento_ids, zoom_asistio, page) → (docs[], total)
get_prospectos_all(...)  → docs[]   # sin paginación, para campañas
get_prospecto(prospecto_id) → dict | None
create_prospecto(nombre, apellido, email, ...) → str
update_prospecto(prospecto_id, fields)
delete_prospecto(prospecto_id)
convert_prospecto_to_member(prospecto_id, periodo_actual) → member_id
upsert_prospectos_from_zoom(evento_id, evento_titulo, evento_fecha, registrantes[])
import_prospectos_bulk(rows[]) → {insertados, actualizados, errores}
get_all_tags() → [{tag, count}]
get_zoom_eventos_disponibles() → [{_id, titulo, fecha, count}]
```

### `zoom_asistio` — valores del filtro

| Valor | Comportamiento en MongoDB |
|-------|--------------------------|
| `""` | Sin filtro zoom |
| `"tiene"` | `zoom_eventos.0: {$exists: true}` — tiene al menos un webinar |
| `"true"` | `zoom_eventos: {$elemMatch: {asistio: true}}` |
| `"false"` | `zoom_eventos: {$elemMatch: {asistio: {$ne: true}}}` |
| `"excluir"` | `zoom_eventos: {$not: {$elemMatch: {}}}` — sin webinars |
| `"inscrito"` (con ids) | `zoom_eventos: {$elemMatch: {evento_id: {$in: ids}}}` |

### Integración con Zoom

`POST /eventos/{id}/sync-zoom` llama `zoom_client.get_webinar_registrants(webinar_id)` y luego `upsert_prospectos_from_zoom(...)`. Requiere que el evento tenga `zoom_webinar_id` guardado.

---

## 18. Redes Sociales {#redes}

`/redes` — gestión de presencia en RRSS: Instagram, LinkedIn, YouTube, TikTok.

### Sub-secciones

| Ruta | Template | Descripción |
|------|----------|-------------|
| `/redes` | `redes_dashboard.html` | Dashboard: métricas actuales por cuenta |
| `/redes/posts` | `redes_posts.html` | Posts publicados con filtros |
| `/redes/cronograma` | `redes_cronograma.html` | Calendario de publicaciones planificadas |

### Sincronización de métricas (`sync_redes.py`)

**Corre solo localmente**, no en Render. Usa Playwright con Brave para scrapear seguidores/vistas.

```bash
# Primera vez — inicia sesión manualmente en las redes
python sync_redes.py --setup

# Uso normal — sincroniza métricas
python sync_redes.py
```

El perfil del navegador se guarda en `chrome_profile_redes/`. La tarea se programa con Windows Task Scheduler (`setup_tarea_redes.bat`).

### Funciones en db.py

```python
get_redes_cuentas(solo_activas=False) → list
create_red_cuenta(plataforma, nombre, url, handle) → str
save_red_metrica(cuenta_id, plataforma, seguidores, ...) → None
get_redes_metricas_latest() → list    # última métrica por cuenta
get_redes_metricas_historico() → dict # {cuenta_id: [metricas]}
get_redes_posts(plataforma, tipo, search, page) → (docs[], total)
create_red_post(cuenta_id, plataforma, url, tipo, ...) → str
get_cronograma(plataforma, estado, search, page) → (docs[], total)
create_cronograma_item(plataforma, titulo, descripcion, ...) → str
```

---

## 19. Facturación electrónica SUNAT {#sunat}

`webapp/sunat_client.py` — cliente APISPERU para emitir boletas/facturas electrónicas (UBL 2.1) directamente a SUNAT.

### Flujo de emisión

1. Crear comprobante en BD con `create_comprobante(...)` → estado `"pre_registrado"` si se importa sin número SUNAT, o `"emitido"` directo si se emite desde el sistema.
2. `GET /api/sunat/preflight/{comp_id}` → valida datos antes de emitir (RUC, correlativo, etc.)
3. `POST /comprobantes/{id}/sunat/emitir` → llama `sunat_client.emitir_comprobante(...)` → APISPERU → SUNAT → devuelve XML + PDF firmado.
4. `GET /comprobantes/{id}/pdf` → genera PDF local con `pdf_builder.py` (como respaldo o para boletas sin XML).
5. `POST /comprobantes/{id}/sunat/anular` → comunicación de baja a SUNAT.

### Pre-comprobantes (importación XML)

`POST /comprobantes/import-xml` — importa un XML de comprobante ya emitido externamente (ej. factura de empresa pagadora). El `xml_parser.py` extrae número, tipo, fechas, RUC, montos. Se crea con estado `"pre_registrado"`. Luego `/comprobantes/preparar` permite completar la vinculación con payments.

### Correlativo automático

`get_ultimo_correlativo_info(serie)` / `get_ultimo_correlativo_emitido(serie)` — busca el último número emitido para una serie (B001, F001, etc.) en la colección `comprobantes`. `GET /api/sunat/siguiente-correlativo` devuelve el siguiente disponible.

---

## 20. Zoom {#zoom}

`webapp/zoom_client.py` — Server-to-Server OAuth. Token expira cada hora y se re-obtiene automáticamente.

### Variables de entorno necesarias

`ZOOM_ACCOUNT_ID`, `ZOOM_CLIENT_ID`, `ZOOM_CLIENT_SECRET`

### Funciones del cliente

```python
get_webinars(page_size=100) → list         # lista webinars de la cuenta
get_webinar(webinar_id) → dict             # detalle de un webinar
get_webinar_registrants(webinar_id) → list # registrantes (nombre, email, ¿asistió?)
```

### Integración con eventos y prospectos

- `GET /api/zoom/webinars` — devuelve webinars activos para el selector en eventos.
- `POST /eventos/{id}/sync-zoom` — sincroniza registrantes del webinar vinculado al evento; hace upsert en `publico` y marca asistencia.
- Los eventos pueden almacenar `zoom_webinar_id` y `zoom_webinar_url`.

---

## 21. Campañas y envíos programados {#campanias}

### Campañas por lotes (`/comunicaciones/campanas`)

Permiten crear una lista de destinatarios + contenido y enviar en lotes para no saturar el SMTP.

```python
create_campana(asunto, cuerpo, fuente, filtros, destinatarios) → str
get_campanas(limit) → list
get_campana(campana_id) → dict | None
delete_campana(campana_id)
marcar_rebote_campana(email)
```

`POST /campanas/crear` — crea campaña con `fuente="socios"` o `fuente="publico"`.
`POST /campanas/{id}/enviar-lote` — envía el siguiente lote (paginado) de la campaña.

### Envíos programados

```python
create_envio_programado(asunto, cuerpo, filtros, fecha_programada) → str
get_envios_programados_pendientes() → list
update_envio_programado_estado(envio_id, estado, ...)
cancel_envio_programado(envio_id)
get_envios_programados(limit) → list
```

`scheduler.run_programados_scheduler()` — tarea async que se lanza en startup y verifica cada 5 min si hay envíos programados listos para ejecutar.

`POST /api/comunicaciones/programar` — crea un envío programado para una fecha/hora futura.
`POST /api/comunicaciones/programados/{id}/cancelar` — cancela un envío pendiente.
`GET /api/comunicaciones/programados` — lista envíos programados.

---

## 22. Plantillas de email guardadas {#plantillas}

Las plantillas se almacenan en MongoDB (colección interna de `comunicaciones_col` o dedicada) y se reutilizan en comunicaciones masivas, campañas y eventos.

```python
get_plantillas(tipo="") → list            # tipo: "socios" | "publico" | ""
create_plantilla(nombre, asunto, cuerpo, tipo) → str
update_plantilla(plantilla_id, ...)
delete_plantilla(plantilla_id)
_seed_plantillas()                         # seed inicial en startup
```

Rutas: `GET/POST /api/plantillas`, `POST /api/plantillas/{id}/update`, `POST /api/plantillas/{id}/delete`.

---

## 23. Constancias de membresía {#constancias}

`webapp/constancia.py` — genera constancias en PDF (fpdf2) que acreditan la membresía activa del socio.

- `POST /members/{id}/constancia/enviar` — genera el PDF y lo envía por email al socio.
- `get_next_constancia_number(year)` — correlativo anual.
- `save_constancia(member_id, numero, ...)` — registra la constancia emitida en BD.

---

## 24. IA Tareas {#ia-tareas}

Lista de tareas internas para el agente IA. Visible en `/pendientes` (misma pantalla) o vía API.

```python
get_ia_tareas(solo_pendientes=False) → list
create_ia_tarea(texto) → str
update_ia_tarea(tarea_id, texto, estado, ...)
toggle_ia_tarea_stand_by(tarea_id)   # alterna estado stand-by
delete_ia_tarea(tarea_id)
```

Rutas: `GET /api/ia-tareas`, `POST /ia-tareas/add`, `POST /ia-tareas/{id}/estado`, `POST /ia-tareas/{id}/update`, `POST /ia-tareas/{id}/stand-by`, `POST /ia-tareas/{id}/delete`.

---

## 25. Alianzas {#alianzas}

`/alianzas` propone instituciones para posibles alianzas y sustenta cada una: por qué es importante y por qué sería estratégica. `webapp/alianzas_ia.py` usa Claude con búsqueda web; no envía nada ni toca socios. El motor de búsqueda, la verificación de fuentes y el cálculo de costo son comunes con Ponentes y viven en `webapp/investigacion_ia.py`.

- **Frecuencia:** `scheduler.run_investigacion_scheduler` revisa cada hora si ya toca. La fecha vive en `config.alianzas_estado`, así que un reinicio no repite ni reprograma la búsqueda. Tras un fallo espera 24 horas para no gastar en reintentos.
- **Una a la vez:** `pdb.reservar_corrida_alianzas` reserva de forma atómica; una reserva de más de 45 minutos caduca porque un reinicio la pudo dejar abierta.
- **Datos, no código (R10):** perfil, criterios, frecuencia, modelo y precios viven en `config.alianzas_config`; los tipos en el catálogo `tipos_institucion`. Las etapas son lógica y están en `catalogos.ESTADOS_ALIANZA`.
- **Fuentes verificadas:** `_preparar` descarta una propuesta si ninguna de sus fuentes apareció en los resultados de esa búsqueda. No relajar esta regla: es lo que evita instituciones o enlaces inventados.
- **El equipo manda:** `guardar_propuesta_alianza` nunca pisa etapa, responsable ni notas, y no vuelve a proponer una institución descartada. El análisis solo se reescribe mientras sigue «Por revisar».
- **Socios vinculados:** se calculan al mostrar la lista cruzando el nombre o las siglas con `members.centro_trabajo`. A Claude no se le envían datos de socios.

Colección `alianzas`: `nombre`, `clave` (única), `siglas`, `tipo`, `ambito`, `pais`, `web`, `dominio`, `que_hace`, `por_que_importante`, `por_que_estrategica`, `como_colaborar[]`, `primer_paso`, `riesgos`, `afinidad`, `impacto`, `viabilidad`, `puntaje`, `fuentes[{titulo, url}]`, `novedades[{fecha, texto, corrida_id}]`, `origen`, `estado`, `responsable`, `fecha_seguimiento`, `notas`, `created_at`, `updated_at`.

---

## 26. Ponentes {#ponentes}

`/ponentes` es el directorio de posibles ponentes. La búsqueda (`webapp/ponentes_ia.py`) prioriza a profesionales de **otras instituciones**; los socios se detectan aparte, sin enviar sus datos a Claude.

- **Dos búsquedas:** una semanal, con los temas prioritarios, los eventos próximos y la asistencia de los anteriores, y otra por evento, con el botón «Buscar ponentes con IA» en `/eventos/{id}`. Solo la semanal cuenta para la frecuencia (`semanal=False` en la reserva).
- **Personas, reglas más estrictas:** solo información profesional pública. `_preparar` borra correos y teléfonos aunque Claude los devuelva, y descarta LinkedIn o perfiles que no aparecieron en la búsqueda. No relajar estas reglas.
- **Eventos:** cada expositor guarda `ponente_id`. Al agregar un expositor en un evento se crea o reutiliza su ficha (`registrar_ponente_desde_evento`), y al confirmarlo la ficha pasa a «Confirmado». La ficha muestra en qué eventos expuso y cuánta gente asistió, con datos de `zoom_registrants`.
- **Cruces con socios:** se calculan al mostrar la lista: «Posible socio» si el nombre coincide, y socios que trabajan en su institución. Son coincidencias de texto: hay que confirmarlas.
- **Datos, no código (R10):** público, temas, frecuencia, modelo y precios están en `config.ponentes_config`; las etapas, en `catalogos.ESTADOS_PONENTE`.

Colección `ponentes`: `nombre`, `clave` (única), `cargo`, `institucion`, `pais`, `ambito`, `temas[]`, `por_que_relevante`, `trayectoria`, `charlas_posibles[]`, `canal_contacto`, `linkedin`, `perfil_url`, `es_funcionario_publico`, `consideraciones`, `relevancia`, `prestigio`, `viabilidad`, `puntaje`, `fuentes[]`, `sugerencias[{evento_id, evento_titulo, motivo, fecha}]`, `novedades[]`, `origen` (`busqueda`, `evento`, `manual`), `estado`, `responsable`, `fecha_seguimiento`, `notas`.
