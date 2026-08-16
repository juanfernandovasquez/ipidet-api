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

## Arquitectura general

```
main.py              — Agente de email: loop principal, orquesta todo
platform_main.py     — Servidor web admin (uvicorn + FastAPI)
import_padron.py     — Importación Excel → MongoDB (one-shot)

config/settings.py   — Variables de entorno centralizadas
gmail/client.py      — IMAP/SMTP con Gmail
classifier/engine.py — Clasificador de intención con Claude
workflows/
  incoming.py        — Procesa emails entrantes, decide acción
  approvals.py       — Cola de aprobaciones manuales vía Telegram
  event_manager.py   — Gestión de eventos
  learning.py        — Aprendizaje de respuestas aprobadas
billing/
  db.py              — Consultas MongoDB de cobranzas
  reminders.py       — Scheduler: recordatorios y alertas de morosos
  sheets.py          — Sync con Google Sheets
knowledge_base/
  db.py              — CRUD de FAQs en MongoDB
  faq_loader.py      — Carga FAQs desde YAML → MongoDB
  faqs/              — Archivos YAML: inscripciones, eventos, membresías
telegram_bot/
  notifications.py   — Envío de alertas al admin vía Telegram
webapp/
  app.py             — Rutas FastAPI (dashboard, socios, cobranzas, FAQs)
  db.py              — Consultas MongoDB para la web
  templates/         — HTML Jinja2: base, dashboard, members, billing, faqs
```

## Base de datos (MongoDB)

Base: `ipidet_agent` — URI en `.env` como `MONGODB_URI`.

### Colección `members`
```
member_id       string  "IPIDET-0294" (generado del No. REGISTRO del Excel)
apellidos       string
nombres         string
titulo          string  normalizado ("Contador", "Abogado", etc.)
centro_trabajo  string
celular         string
ubicacion       string  normalizado ("Lima", "La Libertad", etc.)
fecha_ingreso   datetime | null
fecha_nacimiento datetime | null
estado          string  "activo" | "retirar"
emails          [{email, estado: "habilitado"|"inhabilitado", principal: bool}]
notas           string  comentarios del padrón unidos con " | "
```

### Colección `payments`
```
member_id        string  FK → members.member_id
periodo          string  "2025" | "2026"
estado           string  "pagado"|"debe"|"fraccionamiento"|"exonerado"|"no_aplica"
                         |"pendiente"|"retirar"|"en_revision"|"revisar"
empresa_pagadora string | null  "EY"|"BDO"|"PWC"|"KPMG"|"PPU"
fecha_pago       string | null
medio_pago       string | null
raw_original     string  valor original del Excel
```

### Colección `faqs`
```
question    string
answer      string
category    string
active      bool
times_used  int
created_at  datetime
```

### Colección `events`
Gestión de eventos del agente (aprobaciones pendientes, etc.).

## Variables de entorno (.env)

```
ANTHROPIC_API_KEY       — Claude API
TELEGRAM_BOT_TOKEN      — Bot de Telegram
TELEGRAM_CHAT_ID        — Chat del admin
MONGODB_URI             — MongoDB (default: mongodb://localhost:27017)
GMAIL_ADDRESS           — cuenta Gmail del agente
GMAIL_APP_PASSWORD      — App password de Gmail
ADMIN_FORWARD_EMAIL     — administracion@ipidet.org
CONFIDENCE_THRESHOLD    — 0.85 (umbral para auto-responder)
AUTO_APPROVE_CONFIDENCE — 0.88 (umbral para aprobar sin humano)
CHECK_INTERVAL_SECONDS  — 60
GOOGLE_CREDENTIALS_PATH — google_credentials.json (para Sheets)
BILLING_SHEET_ID        — ID de la hoja de cobranzas en Google Sheets
BILLING_REMINDER_DAYS   — 7
BILLING_CHECK_INTERVAL_HOURS — 6
```

## Web admin — rutas

| Ruta | Descripción |
|------|-------------|
| `GET /` | Dashboard: stats generales |
| `GET /members` | Padrón paginado, filtros por estado/pago/ubicación/búsqueda |
| `GET /members/{id}` | Detalle de socio, historial de pagos, editar emails/notas/estado |
| `POST /members/{id}/emails/toggle` | Habilitar/inhabilitar email |
| `POST /members/{id}/emails/add` | Agregar nuevo email |
| `POST /members/{id}/notes` | Actualizar notas |
| `POST /members/{id}/estado` | Cambiar estado del socio |
| `GET /billing` | Cobranzas paginadas, filtros por período/estado/empresa |
| `POST /billing/{id}/update` | Actualizar estado de pago |
| `GET /faqs` | Lista de FAQs con búsqueda/categoría |
| `POST /faqs/add` | Nueva FAQ |
| `POST /faqs/{id}/delete` | Desactivar FAQ (soft delete) |
| `GET /api/members` | JSON para el bot |
| `GET /api/members/{id}` | JSON detalle para el bot |
| `GET /api/payments` | JSON cobranzas para el bot |

## Importación del padrón

El script `import_padron.py` lee el archivo Excel del padrón (columnas fijas) y lo importa a MongoDB, normalizando ubicaciones, títulos y estados de pago. **Hace drop de las colecciones `members` y `payments` antes de importar** — ejecutar con cuidado.

Ruta hardcodeada del Excel: `C:\Users\Juan\Downloads\PADRÓN - JULIO.xlsx`. Actualizar si el archivo cambia.

## Flujo del agente de email

1. `gmail/client.py` — polling IMAP cada 60s, obtiene emails nuevos por UID
2. `classifier/engine.py` — Claude clasifica intención del email
3. `workflows/incoming.py` — decide: responder automáticamente, escalar a admin o ignorar
4. `workflows/approvals.py` — si confianza baja, Telegram pide aprobación al admin
5. Respuesta aprobada → `workflows/learning.py` → guarda como FAQ si es recurrente

## Decisiones de diseño relevantes

- `member_id` se genera del campo "No. REGISTRO IPIDET" del Excel, con prefijo `IPIDET-`. Registros sin número válido quedan como `IPIDET-?`.
- Los emails múltiples en el Excel (separados por `;` o `,`) se parsean en un array. El primero se marca `principal: true`.
- El padrón de socios es la fuente de verdad para emails del agente; los emails inhabilitados no reciben mensajes.
- Los estados de pago se normalizan desde texto libre del Excel (ej. "PAGADO EY" → estado: "pagado", empresa: "EY").
