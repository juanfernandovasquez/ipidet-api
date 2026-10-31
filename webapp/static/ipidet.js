/* Componentes compartidos de la plataforma: avisos, diálogos y búsqueda global.
   Debe cargarse antes de Alpine. API para las plantillas:
     avisar(mensaje, 'ok' | 'error')
     await confirmar(mensaje)          → true / false
     await solicitar(mensaje, valor)   → texto o null
     <form data-confirm="¿Seguro?">    → pide confirmación antes de enviar
*/
(function () {
  const PELIGRO = /eliminar|anular|descartar|inhabilitar|desactivar|irreversible|cancelar este/i;

  document.addEventListener('alpine:init', () => {
    Alpine.store('ui', { menu: false });

    Alpine.store('avisos', {
      lista: [],
      sig: 0,
      agregar(mensaje, tipo) {
        const id = ++this.sig;
        this.lista.push({ id, mensaje, tipo: tipo || 'ok' });
        setTimeout(() => this.quitar(id), tipo === 'error' ? 8000 : 3500);
      },
      quitar(id) { this.lista = this.lista.filter(a => a.id !== id); },
    });

    Alpine.store('dialogo', {
      abierto: false, mensaje: '', peligro: false, pideTexto: false, valor: '',
      _resolver: null, _foco: null, _rango: null,
      abrir(opciones) {
        this.mensaje = opciones.mensaje;
        this.peligro = !!opciones.peligro;
        this.pideTexto = !!opciones.pideTexto;
        this.valor = opciones.valor || '';
        this._foco = document.activeElement;
        // Un editor de texto pierde el cursor al abrir el diálogo: se guarda para restaurarlo.
        const sel = window.getSelection();
        this._rango = sel && sel.rangeCount ? sel.getRangeAt(0).cloneRange() : null;
        this.abierto = true;
        return new Promise(resolve => { this._resolver = resolve; });
      },
      responder(valor) {
        this.abierto = false;
        if (this._foco && this._foco.focus) this._foco.focus();
        if (this._rango) {
          const sel = window.getSelection();
          sel.removeAllRanges();
          sel.addRange(this._rango);
        }
        const resolver = this._resolver;
        this._resolver = null;
        if (resolver) resolver(valor);
      },
    });

    Alpine.data('buscador', () => ({
      abierto: false, q: '', resultados: [], indice: 0, cargando: false, _t: null, _foco: null,
      init() {
        window.addEventListener('keydown', e => {
          if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
            e.preventDefault();
            this.abrir();
          }
        });
      },
      paginas() {
        return [...document.querySelectorAll('aside .sidebar-link')].map(a => ({
          grupo: 'Ir a', titulo: a.dataset.nombre, detalle: '', url: a.getAttribute('href'),
          icono: a.querySelector('i')?.className || 'fa-solid fa-arrow-right',
        }));
      },
      abrir() {
        this._foco = document.activeElement;
        this.abierto = true;
        this.q = '';
        this.resultados = this.paginas();
        this.indice = 0;
        this.$nextTick(() => this.$refs.q.focus());
      },
      cerrar() {
        this.abierto = false;
        if (this._foco && this._foco.focus) this._foco.focus();
      },
      buscar() {
        clearTimeout(this._t);
        const q = this.q.trim();
        const norm = s => s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
        const paginas = this.paginas().filter(p => norm(p.titulo).includes(norm(q)));
        this.resultados = paginas;
        this.indice = 0;
        if (q.length < 2) { this.cargando = false; return; }
        this.cargando = true;
        this._t = setTimeout(async () => {
          try {
            const r = await fetch('/api/buscar?q=' + encodeURIComponent(q));
            const datos = r.ok ? await r.json() : [];
            if (this.q.trim() === q) this.resultados = [...datos, ...paginas];
          } finally {
            this.cargando = false;
          }
        }, 220);
      },
      mover(paso) {
        const n = this.resultados.length;
        if (!n) return;
        this.indice = (this.indice + paso + n) % n;
        this.$nextTick(() => this.$refs.lista?.querySelector('[data-activo="true"]')?.scrollIntoView({ block: 'nearest' }));
      },
      ir() {
        const r = this.resultados[this.indice];
        if (r) location.href = r.url;
      },
    }));
  });

  // Misma presentación de fechas que el filtro format_fecha del servidor.
  window.fechaPe = valor => {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(valor || '');
    return m ? `${m[3]}/${m[2]}/${m[1]}` : (valor || '—');
  };
  window.avisar = (mensaje, tipo) => Alpine.store('avisos').agregar(mensaje, tipo);
  // alert() bloquea la página y se ve ajeno a la plataforma; todos los usos son errores.
  window.alert = mensaje => window.avisar(String(mensaje), 'error');
  window.confirmar = mensaje => Alpine.store('dialogo').abrir({ mensaje, peligro: PELIGRO.test(mensaje) });
  window.solicitar = (mensaje, valor) => Alpine.store('dialogo').abrir({ mensaje, pideTexto: true, valor });

  // Confirmación declarativa: <form data-confirm="..."> y <button data-confirm="...">
  document.addEventListener('submit', e => {
    const form = e.target;
    if (!form.dataset || !form.dataset.confirm) return;
    if (form.dataset.confirmado) { delete form.dataset.confirmado; return; }
    e.preventDefault();
    e.stopImmediatePropagation();
    const boton = e.submitter;
    window.confirmar(form.dataset.confirm).then(ok => {
      if (!ok) return;
      form.dataset.confirmado = '1';
      form.requestSubmit(boton || undefined);
    });
  }, true);

  document.addEventListener('DOMContentLoaded', () => {
    // Accesibilidad: los botones de solo icono anuncian su título.
    document.querySelectorAll('button[title]:not([aria-label]), a[title]:not([aria-label])')
      .forEach(el => el.setAttribute('aria-label', el.getAttribute('title')));
    // Mantiene visible la sección activa cuando el menú es más alto que la pantalla.
    const activo = document.querySelector('aside .sidebar-link.active');
    const nav = activo && activo.closest('nav');
    if (nav) nav.scrollTop = Math.max(0, activo.offsetTop - nav.clientHeight / 2);
  });

  document.addEventListener('alpine:initialized', () => {
    const flash = document.body.dataset.flash;
    if (flash) window.avisar(flash);
  });
})();
