# Documentación del sitio web ipidet.org

**Fecha de relevamiento:** 2026-08-16  
**URL:** https://ipidet.org  
**Estado:** Sitio activo en producción

---

## 1. Stack tecnológico

| Componente | Detalle |
|---|---|
| CMS | WordPress 7.0.4 |
| Tema | `taxpress` (custom/comprado) |
| Page builder | Elementor + Elementor Pro |
| E-commerce | WooCommerce |
| UI Components | JetPlugins suite (jet-menu, jet-elements, jet-blocks, jet-tabs, jet-theme-core) |
| Formularios | Contact Form 7 (CF7) |
| Suscripción email | JetSubscribe (parte de JetPlugins) |
| Slider | bdthemes Prime Slider Lite |
| Popups | Brave Popup Builder |
| Performance | WP Rocket (cache) |
| Spam | Akismet |
| Hosting | Desconocido (servidor externo, dominio propio) |
| REST API | WordPress REST API activa (879 rutas) |

---

## 2. Estructura de navegación completa

```
BARRA SUPERIOR
├── administracion@ipidet.org         mailto:
├── (01) 422-6048                     tel:
├── +51 922474067                     WhatsApp
├── Facebook / LinkedIn / YouTube     redes sociales
└── ícono User-circle                 → /registro/ (login/cuenta WooCommerce)

MENÚ PRINCIPAL
├── INICIO                            → /
├── NOSOTROS
│   ├── Quiénes Somos                 → /quienes-somos/
│   └── Consejo Directivo             → /consejo-directivo/
├── EVENTOS
│   ├── Asambleas                     → /asambleas/
│   ├── Conversatorios                → /conversatorio/
│   ├── Ipidet Te Informa             → /ipidet-te-informa/
│   ├── Foro                          → /foro/
│   └── Portafolio de Eventos         → /portafolio-eventos/
├── CONVENIOS                         → /convenios/
├── TRÁMITES
│   ├── Admisión                      → /admision/
│   └── Pagos                         → /pagos/  (WooCommerce shop)
├── TRANSPARENCIA                     → /transparencia/
├── ASOCIADOS
│   ├── Asociados                     → /asociados/  (WooCommerce My Account)
│   └── Zona de Asociados             → /zona-de-asociados/
└── CONTACTO                          → /contacto/

CTA FLOTANTE (bottom-right, todas las páginas)
└── "ACTUALIZA TUS DATOS"             → destino pendiente de confirmar
```

---

## 3. Páginas — descripción detallada

### 3.1 `/` — Inicio
- Hero con texto institucional + botones "CONTÁCTANOS" y "VER MÁS"
- Carrusel de citas del Consejo Directivo 2026-2028 (Presidente: Katarzyna Dunin Borkowski, Vicepresidente: Miguel Bobadilla)
- Sección "ÁREAS DE INTERÉS"
- Feed de últimos eventos (6 cards)
- Sección "COMUNICADOS"
- Videos "45 AÑOS" (aniversario institucional)
- "ENLACES DE INTERÉS"
- Formulario de newsletter (JetSubscribe, solo campo email)

### 3.2 `/quienes-somos/` — Quiénes Somos
- Texto descriptivo institucional
- Objetivos del IPIDET (4 objetivos)
- Valores institucionales (no se expandieron en el relevamiento)
- Contadores animados: Asociados / Socios Honorarios / Filiales en Perú / Años de fundación  
  *(actualmente en 0 — datos hardcodeados o no cargaron JS)*

### 3.3 `/consejo-directivo/` — Consejo Directivo
- Consejo Directivo 2024-2026: Presidente, Vicepresidente, Secretaria, Tesorero, 3 Vocales
- Lista de Past Presidents (1981 hasta 2026)
- Lista de Socios Honorarios (14 personas)

### 3.4 `/asambleas/` — Asambleas
- Archivo de eventos tipo "Asamblea" (posts de WordPress)
- Contenido actual: 4 entradas (2021–2026)
- Última: "CIERRE CONTABLE Y TRIBUTARIO 2025" (feb 2026)

