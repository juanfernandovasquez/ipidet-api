/* Sitio público de IPIDET: interacción. Sin dependencias y sin innerHTML con datos (CSP: solo scripts propios). */
(() => {
  "use strict";
  const $ = (s, c = document) => c.querySelector(s);
  const $$ = (s, c = document) => Array.from(c.querySelectorAll(s));
  const quieto = matchMedia("(prefers-reduced-motion: reduce)").matches;

  function menu() {
    const cajon = $(".cajon"), abrir = $(".boton-menu");
    if (!cajon || !abrir) return;
    const poner = (abierto) => {
      cajon.classList.toggle("abierto", abierto);
      abrir.setAttribute("aria-expanded", String(abierto));
      document.body.style.overflow = abierto ? "hidden" : "";
      if (abierto) setTimeout(() => $(".cajon__panel a", cajon)?.focus(), 50);
    };
    abrir.addEventListener("click", () => poner(true));
    $$("[data-cerrar-menu]", cajon).forEach((b) => b.addEventListener("click", () => poner(false)));
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && cajon.classList.contains("abierto")) { poner(false); abrir.focus(); }
    });
  }

  function cabecera() {
    const cab = $(".cabecera"), arriba = $(".arriba");
    const mover = () => {
      cab?.classList.toggle("con-sombra", scrollY > 8);
      arriba?.classList.toggle("visible", scrollY > 700);
    };
    addEventListener("scroll", mover, { passive: true });
    mover();
    arriba?.addEventListener("click", () => scrollTo({ top: 0, behavior: quieto ? "auto" : "smooth" }));
  }

  function contar(el) {
    if (el.dataset.contado) return;
    el.dataset.contado = "1";
    const fin = Number(el.dataset.contar), t0 = performance.now();
    if (quieto || !fin) return;
    const paso = (t) => {
      const p = Math.max(0, Math.min(1, (t - t0) / 1300));
      el.textContent = Math.round(fin * (1 - (1 - p) ** 3)).toLocaleString("es-PE");
      if (p < 1) requestAnimationFrame(paso);
    };
    requestAnimationFrame(paso);
  }

  function aparecer() {
    $$("[data-escalonar]").forEach((g) => [...g.children].forEach((h, i) => {
      h.classList.add("aparece");
      h.style.setProperty("--retardo", `${Math.min(i, 8) * 70}ms`);
    }));
    const todos = $$(".aparece");
    if (!("IntersectionObserver" in window)) { todos.forEach((e) => e.classList.add("visto")); return; }
    const ojo = new IntersectionObserver((es) => es.forEach((e) => {
      if (!e.isIntersecting) return;
      e.target.classList.add("visto");
      ojo.unobserve(e.target);
      $$("[data-contar]", e.target).forEach(contar);
    }), { threshold: 0.01 });
    todos.forEach((e) => ojo.observe(e));
  }

  function filtros() {
    const lista = $("[data-filtrable]");
    if (!lista) return;
    let filtro = "todos", texto = "";
    const aplicar = () => {
      let n = 0;
      [...lista.children].forEach((x) => {
        const ok = (filtro === "todos" || (x.dataset.clases || "").split(" ").includes(filtro))
          && (!texto || (x.dataset.texto || "").includes(texto));
        x.hidden = !ok;
        if (ok) { n++; x.classList.add("visto"); }
      });
      const vacio = $("[data-sin-resultados]");
      if (vacio) vacio.hidden = n > 0;
    };
    $$("[data-filtro]").forEach((b) => b.addEventListener("click", () => {
      filtro = b.dataset.filtro;
      $$("[data-filtro]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      aplicar();
    }));
    $("[data-buscar]")?.addEventListener("input", (e) => { texto = e.target.value.trim().toLowerCase(); aplicar(); });
  }

  function sede() {
    $$("[data-sede]").forEach((b) => b.addEventListener("click", () => {
      $$("[data-sede]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      $$("[data-de-sede]").forEach((x) => { x.hidden = x.dataset.deSede !== b.dataset.sede; });
    }));
  }

  function pestanas() {
    const tabs = $$("[role='tab']");
    if (!tabs.length) return;
    const activar = (t, anotar = true) => {
      tabs.forEach((x) => {
        const sel = x === t;
        x.setAttribute("aria-selected", String(sel));
        x.tabIndex = sel ? 0 : -1;
        const panel = document.getElementById(x.getAttribute("aria-controls"));
        if (panel) panel.hidden = !sel;
      });
      if (anotar) history.replaceState(null, "", `#${t.id.slice(2)}`);
    };
    tabs.forEach((t, i) => {
      t.addEventListener("click", () => activar(t));
      t.addEventListener("keydown", (e) => {
        const d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
        if (d) { const n = tabs[(i + d + tabs.length) % tabs.length]; n.focus(); activar(n); }
      });
    });
    $$("[data-ir-pestana]").forEach((b) => b.addEventListener("click", () => {
      const t = tabs.find((x) => x.getAttribute("aria-controls") === b.dataset.irPestana);
      if (t) activar(t);
    }));
    const inicial = tabs.find((x) => `#${x.id.slice(2)}` === location.hash);
    if (inicial) activar(inicial, false);
  }

  function linea() {
    const pista = $("#pista");
    if (!pista) return;
    pista.scrollLeft = pista.scrollWidth;
    $$("[data-mover]").forEach((b) => b.addEventListener("click", () => {
      pista.scrollBy({ left: Number(b.dataset.mover) * 450, behavior: quieto ? "auto" : "smooth" });
    }));
  }

  /* ── Envíos ───────────────────────────────────────────────────────────── */
  function decir(zona, texto, tipo) {
    if (!zona) return;
    zona.textContent = texto;
    zona.classList.remove("ok", "error");
    if (tipo) zona.classList.add(tipo);
  }
  // Solo se navega dentro del sitio, aunque una respuesta diga otra cosa.
  const irA = (ruta) => {
    if (!/^\/web(\/|\?|#|$)/.test(ruta || "")) return false;
    location.assign(ruta);
    return true;
  };

  async function enviar(url, cuerpo) {
    const opciones = { method: "POST", credentials: "same-origin" };
    if (cuerpo instanceof FormData) opciones.body = cuerpo;
    else { opciones.headers = { "Content-Type": "application/json" }; opciones.body = JSON.stringify(cuerpo || {}); }
    let r;
    try { r = await fetch(url, opciones); } catch (_) { throw new Error("No pudimos conectar. Revisa tu conexión e inténtalo de nuevo."); }
    const j = await r.json().catch(() => ({}));
    if (!r.ok || j.ok === false) throw new Error(j.error || "No pudimos completar el pedido. Vuelve a intentarlo.");
    return j;
  }

  function formularios() {
    $$("form[data-json], form[data-multipart]").forEach((form) => form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const zona = $(".estado-form", form), boton = $("button[type='submit']", form);
      if (!form.checkValidity()) {
        const mal = $(":invalid", form);
        mal?.focus();
        decir(zona, "Revisa los campos obligatorios.", "error");
        return;
      }
      let cuerpo;
      if (form.dataset.multipart) cuerpo = new FormData(form);
      else {
        cuerpo = Object.fromEntries(new FormData(form).entries());
        $$("input[type='checkbox']", form).forEach((c) => { cuerpo[c.name] = c.checked; });
      }
      decir(zona, "");
      boton.disabled = true;
      try {
        const j = await enviar(form.dataset.multipart || form.dataset.json, cuerpo);
        if (form.dataset.paso === "correo") { pedirCodigo(j.reto, cuerpo.email); return; }
        if (!("sinIr" in form.dataset) && j.ir && irA(j.ir)) return;
        if ("limpiar" in form.dataset) form.reset();
        decir(zona, form.dataset.ok || j.mensaje || "Listo.", "ok");
      } catch (err) {
        decir(zona, err.message, "error");
      } finally {
        boton.disabled = false;
      }
    }));
  }

  function pedirCodigo(reto, correo) {
    const uno = $("[data-paso='correo']"), dos = $("[data-paso='codigo']");
    uno.hidden = true;
    dos.hidden = false;
    dos.elements.reto.value = reto;
    dos.elements.codigo.value = "";
    $("[data-correo]", dos).textContent = correo;
    decir($(".estado-form", dos), "");
    dos.elements.codigo.focus();
  }

  function acceso() {
    $("[data-otro-correo]")?.addEventListener("click", () => {
      $("[data-paso='codigo']").hidden = true;
      const uno = $("[data-paso='correo']");
      uno.hidden = false;
      uno.elements.email.focus();
    });
  }

  function botones() {
    $$("button[data-post]").forEach((b) => {
      let seguro = !b.dataset.confirmar;
      const original = b.textContent;
      b.addEventListener("click", async () => {
        if (!seguro) {
          seguro = true;
          b.textContent = b.dataset.confirmar;
          setTimeout(() => { seguro = false; b.textContent = original; }, 5000);
          return;
        }
        let zona = null;
        for (let caja = b.parentElement; caja && !zona && caja.id !== "contenido"; caja = caja.parentElement) zona = $(".estado-form", caja);
        const cuerpo = b.dataset.payment ? { payment_id: b.dataset.payment, cuota_numero: b.dataset.cuota } : {};
        b.disabled = true;
        try {
          const j = await enviar(b.dataset.post, cuerpo);
          if (j.ir) {
            // Una acción que termina en la misma página la vuelve a cargar.
            if (j.ir === location.pathname) location.reload(); else irA(j.ir);
            return;
          }
          decir(zona, j.mensaje || "Listo.", "ok");
        } catch (err) {
          decir(zona, err.message, "error");
        } finally {
          b.disabled = false;
        }
      });
    });
  }

  function comprobante() {
    const sel = $("[data-comprobante]");
    sel?.addEventListener("change", () => {
      $$("[data-solo]", sel.form).forEach((x) => { x.hidden = x.dataset.solo !== sel.value; });
    });
  }

  [menu, cabecera, aparecer, filtros, sede, pestanas, linea, formularios, acceso, botones, comprobante].forEach((f) => f());
})();
