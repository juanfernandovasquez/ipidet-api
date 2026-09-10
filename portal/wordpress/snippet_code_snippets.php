<?php
/**
 * IPIDET – Portal de Socio
 *
 * Pegar en: WordPress Admin → Code Snippets → Añadir nuevo
 * Tipo: "Ejecutar en todas partes" (run everywhere)
 *
 * Este snippet hace DOS cosas:
 *   1. Expone un endpoint REST de WordPress que actúa como PROXY seguro hacia FastAPI.
 *      El secreto PORTAL_SECRET nunca llega al navegador del socio.
 *      Las respuestas se cachean 1 hora en WP Transients; si la API cae, se sirve el
 *      último dato conocido (backup de 7 días) con degradación elegante.
 *   2. Inyecta en /asociados/ el JS que llama a ese endpoint y pinta el widget.
 */

define('IPIDET_PORTAL_API_BASE', 'https://ipidet-api.onrender.com');
define('IPIDET_PORTAL_SECRET',   'ipidet_portal_2026');

// ─────────────────────────────────────────────────────────────────────────────
// 1. ENDPOINT REST PROXY (server-to-server, el secret nunca sale al browser)
// ─────────────────────────────────────────────────────────────────────────────

add_action('rest_api_init', function() {
    register_rest_route('ipidet/v1', '/member-status', [
        'methods'             => 'GET',
        'callback'            => 'ipidet_member_status_handler',
        'permission_callback' => function() {
            return is_user_logged_in();
        },
    ]);
});

function ipidet_member_status_handler(WP_REST_Request $request) {
    $user  = wp_get_current_user();
    $email = sanitize_email($user->user_email);

    if (empty($email)) {
        return new WP_Error('no_email', 'Sin email de usuario', ['status' => 400]);
    }

    $cache_key = 'ipidet_' . md5($email);

    // Intentar caché principal (1 hora)
    $cached = get_transient($cache_key);
    if ($cached !== false) {
        return rest_ensure_response($cached);
    }

    $api_url  = IPIDET_PORTAL_API_BASE . '/api/portal/member-status';
    $response = wp_remote_get(add_query_arg(['email' => $email, 'wp_user_id' => $user->ID], $api_url), [
        'timeout' => 8,
        'headers' => [
            'Authorization' => 'Bearer ' . IPIDET_PORTAL_SECRET,
        ],
    ]);

    if (is_wp_error($response)) {
        // API caída: devolver backup (hasta 7 días) con flag _stale para el JS
        $backup = get_transient($cache_key . '_bk');
        if ($backup !== false) {
            $backup['_stale'] = true;
            return rest_ensure_response($backup);
        }
        return new WP_Error('api_error', 'No se pudo conectar con el servidor IPIDET', ['status' => 502]);
    }

    $body = wp_remote_retrieve_body($response);
    $data = json_decode($body, true);
    if (!$data) {
        return new WP_Error('api_error', 'Respuesta inválida', ['status' => 502]);
    }

    set_transient($cache_key,          $data, HOUR_IN_SECONDS);           // caché normal: 1 hora
    set_transient($cache_key . '_bk',  $data, DAY_IN_SECONDS * 7);        // backup: 7 días

    return rest_ensure_response($data);
}

// ─────────────────────────────────────────────────────────────────────────────
// 2. INVALIDAR CACHÉ CUANDO SE COMPLETA UN PEDIDO WOOCOMMERCE
//    Esto garantiza que el socio vea el pago actualizado en cuanto vuelve al portal.
// ─────────────────────────────────────────────────────────────────────────────

function ipidet_clear_portal_cache($order_id) {
    $order = wc_get_order($order_id);
    if (!$order) return;
    $email = sanitize_email($order->get_billing_email());
    if (!$email) return;
    $cache_key = 'ipidet_' . md5($email);
    delete_transient($cache_key);  // El backup (_bk) lo mantenemos como fallback
}
add_action('woocommerce_order_status_completed',  'ipidet_clear_portal_cache');
add_action('woocommerce_order_status_processing', 'ipidet_clear_portal_cache');

// ── 3. OCULTAR BARRA DE ADMIN A USUARIOS NO-ADMINISTRADORES ─────────────────

add_filter('show_admin_bar', function($show) {
    if (!current_user_can('manage_options')) return false;
    return $show;
});

// ── 4. OCULTAR BARRA SUPERIOR (elementor-element-e92d977) A NO-ADMINS ─────────