### 3.5 `/conversatorio/` — Conversatorios
- Archivo de eventos tipo "Conversatorio" (posts de WordPress)
- Es la categoría con más contenido: **99 posts**
- Vista de listado; cada post tiene su propia página

### 3.6 `/ipidet-te-informa/` — Ipidet Te Informa
- Sección informativa/comunicados
- 1 post en categoría "Ipidet Te Informa"

### 3.7 `/foro/` — Foro
- Archivo de Foros Internacionales de Tributación y Contabilidad
- Contenido actual: VI, V y IV Foro Internacional
- Incluye directivas y características de trabajo (documentos)

### 3.8 `/portafolio-eventos/` — Portafolio de Eventos
- Galería fotográfica de eventos pasados filtrable por año (2016–2026)
- 42 posts en categoría "Portafolio de Eventos"
- Usa JetTabs o similar para el filtro por año

### 3.9 `/convenios/` — Convenios
- Logos/listado de instituciones con las que IPIDET tiene convenios
- Contenido escaso en el relevamiento (solo título "INSTITUCIONES")

### 3.10 `/admision/` — Admisión
- Texto de convocatoria (desactualizado — referencia a asamblea de marzo 2020)
- **6 documentos PDF descargables:**
  - Solicitud de Inscripción
  - Beneficios del Asociado
  - Reglamento de Admisión
  - Requisitos de Admisión
  - Guía de Postulación Lima
  - Guía de Postulación Filiales
- **Formulario CF7** (Contact Form 7):
  - Nombre, Correo, Observaciones, adjuntar Ficha de Inscripción (file upload)
  - Acción: `POST /admision/#wpcf7-f143-p7805-o1`

### 3.11 `/pagos/` — Pagos (WooCommerce Shop)
- Tienda WooCommerce con 5 productos:

| Producto | Precio | ID WC |
|---|---|---|
| Pago Ordinario | S/ 780.00 | 8882 |
| Cuota anual provincia | S/ 350.00 | 19105 |
| Fraccionamiento en 3 Meses | S/ 260.00 | 8880 |
| Libro «Tributación & Bicentenario» | S/ 160.00 | 12388 |
| Libros V Foro Internacional (Tomo I y II) | S/ 290.00 | 14055 |

- Flujo: añadir al carrito → `/carrito/` → `/finalizar-compra/`
- Los pagos de cuotas se realizan aquí (WooCommerce), completamente separados del padrón MongoDB

### 3.12 `/transparencia/` — Transparencia
- **6 documentos PDF descargables:**
  - Estado Financiero ENE 2021 - MAR 2022
  - Postulantes al Comité Directivo / Sucursales
  - Estado Financiero DIC 2019-2020
  - Memoria Anual
  - Reglamento de Filiales
  - Estatuto IPIDET
- Preguntas Frecuentes (accordion, expandible):
  - Descuentos/convenios
  - Formas de pago
  - Usuario/contraseña para cursos
  - Política de devolución/reembolso
  - Certificados

### 3.13 `/asociados/` — Área de Cuenta WooCommerce (My Account)
Página de login/cuenta de WooCommerce. Si el usuario está logueado, muestra:

**Tabs del área de miembro:**
| Tab | URL | Descripción |
|---|---|---|
| Pagos | `/asociados/pedidos/` | Historial de pedidos WooCommerce |
| Información | `/asociados/editar-cuenta/` | Editar nombre, apellidos, email, contraseña |
| Videos Exclusivos | *(enlace interno)* | Aparenta ser un link |
| Biblioteca | *(enlace interno)* | Aparenta ser un link |
| Cerrar | `/asociados/cierre-sesion/` | Logout |

**Formulario de edición de cuenta** (`/asociados/editar-cuenta/`):
- Nombre, Apellidos, Nombre visible, Email, Contraseña actual, Nueva contraseña

**Nota importante:** Los "usuarios" de WordPress/WooCommerce son **completamente independientes** del padrón MongoDB. Un socio puede tener cuenta WP sin estar en el padrón y viceversa.

