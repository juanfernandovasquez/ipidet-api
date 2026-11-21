/* IPIDET — interacción compartida del sitio público (sin dependencias).
   Pinta las secciones marcadas con data-render usando window.IPIDET_DATOS y, si la plataforma
   responde, las vuelve a pintar con sus datos (/api/web/sitio). Formularios → /api/web/*. */
(function () {
  "use strict";
  const D = window.IPIDET_DATOS || {};
  const $ = (s, c = document) => c.querySelector(s);
  const $$ = (s, c = document) => Array.from(c.querySelectorAll(s));
  const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "set", "oct", "nov", "dic"];
  const MESES_LARGOS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "setiembre", "octubre", "noviembre", "diciembre"];
  const hoyISO = new Date().toISOString().slice(0, 10);

  const esc = (t) => String(t == null ? "" : t).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const icono = (n, extra = "") => `<svg class="icono ${extra}" aria-hidden="true"><use href="#i-${n}"></use></svg>`;
  const fecha = (iso) => {
    const [a, m, d] = String(iso || "").split("-").map(Number);
    if (!a || !m) return { dia: "", mes: "", larga: "" };
    return { dia: d || "", mes: MESES[m - 1], larga: `${d ? d + " de " : ""}${MESES_LARGOS[m - 1]} de ${a}`, anio: a };
  };
  const tipoEvento = (t) => {
    const s = (t || "").toLowerCase();
    if (s.startsWith("conversatorio")) return ["conversatorio", "Conversatorio"];
    if (s.startsWith("presentación") || s.startsWith("presentacion")) return ["presentacion", "Presentación"];
    if (s.includes("foro")) return ["evento", "Foro"];
    return ["evento", "Evento"];
  };
  const tituloCorto = (t) => String(t || "").replace(/^conversatorio institucional:\s*/i, "").replace(/^presentación( de libro)?:\s*/i, "");

  /* ── Menú, cabecera, volver arriba ─────────────────────────────────────── */
  function menu() {
    const cajon = $(".cajon"), abrir = $(".boton-menu");
    if (!cajon || !abrir) return;
    const cerrar = () => { cajon.classList.remove("abierto"); abrir.setAttribute("aria-expanded", "false"); document.body.style.overflow = ""; abrir.focus(); };
    abrir.addEventListener("click", () => {
      cajon.classList.add("abierto"); abrir.setAttribute("aria-expanded", "true"); document.body.style.overflow = "hidden";
      setTimeout(() => $(".cajon__panel a", cajon)?.focus(), 50);
    });
    $$("[data-cerrar-menu]", cajon).forEach((b) => b.addEventListener("click", cerrar));
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && cajon.classList.contains("abierto")) cerrar(); });
  }
  function cabecera() {
    const cab = $(".cabecera"), arriba = $(".arriba");
    const alMover = () => {
      cab?.classList.toggle("con-sombra", scrollY > 8);
      arriba?.classList.toggle("visible", scrollY > 700);
    };
    addEventListener("scroll", alMover, { passive: true }); alMover();
    arriba?.addEventListener("click", () => scrollTo({ top: 0, behavior: "smooth" }));
  }

  /* ── Aparición al hacer scroll y contadores ────────────────────────────── */
  let observador;
  function aparecer(raiz = document) {
    $$("[data-escalonar]", raiz).forEach((g) => [...g.children].forEach((h, i) => {
      h.classList.add("aparece"); h.style.setProperty("--retardo", `${Math.min(i, 8) * 70}ms`);
    }));
    const pendientes = $$(".aparece:not(.visto)", raiz);
    if (!("IntersectionObserver" in window)) { pendientes.forEach((e) => e.classList.add("visto")); return; }
    observador = observador || new IntersectionObserver((entradas) => entradas.forEach((en) => {
      if (!en.isIntersecting) return;
      en.target.classList.add("visto"); observador.unobserve(en.target);
      if (en.target.matches("[data-contar]")) contar(en.target);
      $$("[data-contar]", en.target).forEach(contar);
    }), { rootMargin: "0px 0px -8% 0px", threshold: .12 });
    pendientes.forEach((e) => observador.observe(e));
  }
  function contar(el) {
    if (el.dataset.contado) return; el.dataset.contado = "1";
    const fin = Number(el.dataset.contar), dur = 1300, t0 = performance.now();
    const reducido = matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reducido || !fin) { el.textContent = fin.toLocaleString("es-PE"); return; }
    const paso = (t) => {
      const p = Math.min(1, (t - t0) / dur), v = Math.round(fin * (1 - Math.pow(1 - p, 3)));
      el.textContent = v.toLocaleString("es-PE");
      if (p < 1) requestAnimationFrame(paso);
    };
    requestAnimationFrame(paso);
  }

  /* ── Ventana (flyers y detalle de eventos) ─────────────────────────────── */
  let ultimoFoco = null;
  function ventana(html) {
    const v = $("#ventana"); if (!v) return;
    $(".ventana__contenido", v).innerHTML = html;
    ultimoFoco = document.activeElement;
    v.classList.add("abierta"); v.setAttribute("aria-hidden", "false"); document.body.style.overflow = "hidden";
    $(".ventana__cerrar", v).focus();
  }
  function cerrarVentana() {
    const v = $("#ventana"); if (!v || !v.classList.contains("abierta")) return;
    v.classList.remove("abierta"); v.setAttribute("aria-hidden", "true"); document.body.style.overflow = "";
    ultimoFoco?.focus();
  }
  function prepararVentana() {
    const v = $("#ventana"); if (!v) return;
    $$("[data-cerrar-ventana]", v).forEach((b) => b.addEventListener("click", cerrarVentana));
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") cerrarVentana(); });
  }
  function detalleEvento(ev) {
    const f = fecha(ev.fecha), [tipo, etiqueta] = tipoEvento(ev.titulo), proximo = ev.fecha >= hoyISO;
    ventana(`
      ${ev.flyer ? `<div class="ventana__img"><img src="${esc(ev.flyer)}" alt="Flyer: ${esc(ev.titulo)}"></div>` : ""}
      <div class="ventana__cuerpo">
        <div><span class="tipo ${proximo ? "tipo--proximo" : "tipo--" + tipo}">${proximo ? "Próximo · " : ""}${etiqueta}</span></div>
        <h3 id="ventana-titulo">${esc(ev.titulo)}</h3>
        <div class="evento__meta">
          ${f.larga ? `<span>${icono("calendario")} ${f.larga}${ev.hora ? " · " + esc(ev.hora) : ""}</span>` : ""}
          ${ev.lugar || ev.modalidad ? `<span>${icono("lugar")} ${esc([ev.modalidad, ev.lugar].filter(Boolean).join(" · "))}</span>` : ""}
          ${ev.precio ? `<span>${icono("recibo")} ${esc(ev.precio)}</span>` : ""}
        </div>
        ${ev.descripcion ? `<p>${esc(ev.descripcion)}</p>` : ""}
        <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:6px">
          ${ev.registro ? `<a class="btn btn--primario" href="${esc(ev.registro)}" target="_blank" rel="noopener">Inscribirme ${icono("flecha", "icono--flecha")}</a>` : ""}
          ${!proximo && D.contacto?.youtube ? `<a class="btn btn--borde" href="${esc(D.contacto.youtube)}" target="_blank" rel="noopener">${icono("youtube")} Ver en YouTube</a>` : ""}
        </div>
      </div>`);
  }

  /* ── Render de secciones ───────────────────────────────────────────────── */
  const eventosOrdenados = () => {
    const ev = (D.eventos || []).filter((e) => e && e.titulo);
    const prox = ev.filter((e) => e.fecha >= hoyISO).sort((a, b) => a.fecha.localeCompare(b.fecha));
    const pas = ev.filter((e) => !(e.fecha >= hoyISO)).sort((a, b) => b.fecha.localeCompare(a.fecha));
    return { prox, pas, todos: [...prox, ...pas] };
  };
  function filaEvento(ev, i) {
    const f = fecha(ev.fecha), [tipo, etiqueta] = tipoEvento(ev.titulo), proximo = ev.fecha >= hoyISO;
    return `<button type="button" class="evento" data-evento="${i}">
      <div class="evento__fecha">${ev.flyer ? `<img src="${esc(ev.flyer)}" alt="" loading="lazy">` : ""}<div><span class="num">${f.dia}</span><small>${f.mes} ${f.anio ? String(f.anio).slice(2) : ""}</small></div></div>
      <div class="evento__cuerpo">
        <span class="tipo ${proximo ? "tipo--proximo" : "tipo--" + tipo}">${proximo ? "Próximo" : etiqueta}</span>
        <h3>${esc(tituloCorto(ev.titulo))}</h3>
        <div class="evento__meta"><span>${icono("calendario")} ${f.larga}</span>${ev.lugar || ev.modalidad ? `<span>${icono("lugar")} ${esc(ev.modalidad || ev.lugar)}</span>` : ""}</div>
      </div>
      <div class="evento__ir">${icono("derecha")}</div>
    </button>`;
  }
  function render() {
    const { prox, pas, todos } = eventosOrdenados();

    // Inicio: agenda (próximos; si no hay, los más recientes)
    $$("[data-render='agenda']").forEach((el) => {
      const lista = (prox.length ? prox : pas).slice(0, 4);
      $$("[data-agenda-titulo]").forEach((t) => (t.textContent = prox.length ? "Próximos eventos" : "Eventos recientes"));
      el.innerHTML = lista.length ? lista.map((ev) => filaEvento(ev, todos.indexOf(ev))).join("") : `<div class="vacio">Pronto anunciaremos nuevos eventos.</div>`;
      $$("[data-evento]", el).forEach((b) => b.addEventListener("click", () => detalleEvento(todos[b.dataset.evento])));
    });

    // Pie: tres eventos recientes
    $$("[data-render='pie-eventos']").forEach((el) => {
      el.innerHTML = todos.slice(0, 3).map((ev, i) => `<li><a href="mockup_eventos.html#ev-${i}">${esc(tituloCorto(ev.titulo))}</a><br><small style="color:var(--laton)">${fecha(ev.fecha).larga}</small></li>`).join("");
    });

    // Comunicados (abren el flyer en grande)
    $$("[data-render='comunicados']").forEach((el) => {
      el.innerHTML = (D.comunicados || []).map((c, i) => `<button type="button" class="cartel cartel--fila" data-comunicado="${i}">
        <div class="cartel__img"><img src="${esc(c.imagen)}" alt="${esc(c.titulo)}" loading="lazy"></div>
        <div class="cartel__cuerpo"><span class="tipo tipo--presentacion">${esc(c.fecha)}</span><h3>${esc(c.titulo)}</h3>
        <span class="enlace">Ver ${icono("flecha")}</span></div></button>`).join("");
      $$("[data-comunicado]", el).forEach((b) => b.addEventListener("click", () => {
        const c = D.comunicados[b.dataset.comunicado];
        ventana(`<div class="ventana__img"><img src="${esc(c.imagen)}" alt="${esc(c.titulo)}"></div><div class="ventana__cuerpo"><h3 id="ventana-titulo">${esc(c.titulo)}</h3><p>${esc(c.fecha)}</p></div>`);
      }));
    });

    // Página de eventos: destacado + rejilla filtrable
    $$("[data-render='eventos']").forEach((el) => renderEventos(el, prox, pas, todos));

    // Consejo directivo
    $$("[data-render='consejo']").forEach((el) => {
      el.innerHTML = (D.consejo || []).map((p) => {
        const inicial = (p.nombre || "?").split(" ").map((x) => x[0]).slice(0, 2).join("");
        return `<article class="persona">
          <div class="persona__foto">${p.foto ? `<img src="${esc(p.foto)}" alt="${esc(p.nombre)}" loading="lazy">` : `<div class="persona__inicial">${esc(inicial)}</div>`}</div>
          <div class="persona__datos"><div class="persona__cargo">${esc(p.cargo)}</div><div class="persona__nombre">${esc(p.nombre)}</div></div>
        </article>`;
      }).join("");
      el.setAttribute("data-escalonar", "");
    });
    $$("[data-render='consejo-periodo']").forEach((el) => (el.textContent = D.consejo_periodo || ""));

    // Expresidentes (línea de tiempo) y socios honorarios
    $$("[data-render='expresidentes']").forEach((el) => {
      const actual = (D.consejo || []).find((p) => /^president/i.test(p.cargo || ""));
      el.innerHTML = (D.expresidentes || []).map(([a, n]) => `<div class="hito"><div class="hito__anios num">${esc(a)}</div><div class="hito__nombre">${esc(n)}</div></div>`).join("")
        + (actual ? `<div class="hito hito--actual"><div class="hito__anios num">${esc(D.consejo_periodo || "")} · actual</div><div class="hito__nombre">${esc(actual.nombre)}</div></div>` : "");
      requestAnimationFrame(() => (el.scrollLeft = el.scrollWidth));   // empieza en lo más reciente
    });
    $$("[data-render='honorarios']").forEach((el) => (el.innerHTML = (D.honorarios || []).map((n) => `<li>${esc(n)}</li>`).join("")));

    // Aliadas: solo las que la plataforma tenga como «aliada»
    $$("[data-render='aliadas']").forEach((el) => {
      const a = D.aliadas || [];
      el.closest("[data-seccion-aliadas]")?.toggleAttribute("hidden", !a.length);
      el.innerHTML = a.map((x) => x.web ? `<a class="aliada" href="${esc(x.web)}" target="_blank" rel="noopener">${esc(x.siglas || x.nombre)}</a>` : `<span class="aliada">${esc(x.siglas || x.nombre)}</span>`).join("");
    });

    // Cifras: años desde la fundación y socios activos (solo con la plataforma)
    const anios = new Date().getFullYear() - Number(String(D.fundacion || "1979").slice(0, 4));
    $$("[data-anios]").forEach((el) => { el.dataset.contar = anios; if (!el.dataset.contado) el.textContent = anios; });
    const socios = D.cifras?.socios_activos;
    $$("[data-cifra-socios]").forEach((el) => {
      if (!socios) return;
      el.hidden = false; const v = $("[data-contar]", el); if (v) { v.dataset.contar = socios; delete v.dataset.contado; contar(v); }
      $$("[data-cifra-alternativa]").forEach((x) => (x.hidden = true));
    });

    // Contacto (lo que el equipo cambie en la plataforma)
    const c = D.contacto || {};
    $$("[data-c]").forEach((el) => {
      const k = el.dataset.c; if (!c[k]) return;
      if (el.dataset.cHref === "mailto") el.href = "mailto:" + c[k];
      else if (el.dataset.cHref === "url") el.href = c[k];
      if (el.dataset.cTexto !== "no") el.textContent = c[k];
    });

    // Cuotas en Trámites
    actualizarPrecio();
    aparecer();
  }

  function renderEventos(el, prox, pas, todos) {
    const dest = $("[data-destacado]"), rejilla = $("[data-rejilla-eventos]", el) || el;
    const principal = prox[0] || pas[0];
    if (dest && principal) {
      const f = fecha(principal.fecha), proximo = principal.fecha >= hoyISO;
      dest.innerHTML = `<div class="destacado__img">${principal.flyer ? `<img src="${esc(principal.flyer)}" alt="">` : ""}</div>
        <div class="destacado__cuerpo">
          <span class="etiqueta">${proximo ? "Próximo evento" : "Último evento"}</span>
          <h3>${esc(principal.titulo)}</h3>
          <div class="evento__meta"><span>${icono("calendario")} ${f.larga}</span>${principal.lugar ? `<span>${icono("lugar")} ${esc(principal.lugar)}</span>` : ""}</div>
          <div><button type="button" class="btn btn--primario" data-evento-dest>${proximo && principal.registro ? "Ver e inscribirme" : "Ver detalle"} ${icono("flecha", "icono--flecha")}</button></div>
        </div>`;
      $("[data-evento-dest]", dest).addEventListener("click", () => detalleEvento(principal));
    }
    const resto = todos.filter((e) => e !== principal);
    let filtro = "todos", texto = "";
    const pintar = () => {
      const lista = resto.filter((e) => (filtro === "todos" || tipoEvento(e.titulo)[0] === filtro || (filtro === "proximos" && e.fecha >= hoyISO))
        && (!texto || e.titulo.toLowerCase().includes(texto)));
      rejilla.innerHTML = lista.length ? lista.map((ev) => {
        const i = todos.indexOf(ev), f = fecha(ev.fecha), [tipo, etiqueta] = tipoEvento(ev.titulo);
        return `<button type="button" class="cartel" id="ev-${i}" data-evento="${i}">
          <div class="cartel__img">${ev.flyer ? `<img src="${esc(ev.flyer)}" alt="" loading="lazy">` : ""}</div>
          <div class="cartel__cuerpo"><span class="tipo tipo--${tipo}">${etiqueta}</span><h3>${esc(tituloCorto(ev.titulo))}</h3>
          <div class="evento__meta"><span>${icono("calendario")} ${f.larga}</span></div></div></button>`;
      }).join("") : `<div class="vacio" style="grid-column:1/-1">No hay eventos con ese filtro.</div>`;
      $$("[data-evento]", rejilla).forEach((b) => b.addEventListener("click", () => detalleEvento(todos[b.dataset.evento])));
      rejilla.setAttribute("data-escalonar", ""); aparecer(rejilla);
      const cuenta = $("[data-cuenta-eventos]"); if (cuenta) cuenta.textContent = `${lista.length} evento${lista.length === 1 ? "" : "s"}`;
    };
    $$("[data-filtro]").forEach((b) => b.onclick = () => {
      filtro = b.dataset.filtro; $$("[data-filtro]").forEach((x) => x.setAttribute("aria-pressed", String(x === b))); pintar();
    });
    const buscar = $("[data-buscar-eventos]");
    if (buscar) buscar.oninput = () => { texto = buscar.value.trim().toLowerCase(); pintar(); };
    pintar();
    const ancla = location.hash.match(/^#ev-(\d+)$/);
    if (ancla && todos[ancla[1]]) setTimeout(() => detalleEvento(todos[ancla[1]]), 300);
  }

  /* ── Trámites: Lima / Provincia ────────────────────────────────────────── */
  let sede = "lima";
  function actualizarPrecio() {
    const caja = $("[data-precio]"); if (!caja) return;
    const monto = (D.cuotas || {})[sede];
    const cifra = $("[data-precio-monto]", caja);
    cifra.style.opacity = 0;
    setTimeout(() => { cifra.innerHTML = `<small>S/</small>${monto ? Number(monto).toLocaleString("es-PE") : "—"}`; cifra.style.opacity = 1; }, 150);
    $("[data-precio-sede]", caja).textContent = sede === "lima" ? "Asociado ordinario · Lima" : "Asociado de filial · provincias";
    $$("[data-solo-lima]").forEach((x) => (x.hidden = sede !== "lima"));
    const guia = (D.documentos || []).find((d) => d.sede === sede);
    $$("[data-guia]").forEach((a) => { if (guia) { a.href = guia.url; $(".guia-nombre", a) && ($(".guia-nombre", a).textContent = guia.nombre); } });
  }
  function selectorSede() {
    $$("[data-sede]").forEach((b) => b.addEventListener("click", () => {
      sede = b.dataset.sede; $$("[data-sede]").forEach((x) => x.setAttribute("aria-pressed", String(x === b))); actualizarPrecio();
    }));
  }

  /* ── Pestañas (portal de ejemplo) ──────────────────────────────────────── */
  function pestanas() {
    $$("[role='tablist']").forEach((lista) => {
      const tabs = $$("[role='tab']", lista);
      const activar = (t) => {
        tabs.forEach((x) => { const sel = x === t; x.setAttribute("aria-selected", String(sel)); x.tabIndex = sel ? 0 : -1; $("#" + x.getAttribute("aria-controls")).hidden = !sel; });
      };
      tabs.forEach((t, i) => {
        t.addEventListener("click", () => activar(t));
        t.addEventListener("keydown", (e) => {
          const d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
          if (d) { const n = tabs[(i + d + tabs.length) % tabs.length]; n.focus(); activar(n); }
        });
      });
    });
    $$("[data-ir-pestana]").forEach((b) => b.addEventListener("click", () => $(`[role='tab'][aria-controls='${b.dataset.irPestana}']`)?.click()));
    $$("[data-demo-entrar]").forEach((f) => f.addEventListener("submit", (e) => {
      e.preventDefault(); $("[data-demo-login]").hidden = true; $("[data-demo-portal]").hidden = false; $("[data-demo-portal]").scrollIntoView({ behavior: "smooth", block: "start" });
    }));
    $$("[data-demo-salir]").forEach((b) => b.addEventListener("click", () => { $("[data-demo-login]").hidden = false; $("[data-demo-portal]").hidden = true; }));
  }

  /* ── Línea de tiempo: botones ──────────────────────────────────────────── */
  function lineaTiempo() {
    $$("[data-mover]").forEach((b) => b.addEventListener("click", () => {
      const pista = $(b.dataset.objetivo); pista?.scrollBy({ left: Number(b.dataset.mover) * 450, behavior: "smooth" });
    }));
  }

  /* ── Formularios → plataforma ──────────────────────────────────────────── */
  function formularios() {
    $$("form[data-api]").forEach((form) => form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const estado = $(".estado-form", form), boton = $("button[type='submit']", form), textoBoton = boton.innerHTML;
      const datos = Object.fromEntries(new FormData(form).entries());
      datos.acepta = !!form.querySelector("[name='acepta']")?.checked;
      estado.className = "estado-form"; boton.disabled = true; boton.innerHTML = "Enviando…";
      try {
        const ctrl = new AbortController(); const t = setTimeout(() => ctrl.abort(), 15000);
        const r = await fetch(`${D.api}/${form.dataset.api}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(datos), signal: ctrl.signal });
        clearTimeout(t);
        const j = await r.json().catch(() => ({}));
        if (!r.ok || j.ok === false) throw new Error(j.error || "No pudimos enviar tu mensaje.");
        form.reset();
        estado.textContent = form.dataset.ok || "¡Listo! Recibimos tu mensaje.";
        estado.classList.add("ok");
      } catch (err) {
        estado.textContent = (err.name === "AbortError" || err instanceof TypeError)
          ? `No pudimos conectar. Escríbenos a ${D.contacto?.email || "administracion@ipidet.org"} o por WhatsApp.`
          : err.message;
        estado.classList.add("error");
      } finally { boton.disabled = false; boton.innerHTML = textoBoton; }
    }));
  }

  /* ── Datos de la plataforma ────────────────────────────────────────────── */
  async function cargarPlataforma() {
    if (!D.api || location.protocol === "file:") return;   // abierto desde el disco: se queda con el respaldo
    try {
      const ctrl = new AbortController(); const t = setTimeout(() => ctrl.abort(), 8000);
      const r = await fetch(`${D.api}/sitio`, { signal: ctrl.signal }); clearTimeout(t);
      if (!r.ok) return;
      const s = await r.json();
      if (s.consejo?.length) {
        const fotos = Object.fromEntries((D.consejo || []).map((p) => [p.nombre.toLowerCase(), p.foto]));
        D.consejo = s.consejo.map((p) => ({ ...p, foto: p.foto || fotos[(p.nombre || "").toLowerCase()] || "" }));
        const desde = s.consejo.map((p) => p.desde).filter(Boolean).sort()[0], hasta = s.consejo.map((p) => p.hasta).filter(Boolean).sort().pop();
        if (desde) D.consejo_periodo = `${desde.slice(0, 4)}${hasta ? " – " + hasta.slice(0, 4) : ""}`;
      }
      if (Array.isArray(s.eventos) && s.eventos.length) D.eventos = s.eventos;
      if (s.contacto) D.contacto = { ...D.contacto, ...Object.fromEntries(Object.entries(s.contacto).filter(([, v]) => v)) };
      if (s.cuotas?.length) {
        const lima = s.cuotas.find((c) => c.tipo === "cuota_anual"), prov = s.cuotas.find((c) => c.tipo === "cuota_provincia");
        D.cuotas = { ...D.cuotas, ...(lima?.precio ? { lima: lima.precio } : {}), ...(prov?.precio ? { provincia: prov.precio } : {}) };
      }
      D.aliadas = s.aliadas || [];
      D.cifras = s.cifras || {};
      render();
    } catch (_) { /* sin plataforma: se queda el contenido de respaldo */ }
  }

  document.addEventListener("DOMContentLoaded", () => {
    $$("[data-anio-actual]").forEach((el) => (el.textContent = new Date().getFullYear()));
    menu(); cabecera(); prepararVentana(); selectorSede(); pestanas(); lineaTiempo(); formularios();
    render(); cargarPlataforma();
  });
})();
