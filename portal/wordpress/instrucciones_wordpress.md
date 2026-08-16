# Instrucciones: configurar el portal de socio en WordPress

## Paso 1 — Añadir el snippet PHP (Code Snippets)

1. En WordPress admin → **Code Snippets → Añadir nuevo**
2. Nombre: `IPIDET Portal de Socio`
3. Copia todo el contenido de `snippet_code_snippets.php` y pégalo en el editor
4. **Antes de guardar**, edita estas dos líneas al inicio del snippet:
   ```php
   define('IPIDET_PORTAL_API_BASE', 'http://localhost:8000');
   //                                ↑ Cambiar cuando FastAPI esté en servidor público
   //                                  Ej: 'https://api.ipidet.org'
   
   define('IPIDET_PORTAL_SECRET',   'CAMBIAR_POR_VALOR_DEL_.ENV');
   //                                ↑ Debe ser idéntico a PORTAL_SECRET en el .env de FastAPI
   ```
5. Scope: **"Run everywhere"** (ejecutar en todas partes)
6. Clic en **Guardar cambios y activar**

---

## Paso 2 — Agregar el widget en la página /asociados/

1. Ve a la página `/asociados/` → **Editar con Elementor**
2. Encuentra la sección donde quieres que aparezca el panel de cuotas
   (recomendado: debajo del menú de tabs Pagos/Información/etc.)
3. Arrastra un widget **"HTML"** al lugar deseado
4. Abre `elementor_block.html` y pega su contenido en el campo HTML
5. Clic en **Publicar / Actualizar**

---

## Paso 3 — Configurar el Webhook en WooCommerce

1. En WordPress admin → **WooCommerce → Ajustes → Avanzado → Webhooks**
2. Clic en **Añadir webhook**
3. Configura:
   - **Nombre:** IPIDET Actualizar Cuotas
   - **Estado:** Activo
   - **Tema:** Pedido actualizado
   - **URL de entrega:** `https://TU_SERVIDOR/webhook/woocommerce/order`
   - **Secreto:** *(genera un string aleatorio largo — este mismo valor va en WC_WEBHOOK_SECRET en el .env)*
   - **Versión API:** WP REST API Integration v3
4. Clic en **Guardar webhook**

---

## Paso 4 — Variables de entorno en FastAPI (.env)

Agrega estas líneas al archivo `.env`:

```env
PORTAL_SECRET=una_clave_larga_y_aleatoria_aqui
WC_WEBHOOK_SECRET=otra_clave_larga_para_el_webhook_woocommerce
PORTAL_API_BASE=http://localhost:8000
```

**PORTAL_SECRET** debe ser idéntico al valor en `snippet_code_snippets.php`.

---

## Resumen del flujo de seguridad

```
Socio (browser)
  │ ① Llama /wp-json/ipidet/v1/member-status
  │   con WP nonce (cookie de sesión) — mismo dominio, sin secretos
  ▼
WordPress PHP (servidor)
  │ ② Verifica que el usuario esté logueado
  │ ③ Obtiene el email server-side (nunca pasa por el browser)
  │ ④ Llama a FastAPI con Authorization: Bearer PORTAL_SECRET
  ▼
FastAPI (servidor)
  │ ⑤ Verifica el PORTAL_SECRET
  │ ⑥ Busca el email en MongoDB
  ▼
Respuesta → WordPress → Socio (browser)
```

**¿Qué pasa si alguien intenta atacar la API directamente?**
- Sin el PORTAL_SECRET correcto → 401 Unauthorized
- El secreto nunca aparece en el código JavaScript del browser
- CORS solo permite llamadas desde ipidet.org
- El webhook WooCommerce verifica firma HMAC-SHA256