### 3.14 `/zona-de-asociados/` — Zona de Asociados (Videoteca)
- Videoteca de conversatorios grabados, organizada por año y mes
- Acceso aparentemente público (no se detectó restricción de login)
- Contiene videos desde 2022; última actualización: mayo 2025
- Formato: título del evento, ponentes, fecha, botón "VER VIDEO"

### 3.15 `/contacto/` — Contacto
- Datos de contacto: teléfonos, celular, email
- **Formulario CF7:**
  - Nombre, Correo, Asunto, Mensaje
  - Acción: `POST /contacto/#wpcf7-f5-p29-o1`
- Mapa de ubicación (embebido)

### 3.16 `/registro/` — Registro / Login
- Página WooCommerce de registro/login
- Si el usuario ya está logueado: muestra "Ya estás registrado"
- Permite crear cuenta con email y contraseña (cuenta WooCommerce)

### 3.17 Otras páginas publicadas
| Slug | Título |
|---|---|
| `/articulos/` | Artículos (30 posts — biblioteca de artículos tributarios) |
| `/eventos/` | Eventos (página general de eventos) |
| `/politica-privacidad/` | Política de Privacidad |
| `/carrito/` | Carrito WooCommerce |
| `/finalizar-compra/` | Checkout WooCommerce |

---

## 4. Post Types y Categorías

Los eventos/contenido se publica como **Posts** de WordPress con categorías:

| Categoría | Slug | Posts |
|---|---|---|
| Conversatorio | conversatorio | 99 |
| Portafolio de Eventos | portafolio-de-eventos | 42 |
| Artículos | articulos | 30 |
| Eventos | eventos | 20 |
| Asamblea | asamblea | 4 |
| Foros | foros | 4 |
| Evento Institucional | evento-institucional | 1 |
| Ipidet Te Informa | ipidet-te-informa | 1 |

---

## 5. WordPress REST API

**Endpoint base:** `https://ipidet.org/wp-json/`  
**Total de rutas:** 879

**Namespaces disponibles:**
- `wp/v2` — WordPress core (posts, pages, media, users, categories, etc.)
- `wc/v3`, `wc/v2`, `wc/v1` — WooCommerce (requiere autenticación)
- `wc/store/v1` — WooCommerce Store API (semi-pública)
- `contact-form-7/v1` — CF7 API
- `elementor/v1`, `elementor-pro/v1` — Elementor API
- `jet-menu-api/v2`, `jet-elements-api/v1`, `jet-blocks-api/v1`, `jet-tabs-api/v1`, `jet-theme-core-api/v2`
- `wp-rocket/v1` — WP Rocket API
- `brave/v1` — Brave Popup Builder API
- `code-snippets/v1` — Code Snippets API
- `jetpack/v4` — Jetpack API

**Acceso:**
- Endpoints públicos (`wp/v2/posts`, `/pages`, `/categories`): accesibles sin auth
- WooCommerce (`wc/v3`): requiere Basic Auth o Consumer Key/Secret → devuelve 401 sin credenciales

---

## 6. Plugins activos (detectados)

| Plugin | Función |
|---|---|
| Elementor + Elementor Pro | Page builder visual |
| JetPlugins suite | Componentes avanzados (mega-menu, sliders, tabs, etc.) |
| WooCommerce | E-commerce: tienda, carrito, cuenta de usuario |
| Contact Form 7 (CF7) | Formularios de contacto y admisión |
| Akismet | Antispam para formularios |
| Brave Popup Builder | Pop-ups (CTA "Actualiza tus datos" probablemente) |
| bdthemes Prime Slider | Slider del hero |
| WP Rocket | Cache y optimización de velocidad |
| Code Snippets | Snippets PHP personalizados |
| Jetpack | Estadísticas y otras utilidades |

---

## 7. Flujos actuales de usuario

### Socio quiere pagar su cuota
1. Va a `/pagos/` → elige producto (Pago Ordinario S/780 o Fraccionamiento)
2. Lo agrega al carrito
3. Va a `/finalizar-compra/` y paga online
4. El pago queda registrado en WooCommerce **pero NO en el padrón MongoDB**