add_action('wp_head', function() {
    if (!current_user_can('manage_options')) {
        echo '<style>.elementor-element-e92d977 { display:none !important; }</style>';
    }
});

// ── 5. MENÚ DE "MI CUENTA" — QUITAR ÍTEMS DE TIENDA Y RENOMBRAR DASHBOARD ────

add_filter('woocommerce_account_menu_items', function($items) {
    $items['dashboard'] = 'Inicio';
    unset($items['orders']);        // Pedidos
    unset($items['downloads']);     // Descargas
    unset($items['edit-address']);  // Dirección
    return $items;
});

// ── 6. INYECTAR EL DIV DEL WIDGET EN EL DASHBOARD DE MI CUENTA ──────────────

add_action('woocommerce_account_dashboard', function() {
    echo '<div id="ipidet-portal-widget"></div>';
}, 1);

// ── 6. JS + CSS EN PÁGINAS DE MI CUENTA ─────────────────────────────────────

add_action('wp_footer', function() {
    if (!is_user_logged_in()) return;
    if (!is_account_page()) return;

    $nonce    = wp_create_nonce('wp_rest');
    $rest_url = esc_url(rest_url('ipidet/v1/member-status'));
    ?>
    <style>
    #ipidet-portal-widget {
        background: #fff;
        border: 1px solid #e2e8f0;
        border-radius: 12px;
        padding: 24px;
        margin: 24px 0;
        font-family: "Open Sans", sans-serif;
        max-width: 680px;
    }
    #ipidet-portal-widget h3 {
        font-size: 1rem;
        font-weight: 700;
        color: #00913d;
        margin: 0 0 16px 0;
        display: flex;
        align-items: center;
        gap: 8px;
    }
    .ipidet-member-meta {
        display: flex;
        flex-wrap: wrap;
        gap: 8px 24px;
        margin-bottom: 20px;
        font-size: .85rem;
        color: #555;
    }
    .ipidet-member-meta strong { color: #0a0a0a; }
    .ipidet-payments-table {
        width: 100%;
        border-collapse: collapse;
        font-size: .85rem;
    }
    .ipidet-payments-table th {
        text-align: left;
        padding: 6px 12px;
        background: #f6f6f6;
        color: #555;
        font-weight: 700;
        font-size: .75rem;
        text-transform: uppercase;
        letter-spacing: .04em;
        border-bottom: 2px solid #e2e8f0;
    }
    .ipidet-payments-table td {
        padding: 10px 12px;
        border-bottom: 1px solid #f0f0f0;
        color: #0a0a0a;
        vertical-align: middle;
    }
    .ipidet-badge {
        display: inline-block;
        padding: 2px 10px;
        border-radius: 999px;
        font-size: .75rem;
        font-weight: 600;
    }
    .ipidet-badge-green  { background: #e6f7ee; color: #00913d; }
    .ipidet-badge-red    { background: #fee2e2; color: #991b1b; }
    .ipidet-badge-amber  { background: #fef3c7; color: #92400e; }
    .ipidet-badge-gray   { background: #f1f5f9; color: #555; }
    .ipidet-badge-yellow { background: #fef9c3; color: #713f12; }
    .ipidet-cuotas-detail {
        margin-top: 6px;
        font-size: .78rem;
        color: #555;
    }
    .ipidet-not-found {
        color: #999;
        font-size: .9rem;
        text-align: center;
        padding: 20px 0;
    }
    .ipidet-loading {
        color: #999;
        font-size: .85rem;
        padding: 12px 0;
    }
    .ipidet-stale-notice {
        font-size: .75rem;
        color: #92400e;
        background: #fef3c7;
        border-radius: 6px;
        padding: 4px 10px;
        margin-bottom: 12px;
        display: inline-block;
    }
    .ipidet-pay-btn {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        margin-top: 6px;
        padding: 5px 14px;
        background: #00913d;
        color: #fff !important;
        border-radius: 6px;
        font-size: .78rem;
        font-weight: 600;
        text-decoration: none !important;
        transition: background .15s;
    }
    .ipidet-pay-btn:hover { background: #007a33; }
    .ipidet-pay-btn svg { width: 13px; height: 13px; flex-shrink: 0; }

    /* ── Botón ojo dentro del input de contraseña ───────────────────────── */
    .ipidet-pw-wrap { position: relative; }
    .ipidet-pw-wrap .woocommerce-Input--password,
    .ipidet-pw-wrap input[type="password"],
    .ipidet-pw-wrap input[type="text"] { padding-right: 40px !important; }
    .show-password-input {
        position: absolute;
        right: 10px;
        top: 50%;
        transform: translateY(-50%);
        width: 22px;
        height: 22px;
        padding: 0;
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
        outline: none;
        cursor: pointer;
        color: #aaa;
        transition: color .15s;
        font-size: 0;
        display: flex;
        align-items: center;
        justify-content: center;
    }
    .show-password-input:hover { color: #00913d !important; }
    .show-password-input::before {
        content: '';
        display: block;
        width: 18px;
        height: 18px;
        background-color: currentColor;
        transition: background-color .15s;
        -webkit-mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z'/%3E%3Ccircle cx='12' cy='12' r='3'/%3E%3C/svg%3E");
        mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z'/%3E%3Ccircle cx='12' cy='12' r='3'/%3E%3C/svg%3E");
        -webkit-mask-size: contain; mask-size: contain;
        -webkit-mask-repeat: no-repeat; mask-repeat: no-repeat;
        -webkit-mask-position: center; mask-position: center;
    }
    .show-password-input.display-password::before {
        -webkit-mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24'/%3E%3Cline x1='1' y1='1' x2='23' y2='23'/%3E%3C/svg%3E");
        mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24'/%3E%3Cline x1='1' y1='1' x2='23' y2='23'/%3E%3C/svg%3E");
    }
    </style>

    <script>
    (function() {
        var NONCE    = <?php echo json_encode($nonce); ?>;
        var REST_URL = <?php echo json_encode($rest_url); ?>;

        var BADGE = {
            'pagado':          'ipidet-badge-green',
            'debe':            'ipidet-badge-red',
            'fraccionamiento': 'ipidet-badge-amber',
            'exonerado':       'ipidet-badge-gray',
            'no_aplica':       'ipidet-badge-gray',
            'pendiente':       'ipidet-badge-yellow',
            'retirar':         'ipidet-badge-gray',
            'en_revision':     'ipidet-badge-amber',
            'revisar':         'ipidet-badge-red',
        };

        function badge(estado, label) {
            var cls = BADGE[estado] || 'ipidet-badge-gray';
            return '<span class="ipidet-badge ' + cls + '">' + label + '</span>';
        }

        var ICON_CART = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="14" height="14" style="vertical-align:middle;flex-shrink:0" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="21" r="1"/><circle cx="20" cy="21" r="1"/><path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"/></svg>';

        function renderPayments(payments) {
            if (!payments || payments.length === 0) {
                return '<p class="ipidet-not-found">No se encontraron registros de cuotas.</p>';
            }

            var rows = payments.map(function(p) {
                // ── Detalle de cuotas de fraccionamiento ──
                var cuotaInfo = '';
                if (p.estado === 'fraccionamiento' && p.cuotas_total > 0) {
                    cuotaInfo = '<div class="ipidet-cuotas-detail">' +
                        p.cuotas_pagadas + '/' + p.cuotas_total + ' cuotas pagadas';
                    if (p.cuotas && p.cuotas.length > 0) {
                        cuotaInfo += '<ul style="margin:4px 0 0 12px;padding:0;">';
                        p.cuotas.forEach(function(c) {
                            var cLabel = c.estado === 'pagado'
                                ? '✓ S/' + c.monto + (c.fecha_pago ? ' (' + c.fecha_pago + ')' : '')
                                : '○ S/' + c.monto + (c.fecha_venc ? ' — vence ' + c.fecha_venc : '');
                            cuotaInfo += '<li>' + cLabel + '</li>';
                        });
                        cuotaInfo += '</ul>';
                    }
                    cuotaInfo += '</div>';
                }

                // ── Botón de pago (solo si el socio debe ese año y hay producto WC configurado) ──
                var payBtn = '';
                if (p.wc_pay_url) {
                    payBtn = '<div style="margin-top:6px">' +
                        '<a href="' + p.wc_pay_url + '" class="ipidet-pay-btn">' +
                        ICON_CART + ' Pagar ' + p.periodo +
                        '</a>' +
                        '</div>';
                }

                var estadoCell = badge(p.estado, p.estado_label) + cuotaInfo + payBtn;
                var empresaCell = p.empresa_pagadora
                    ? '<span style="color:#475569">' + p.empresa_pagadora + '</span>'
                    : '<span style="color:#cbd5e1">—</span>';
                var fechaCell = p.fecha_pago || '<span style="color:#cbd5e1">—</span>';

                return '<tr>' +
                    '<td><strong>' + p.periodo + '</strong></td>' +
                    '<td>' + estadoCell + '</td>' +
                    '<td>' + empresaCell + '</td>' +
                    '<td>' + fechaCell + '</td>' +
                    '</tr>';
            }).join('');

            return '<table class="ipidet-payments-table">' +
                '<thead><tr>' +
                '<th>Período</th><th>Estado</th><th>Empresa pag.</th><th>Fecha pago</th>' +
                '</tr></thead>' +
                '<tbody>' + rows + '</tbody>' +
                '</table>';
        }

        function renderWidget(data) {
            var container = document.getElementById('ipidet-portal-widget');
            if (!container) return;

            if (!data.found) {
                container.innerHTML =
                    '<h3>📋 Mi estado de membresía</h3>' +
                    '<p class="ipidet-not-found">Tu email no está registrado en el padrón de IPIDET.<br>' +
                    '<small>Consulta con <a href="mailto:administracion@ipidet.org">administracion@ipidet.org</a>.</small></p>';
                return;
            }

            var estadoBadge = data.estado === 'activo'
                ? '<span class="ipidet-badge ipidet-badge-green">Activo</span>'
                : '<span class="ipidet-badge ipidet-badge-gray">' + data.estado_label + '</span>';

            var staleNotice = data._stale
                ? '<div class="ipidet-stale-notice">⚠️ Información con datos en caché. Actualiza la página más tarde.</div>'
                : '';

            container.innerHTML =
                '<h3>📋 Mi estado de membresía</h3>' +
                staleNotice +
                '<div class="ipidet-member-meta">' +
                    '<span><strong>Socio:</strong> ' + data.nombre + '</span>' +
                    '<span><strong>N°:</strong> ' + data.member_id + '</span>' +
                    (data.titulo ? '<span><strong>Título:</strong> ' + data.titulo + '</span>' : '') +
                    (data.dni ? '<span><strong>DNI:</strong> ' + data.dni + '</span>' : '') +
                    '<span><strong>Estado:</strong> ' + estadoBadge + '</span>' +
                '</div>' +
                renderPayments(data.payments);
        }

        function loadPortalData() {
            var container = document.getElementById('ipidet-portal-widget');
            if (!container) return;

            // Quitar párrafos por defecto de WooCommerce (saludo + descripción de tienda)
            var mcContent = document.querySelector('.woocommerce-MyAccount-content');
            if (mcContent) {
                Array.from(mcContent.querySelectorAll(':scope > p')).forEach(function(p) { p.remove(); });
            }

            container.innerHTML = '<p class="ipidet-loading">⏳ Cargando tu información…</p>';

            fetch(REST_URL, {
                headers: {
                    'X-WP-Nonce': NONCE,
                }
            })
            .then(function(r) { return r.json(); })
            .then(function(data) { renderWidget(data); })
            .catch(function() {
                var container = document.getElementById('ipidet-portal-widget');
                if (container) container.innerHTML =
                    '<p class="ipidet-not-found">No se pudo cargar tu información. Intenta más tarde.</p>';
            });
        }

        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', loadPortalData);
        } else {
            loadPortalData();
        }

        // Wrap password inputs with their toggle buttons so the eye icon
        // sits inside the field. Runs on window.load because WooCommerce
        // injects .show-password-input buttons after DOMContentLoaded.
        function wrapPasswordFields() {
            document.querySelectorAll('.show-password-input').forEach(function(btn) {
                var input = btn.previousElementSibling;
                if (!input || input.tagName !== 'INPUT') return;
                if (input.parentNode.classList.contains('ipidet-pw-wrap')) return;
                var wrap = document.createElement('div');
                wrap.className = 'ipidet-pw-wrap';
                input.parentNode.insertBefore(wrap, input);
                wrap.appendChild(input);
                wrap.appendChild(btn);
            });
        }
        window.addEventListener('load', wrapPasswordFields);

    })();
    </script>
    <?php
}, 20);

// ── 7. SECCIÓN "INFORMACIÓN" — EMAIL ALTERNATIVO LABORAL ─────────────────────

add_action('woocommerce_edit_account_form', function() {
    $user     = wp_get_current_user();
    $primary  = sanitize_email($user->user_email);
    $nonce_field = wp_create_nonce('ipidet_account_fields');

    $cache_key = 'ipidet_' . md5($primary);
    $cached    = get_transient($cache_key);
    $alt_email = '';
    $dni       = '';
    if ($cached) {
        if (isset($cached['emails'])) {
            foreach ($cached['emails'] as $em) {
                if (empty($em['principal']) && ($em['estado'] ?? '') === 'habilitado') {
                    $alt_email = $em['email'];
                    break;
                }
            }
        }
        if (isset($cached['dni'])) {
            $dni = $cached['dni'];
        }
    }
    ?>
    <fieldset style="margin-top:2rem;padding-top:1.5rem;border-top:1px solid #e2e8f0;">
        <legend style="font-weight:700;font-size:.95rem;color:#1e3a5f;margin-bottom:1rem;">
            Datos IPIDET
        </legend>
        <p class="woocommerce-form-row">
            <label for="ipidet_dni">DNI</label>
            <input type="text" id="ipidet_dni" name="ipidet_dni"
                   class="woocommerce-Input woocommerce-Input--text input-text"
                   value="<?php echo esc_attr($dni); ?>"
                   placeholder="12345678" maxlength="8"
                   style="width:100%;">
            <span class="description" style="font-size:.8rem;color:#94a3b8;">
                Documento Nacional de Identidad. Se usa para verificar tu identidad como socio.
            </span>
        </p>
        <p class="woocommerce-form-row">
            <label>Correo principal (login)</label>
            <input type="email" value="<?php echo esc_attr($primary); ?>"
                   class="woocommerce-Input woocommerce-Input--email input-text"
                   disabled style="background:#f8fafc;color:#64748b;cursor:not-allowed;width:100%;">
            <span class="description" style="font-size:.8rem;color:#94a3b8;">
                Este es el correo que usas para ingresar. Para cambiarlo usa el campo "Dirección de correo" de arriba.
            </span>
        </p>
        <p class="woocommerce-form-row">
            <label for="ipidet_alt_email">Correo alternativo (laboral)</label>
            <input type="email" id="ipidet_alt_email" name="ipidet_alt_email"
                   class="woocommerce-Input woocommerce-Input--email input-text"
                   value="<?php echo esc_attr($alt_email); ?>"
                   placeholder="correo@empresa.com"
                   style="width:100%;">
            <span class="description" style="font-size:.8rem;color:#94a3b8;">
                Correo de trabajo. Solo lo usa IPIDET para comunicaciones, no sirve para iniciar sesión.
            </span>
        </p>
        <input type="hidden" name="ipidet_account_fields_nonce" value="<?php echo esc_attr($nonce_field); ?>">
    </fieldset>
    <?php
});

add_action('woocommerce_save_account_details', function($user_id) {
    if (empty($_POST['ipidet_account_fields_nonce'])) return;
    if (!wp_verify_nonce($_POST['ipidet_account_fields_nonce'], 'ipidet_account_fields')) return;

    $user    = get_userdata($user_id);
    $primary = sanitize_email($user->user_email);

    $wp_user_id = (int) $user_id;

    $alt_email = sanitize_email($_POST['ipidet_alt_email'] ?? '');
    if (!empty($alt_email)) {
        wp_remote_post(IPIDET_PORTAL_API_BASE . '/api/portal/update-alternative-email', [
            'timeout' => 8,
            'headers' => [
                'Content-Type'  => 'application/json',
                'Authorization' => 'Bearer ' . IPIDET_PORTAL_SECRET,
            ],
            'body' => json_encode([
                'primary_email'     => $primary,
                'alternative_email' => $alt_email,
                'wp_user_id'        => $wp_user_id,
            ]),
        ]);
    }

    $dni = sanitize_text_field($_POST['ipidet_dni'] ?? '');
    if (!empty($dni)) {
        wp_remote_post(IPIDET_PORTAL_API_BASE . '/api/portal/update-dni', [
            'timeout' => 8,
            'headers' => [
                'Content-Type'  => 'application/json',
                'Authorization' => 'Bearer ' . IPIDET_PORTAL_SECRET,
            ],
            'body' => json_encode([
                'primary_email' => $primary,
                'dni'           => $dni,
                'wp_user_id'    => $wp_user_id,
            ]),
        ]);
    }

    delete_transient('ipidet_' . md5($primary));
});