### Postulante nuevo quiere inscribirse
1. Va a `/admision/`
2. Descarga PDFs (solicitud, guía, requisitos)
3. Llena la ficha y la adjunta al formulario CF7
4. El formulario llega por email a `administracion@ipidet.org`
5. El admin procesa manualmente y agrega al padrón

### Socio quiere ver sus datos de cuenta
1. Entra por `/registro/` o el ícono de usuario
2. Navega por las tabs de WooCommerce (Pagos, Información)
3. Solo puede editar: nombre, apellidos, nombre visible, email, contraseña
4. **No puede ver su estado de cuota IPIDET**, porque eso está en MongoDB

---

## 8. Brechas y oportunidades de integración / automatización

### 8.1 GAP CRÍTICO: Dos bases de datos desconectadas
- **WooCommerce** tiene sus propios usuarios y pedidos
- **MongoDB** tiene el padrón real con el estado de cuotas
- Un socio puede pagar en WooCommerce y ese pago NO se refleja en MongoDB
- El admin tiene que actualizar el padrón manualmente

### 8.2 Integraciones posibles

| # | Automatización | Complejidad | Valor |
|---|---|---|---|
| 1 | **Portal de socio propio**: página FastAPI embebida/linkeada que muestra estado de cuota MongoDB por email + OTP | Media | Alto |
| 2 | **Webhook WooCommerce → FastAPI**: cuando un socio paga en `/pagos/`, WooCommerce notifica a FastAPI y actualiza automáticamente el estado en MongoDB | Media | Muy alto |
| 3 | **Formulario de admisión → MongoDB**: el CF7 de `/admision/` puede enviar un webhook a FastAPI al recibir una postulación | Baja | Alto |
| 4 | **Formulario de contacto → agente de email**: el CF7 de `/contacto/` puede redirigir consultas al agente de email en lugar de ir solo a administracion@ | Baja | Medio |
| 5 | **Publicación de eventos automatizada**: cuando se crea un evento en WordPress, notificar a socios por email automáticamente | Media | Medio |
| 6 | **Newsletter JetSubscribe → email agent**: suscripciones van al agente, que gestiona el envío de boletines | Media | Medio |
| 7 | **Sincronización padrón → WooCommerce users**: crear/actualizar usuarios WP con los emails del padrón MongoDB para que los socios puedan loguearse | Alta | Alto |
| 8 | **Recordatorio de cuotas morosas → email**: el scheduler de FastAPI ya existe; conectarlo al email del socio que está en el padrón | Baja | Muy alto |
| 9 | **REST API pública de estado de socio**: endpoint FastAPI autenticado que WordPress puede consultar vía JS para mostrar datos en cualquier página | Media | Alto |

### 8.3 La integración más viable inmediata
**WooCommerce Webhook → FastAPI** (integración #2):

WooCommerce tiene webhooks nativos. Cuando `order.completed`, manda un POST a una URL. FastAPI recibe el payload, busca el email del comprador en el padrón y actualiza el estado de pago en MongoDB. Esto cierra el gap más grande.

**Configuración en WP:** WooCommerce → Ajustes → Avanzado → Webhooks → Nuevo webhook  
- Evento: `Pedido actualizado`  
- URL de entrega: `https://[tu-servidor]/webhook/woocommerce/order`  
- Secreto: clave compartida para verificar autenticidad

---

## 9. Notas técnicas importantes

- El área `/asociados/` es la página **WooCommerce My Account** — las tabs "Videos Exclusivos" y "Biblioteca" son personalizaciones de esa área (probablemente con JetWooBuilder o un plugin de membership)
- El botón flotante "ACTUALIZA TUS DATOS" probablemente abre un popup de Brave Popup Builder con un formulario CF7
- La sección `/zona-de-asociados/` con la videoteca parece pública (no requiere login al momento del relevamiento)
- Los documentos PDF de `/transparencia/` y `/admision/` están hospedados en `/wp-content/uploads/`
- WP Rocket puede cachear respuestas — si se implementa un portal dinámico embebido, hay que excluir esas URLs del cache
- El dominio `ipidet.org` y el servidor FastAPI son distintos — para llamadas JS cross-origin habrá que configurar CORS en FastAPI
