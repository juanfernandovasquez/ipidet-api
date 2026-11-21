"""Genera los 6 mockups del sitio público con la misma cabecera, menú, pie e iconos.

    python mockup_assets/generar_mockups.py

Estilos en sitio.css, interacción en sitio.js y contenido de respaldo en datos.js (todo en esta
carpeta). Las páginas quedan en la raíz del proyecto (mockup_*.html), como estaban.
"""
import hashlib
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def _v(nombre: str) -> str:
    """Versión del archivo (hash corto) para que el navegador no use una copia vieja."""
    return hashlib.md5((RAIZ / "mockup_assets" / nombre).read_bytes()).hexdigest()[:8]

PAGINAS = [  # (clave, archivo, texto del menú)
    ("inicio", "mockup_ipidet_redesign.html", "Inicio"),
    ("nosotros", "mockup_nosotros.html", "Nosotros"),
    ("eventos", "mockup_eventos.html", "Eventos"),
    ("tramites", "mockup_tramites.html", "Trámites"),
    ("asociados", "mockup_asociados.html", "Asociados"),
    ("contacto", "mockup_contacto.html", "Contacto"),
]
URL = {k: a for k, a, _ in PAGINAS}

# Iconos de trazo (24×24): un solo sprite, sin fuentes de iconos externas.
ICONOS = {
    "flecha": '<path d="M5 12h14M13 6l6 6-6 6"/>',
    "derecha": '<path d="m9 18 6-6-6-6"/>',
    "izquierda": '<path d="m15 18-6-6 6-6"/>',
    "abajo": '<path d="m6 9 6 6 6-6"/>',
    "subir": '<path d="m18 15-6-6-6 6"/>',
    "menu": '<path d="M4 7h16M4 12h16M4 17h10"/>',
    "cerrar": '<path d="M18 6 6 18M6 6l12 12"/>',
    "calendario": '<rect x="3" y="4.5" width="18" height="17" rx="2.5"/><path d="M16 2.5v4M8 2.5v4M3 10h18"/>',
    "lugar": '<path d="M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 0 1 16 0z"/><circle cx="12" cy="10" r="3"/>',
    "correo": '<rect x="2" y="4" width="20" height="16" rx="2.5"/><path d="m22 7-10 6L2 7"/>',
    "telefono": '<path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.4 1.9.7 2.8a2 2 0 0 1-.5 2.1L8.1 9.9a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.9.6 2.8.7a2 2 0 0 1 1.7 2z"/>',
    "whatsapp": '<path d="M21 11.5a8.4 8.4 0 0 1-12.6 7.3L3 21l2.2-5.2A8.4 8.4 0 1 1 21 11.5z"/><path d="M9 9.5c.3 1.6 1.9 3.3 3.6 4 .5.2 1 0 1.3-.4l.4-.5 1.6.8c0 .9-.6 1.6-1.6 1.6-2.8 0-5.9-3-5.9-5.9 0-1 .7-1.6 1.6-1.6l.8 1.6-.5.4c-.3.3-.4.6-.3 1z"/>',
    "facebook": '<path d="M18 2h-3a5 5 0 0 0-5 5v3H7v4h3v8h4v-8h3l1-4h-4V7a1 1 0 0 1 1-1h3z"/>',
    "linkedin": '<path d="M16 8a6 6 0 0 1 6 6v7h-4v-7a2 2 0 0 0-4 0v7h-4v-7a6 6 0 0 1 6-6z"/><rect x="2" y="9" width="4" height="12"/><circle cx="4" cy="4" r="2"/>',
    "youtube": '<path d="M2.5 17a24 24 0 0 1 0-10 2 2 0 0 1 1.4-1.4 49.6 49.6 0 0 1 16.2 0A2 2 0 0 1 21.5 7a24 24 0 0 1 0 10 2 2 0 0 1-1.4 1.4 49.6 49.6 0 0 1-16.2 0A2 2 0 0 1 2.5 17"/><path d="m10 15 5-3-5-3z"/>',
    "play": '<path d="m7 4 13 8-13 8z"/>',
    "archivo": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M16 13H8M16 17H8"/>',
    "descargar": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>',
    "buscar": '<circle cx="11" cy="11" r="7.5"/><path d="m21 21-4.3-4.3"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "personas": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8"/>',
    "micro": '<rect x="9" y="2" width="6" height="12" rx="3"/><path d="M19 10v1a7 7 0 0 1-14 0v-1M12 18v4M8 22h8"/>',
    "libro": '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V2H6.5A2.5 2.5 0 0 0 4 4.5z"/><path d="M4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5"/>',
    "externo": '<path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
    "premio": '<circle cx="12" cy="8" r="6"/><path d="M15.5 13 17 22l-5-3-5 3 1.5-9"/>',
    "escudo": '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
    "diana": '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>',
    "edificio": '<path d="M3 22h18M6 18v-7M10 18v-7M14 18v-7M18 18v-7M12 2l9 5H3z"/>',
    "candado": '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    "maletin": '<rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/>',
    "enviar": '<path d="m22 2-7 20-4-9-9-4z"/><path d="M22 2 11 13"/>',
    "usuario": '<circle cx="12" cy="8" r="4"/><path d="M4 21v-1a6 6 0 0 1 6-6h4a6 6 0 0 1 6 6v1"/>',
    "recibo": '<path d="M4 2v20l3-2 3 2 3-2 3 2 3-2 3 2V2l-3 2-3-2-3 2-3-2-3 2z"/><path d="M8 8h8M8 12h8M8 16h5"/>',
    "tarjeta": '<rect x="2" y="5" width="20" height="14" rx="2"/><path d="M2 10h20"/>',
    "salir": '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>',
    "estrella": '<path d="m12 3 2.6 5.6 6.4.7-4.6 4.4 1.1 6.3L12 17l-5.5 3 1.1-6.3L3 9.3l6.4-.7z"/>',
}
SPRITE = '<svg width="0" height="0" style="position:absolute" aria-hidden="true">' + "".join(
    f'<symbol id="i-{n}" viewBox="0 0 24 24">{p}</symbol>' for n, p in ICONOS.items()) + "</svg>"


def i(nombre: str, clase: str = "") -> str:
    return f'<svg class="icono {clase}" aria-hidden="true"><use href="#i-{nombre}"></use></svg>'


def cabecera(activa: str) -> str:
    def enlaces(clase=""):
        return "".join(
            f'<a href="{a}"{" class=" + chr(34) + clase + chr(34) if clase else ""}{" aria-current=" + chr(34) + "page" + chr(34) if k == activa else ""}>{t}'
            f'{i("derecha") if clase else ""}</a>' for k, a, t in PAGINAS)
    return f"""
  <a class="saltar" href="#contenido">Saltar al contenido</a>
  <header class="cabecera">
    <div class="contenedor cabecera__fila">
      <a class="cabecera__logo" href="{URL['inicio']}" aria-label="IPIDET, inicio"><img src="mockup_assets/logo.png" alt="IPIDET" width="120" height="42"></a>
      <nav class="menu" aria-label="Principal">{enlaces()}</nav>
      <div class="cabecera__acciones">
        <a class="btn btn--primario btn--chico" href="{URL['tramites']}">Asóciate {i("flecha", "icono--flecha")}</a>
        <button class="boton-menu" type="button" aria-label="Abrir menú" aria-expanded="false" aria-controls="cajon">{i("menu")}</button>
      </div>
    </div>
  </header>
  <div class="cajon" id="cajon">
    <div class="cajon__fondo" data-cerrar-menu></div>
    <nav class="cajon__panel" aria-label="Menú móvil">
      <div class="cajon__cabeza"><img src="mockup_assets/logo.png" alt="IPIDET" style="height:36px;width:auto">
        <button class="redondo" type="button" aria-label="Cerrar menú" data-cerrar-menu>{i("cerrar")}</button></div>
      {enlaces("item")}
      <a class="btn btn--primario" href="{URL['tramites']}">Asóciate {i("flecha", "icono--flecha")}</a>
      <a class="btn btn--borde" href="https://wa.me/51922474067" target="_blank" rel="noopener">{i("whatsapp")} Escríbenos</a>
    </nav>
  </div>"""


def pie() -> str:
    return f"""
  <footer class="pie">
    <div class="contenedor pie__rejilla">
      <div>
        <div class="pie__logo"><img src="mockup_assets/logo-blanco.png" alt="IPIDET" width="126" height="44"></div>
        <p>Asociación civil de carácter cultural y científico, sin fines de lucro, que reúne a profesionales especializados en tributación desde 1979.</p>
        <div class="redes">
          <a data-c="facebook" data-c-href="url" data-c-texto="no" href="https://www.facebook.com/Ipidet/" target="_blank" rel="noopener" aria-label="Facebook">{i("facebook")}</a>
          <a data-c="linkedin" data-c-href="url" data-c-texto="no" href="https://www.linkedin.com/in/ipidet-org-15186414a/" target="_blank" rel="noopener" aria-label="LinkedIn">{i("linkedin")}</a>
          <a data-c="youtube" data-c-href="url" data-c-texto="no" href="https://www.youtube.com/channel/UCA8uw2eF3QWs5HiMPeUbz9g" target="_blank" rel="noopener" aria-label="YouTube">{i("youtube")}</a>
        </div>
      </div>
      <div><h4>Navega</h4><ul>{"".join(f'<li><a href="{a}">{t}</a></li>' for _, a, t in PAGINAS)}</ul></div>
      <div><h4>Eventos recientes</h4><ul data-render="pie-eventos"></ul></div>
      <div><h4>Contacto</h4><ul>
        <li class="pie__dato">{i("lugar")}<a href="https://www.google.com/maps/search/?api=1&amp;query=Calle+German+Schreiber+276+San+Isidro+Lima" target="_blank" rel="noopener">Calle German Schreiber 276, Lima 27</a></li>
        <li class="pie__dato">{i("telefono")}<span><a href="tel:+5114226048">(01) 422-6048</a> · <a href="tel:+5114217698">(01) 421-7698</a></span></li>
        <li class="pie__dato">{i("whatsapp")}<a href="https://wa.me/51922474067" target="_blank" rel="noopener">+51 922 474 067</a></li>
        <li class="pie__dato">{i("correo")}<a data-c="email" data-c-href="mailto" href="mailto:administracion@ipidet.org">administracion@ipidet.org</a></li>
      </ul></div>
    </div>
    <div class="contenedor pie__base"><span>© <span data-anio-actual>2026</span> IPIDET — Instituto Peruano de Investigación y Desarrollo Tributario</span><span>Desde el 23 de enero de 1979</span></div>
  </footer>"""


def pagina(clave: str, titulo: str, descripcion: str, cuerpo: str, wa: bool = True) -> None:
    archivo = URL[clave]
    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{titulo}</title>
  <meta name="description" content="{descripcion}">
  <meta name="theme-color" content="#0a3020">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,600;0,700;1,600&amp;family=Source+Sans+3:wght@400;500;600;700&amp;display=swap">
  <link rel="stylesheet" href="mockup_assets/sitio.css?v={_v('sitio.css')}">
</head>
<body>
  {SPRITE}{cabecera(clave)}
  <main id="contenido">
{cuerpo}
  </main>{pie()}
  <div id="ventana" class="ventana" role="dialog" aria-modal="true" aria-labelledby="ventana-titulo" aria-hidden="true">
    <div class="ventana__fondo" data-cerrar-ventana></div>
    <div class="ventana__caja"><button class="ventana__cerrar" type="button" aria-label="Cerrar" data-cerrar-ventana>{i("cerrar")}</button><div class="ventana__contenido"></div></div>
  </div>
  <button class="arriba" type="button" aria-label="Volver arriba">{i("subir")}</button>
  {f'<a class="wa-flotante" href="https://wa.me/51922474067" target="_blank" rel="noopener" aria-label="Escríbenos por WhatsApp">{i("whatsapp")}<span>¿Consultas?</span></a>' if wa else ""}
  <script src="mockup_assets/datos.js?v={_v('datos.js')}"></script>
  <script src="mockup_assets/sitio.js?v={_v('sitio.js')}"></script>
</body>
</html>
"""
    (RAIZ / archivo).write_text(html, encoding="utf-8", newline="\n")
    print("escrito", archivo)


def heroe_interior(miga: str, titulo: str, texto: str, acciones: str = "", foto: str = "") -> str:
    return f"""
    <section class="heroe heroe--interior">
      {f'<div class="heroe__foto" style="background-image:url({foto})"></div>' if foto else ""}
      <div class="contenedor heroe__contenido entrada">
        <div class="miga"><a href="{URL['inicio']}">Inicio</a> {i("derecha")} <span>{miga}</span></div>
        <h1>{titulo}</h1>
        <p class="heroe__texto">{texto}</p>
        {f'<div class="heroe__acciones">{acciones}</div>' if acciones else ""}
      </div>
      <div class="heroe__corte"></div>
    </section>"""


BOLETIN = f"""
    <section class="boletin seccion">
      <div class="contenedor boletin__caja">
        <div class="aparece">
          <span class="etiqueta" style="color:var(--laton-claro)">Boletín</span>
          <h2>Entérate primero de los próximos eventos</h2>
          <p>Te escribimos solo cuando hay un evento, un conversatorio o un comunicado importante.</p>
        </div>
        <form class="aparece" data-api="suscribir" data-ok="¡Gracias! Te avisaremos de los próximos eventos." novalidate style="--retardo:.1s">
          <div class="boletin__form">
            <label class="sr" for="boletin-email">Tu correo</label>
            <input id="boletin-email" type="email" name="email" placeholder="tucorreo@ejemplo.com" autocomplete="email" required>
            <input class="trampa" type="text" name="web" tabindex="-1" autocomplete="off" aria-hidden="true">
            <button class="btn btn--laton" type="submit">Suscribirme {i("enviar")}</button>
          </div>
          <p class="estado-form" role="status" style="margin-top:12px"></p>
        </form>
      </div>
    </section>"""


# ═════════════════════════════════════════ INICIO ══════════════════════════════
pagina("inicio", "IPIDET — Instituto Peruano de Investigación y Desarrollo Tributario",
       "Asociación civil que reúne a profesionales especializados en tributación en el Perú desde 1979.", f"""
    <section class="heroe">
      <div class="heroe__foto" style="background-image:url(mockup_assets/evento-21may26.jpg)"></div>
      <div class="heroe__anio" aria-hidden="true">1979</div>
      <div class="contenedor heroe__contenido entrada">
        <div><span class="chip">{i("estrella")} Asociación civil · Lima, Perú · Desde 1979</span></div>
        <h1>Instituto Peruano de <em>Investigación y Desarrollo</em> Tributario</h1>
        <p class="heroe__texto">Reunimos a profesionales especializados en tributación para estudiar, debatir y mejorar el sistema tributario peruano.</p>
        <div class="heroe__acciones">
          <a class="btn btn--laton" href="{URL['tramites']}">Quiero asociarme {i("flecha", "icono--flecha")}</a>
          <a class="btn btn--claro" href="#agenda">{i("calendario")} Ver eventos</a>
        </div>
      </div>
      <div class="heroe__corte"></div>
    </section>

    <div class="cifras">
      <div class="contenedor">
        <div class="cifras__caja aparece">
          <div class="cifra"><div class="cifra__valor num" data-anios>47</div><div class="cifra__texto">años de trayectoria</div></div>
          <div class="cifra" data-cifra-alternativa><div class="cifra__valor num" data-contar="22">22</div><div class="cifra__texto">consejos directivos desde 1981</div></div>
          <div class="cifra" data-cifra-socios hidden><div class="cifra__valor num" data-contar="0">0</div><div class="cifra__texto">asociados activos</div></div>
          <div class="cifra"><div class="cifra__valor num"><small>N.º</small>6</div><div class="cifra__texto">ediciones de la Revista institucional</div></div>
          <div class="cifra"><div class="cifra__valor">VI</div><div class="cifra__texto">Foro Internacional de Tributación y Contabilidad</div></div>
        </div>
      </div>
    </div>

    <section class="seccion">
      <div class="contenedor bienvenida">
        <figure class="retrato aparece" style="margin:0">
          <div class="retrato__marco"><img src="mockup_assets/katarzyna-dunin-1.webp" alt="Katarzyna Dunin-Borkowski, presidenta de IPIDET"></div>
          <figcaption class="retrato__pie"><strong>Katarzyna Dunin-Borkowski</strong><span>Presidenta · Consejo Directivo <span data-render="consejo-periodo">2026 – 2028</span></span></figcaption>
          <div class="retrato__adorno"></div>
        </figure>
        <div class="aparece" style="--retardo:.12s">
          <span class="etiqueta">Bienvenida</span>
          <h2>Un espacio para crecer en la profesión tributaria</h2>
          <blockquote class="cita">En IPIDET encontrarás información tributaria y contable de interés y muy útil para tu desarrollo profesional.</blockquote>
          <p style="color:var(--gris)">Promovemos el estudio y la investigación tributaria, participamos en el perfeccionamiento de la legislación nacional y organizamos conferencias, seminarios y foros nacionales e internacionales.</p>
          <div style="margin-top:26px"><a class="enlace" href="{URL['nosotros']}#consejo">Conocer al Consejo Directivo {i("flecha")}</a></div>
        </div>
      </div>
    </section>

    <section class="seccion seccion--blanca">
      <div class="contenedor">
        <div class="cabeza cabeza--centro aparece"><span class="etiqueta">Explora</span><h2>¿Qué estás buscando?</h2></div>
        <div class="rejilla rejilla--3" data-escalonar>
          <a class="tarjeta tarjeta--viva" href="{URL['eventos']}"><div class="tarjeta__icono">{i("micro")}</div><h3>Eventos y conversatorios</h3><p>Conversatorios, presentaciones y foros con especialistas en tributación.</p><span class="enlace">Ver eventos {i("flecha")}</span></a>
          <a class="tarjeta tarjeta--viva" href="{URL['tramites']}"><div class="tarjeta__icono">{i("archivo")}</div><h3>Admisión</h3><p>Requisitos, cuota anual y los pasos para asociarte.</p><span class="enlace">Cómo asociarme {i("flecha")}</span></a>
          <a class="tarjeta tarjeta--viva" href="{URL['nosotros']}#consejo"><div class="tarjeta__icono">{i("personas")}</div><h3>Consejo Directivo</h3><p>Quiénes guían la institución en la gestión actual.</p><span class="enlace">Conocerlos {i("flecha")}</span></a>
        </div>
      </div>
    </section>

    <section class="seccion seccion--libro" id="agenda">
      <div class="contenedor agenda">
        <div>
          <div class="fila-cabeza aparece">
            <div><span class="etiqueta">Agenda</span><h2 data-agenda-titulo>Eventos recientes</h2></div>
            <a class="enlace" href="{URL['eventos']}">Todos los eventos {i("flecha")}</a>
          </div>
          <div data-render="agenda" data-escalonar></div>
        </div>
        <aside>
          <div class="fila-cabeza aparece"><div><span class="etiqueta">Novedades</span><h2>Comunicados</h2></div></div>
          <div data-render="comunicados" data-escalonar></div>
          <a class="video aparece" data-c="youtube" data-c-href="url" data-c-texto="no" href="https://www.youtube.com/channel/UCA8uw2eF3QWs5HiMPeUbz9g" target="_blank" rel="noopener">
            <span class="video__play">{i("play")}</span><span><strong>45 años de IPIDET</strong><small>Mira la serie en YouTube</small></span>
          </a>
        </aside>
      </div>
    </section>

    <section class="seccion seccion--blanca" data-seccion-aliadas hidden>
      <div class="contenedor">
        <div class="cabeza cabeza--centro"><span class="etiqueta">Red institucional</span><h2>Instituciones aliadas</h2></div>
        <div class="aliadas" data-render="aliadas"></div>
      </div>
    </section>
{BOLETIN}""")


# ═════════════════════════════════════════ NOSOTROS ════════════════════════════
VALORES = [("premio", "Compromiso"), ("escudo", "Honestidad"), ("personas", "Respeto"), ("check", "Responsabilidad"), ("diana", "Disciplina")]
OBJETIVOS = [
    ("personas", "Agrupar", "A profesionales con título universitario que, por razones científicas, académicas o profesionales, se han especializado en tributación."),
    ("libro", "Investigar", "El estudio de los principios y técnicas que rigen un sistema tributario y de las materias afines a la tributación."),
    ("edificio", "Perfeccionar", "La legislación tributaria nacional, participando en las comisiones destinadas a ese fin."),
    ("micro", "Difundir", "Con conferencias, seminarios, congresos y jornadas nacionales e internacionales sobre tributación."),
]
pagina("nosotros", "Nosotros — IPIDET", "Quiénes somos, nuestros valores y el Consejo Directivo de IPIDET.", heroe_interior(
    "Nosotros", "Al servicio de la <em>tributación</em> en el Perú desde 1979",
    "Somos una asociación civil de derecho privado, de carácter cultural y científico, sin fines de lucro, fundada el 23 de enero de 1979.",
    f'<a class="btn btn--laton" href="#consejo">{i("personas")} Consejo Directivo</a>', "mockup_assets/evento-21may26.jpg") + f"""
    <section class="seccion">
      <div class="contenedor">
        <div class="cabeza aparece"><span class="etiqueta">Quiénes somos</span><h2>Una comunidad que estudia y aporta a la tributación</h2>
          <p>En IPIDET el asociado puede participar en eventos, exponer y escribir artículos que se publican en los medios que organiza el Instituto.</p></div>
        <div class="rejilla rejilla--4" data-escalonar>
          {"".join(f'<div class="tarjeta tarjeta--viva"><div class="tarjeta__icono">{i(ic)}</div><h3>{t}</h3><p>{d}</p></div>' for ic, t, d in OBJETIVOS)}
        </div>
        <div class="aparece" style="margin-top:44px">
          <span class="etiqueta">Valores institucionales</span>
          <div class="valores" data-escalonar>{"".join(f'<span class="valor">{i(ic)} {t}</span>' for ic, t in VALORES)}</div>
        </div>
      </div>
    </section>

    <section class="seccion seccion--blanca" id="consejo">
      <div class="contenedor">
        <div class="cabeza aparece"><span class="etiqueta">Gestión <span data-render="consejo-periodo">2026 – 2028</span></span><h2>Consejo Directivo</h2></div>
        <div class="consejo" data-render="consejo"></div>
      </div>
    </section>

    <section class="seccion seccion--libro">
      <div class="contenedor">
        <div class="fila-cabeza aparece">
          <div><span class="etiqueta">Nuestra historia</span><h2>Quienes presidieron IPIDET</h2></div>
          <div class="linea-tiempo__botones">
            <button class="redondo" type="button" aria-label="Anteriores" data-mover="-1" data-objetivo="#pista">{i("izquierda")}</button>
            <button class="redondo" type="button" aria-label="Más recientes" data-mover="1" data-objetivo="#pista">{i("derecha")}</button>
          </div>
        </div>
        <div class="linea-tiempo aparece"><div class="linea-tiempo__pista" id="pista" data-render="expresidentes" tabindex="0" aria-label="Línea de tiempo de expresidentes"></div></div>
        <details class="tarjeta aparece" style="margin-top:30px">
          <summary style="cursor:pointer;font-weight:700;display:flex;justify-content:space-between;align-items:center">
            <span>{i("premio")} Socios honorarios</span>{i("abajo")}</summary>
          <ul class="nombres" data-render="honorarios" style="list-style:none;padding:0;margin:18px 0 0"></ul>
        </details>
      </div>
    </section>
{BOLETIN}""")


# ═════════════════════════════════════════ EVENTOS ═════════════════════════════
pagina("eventos", "Eventos — IPIDET", "Conversatorios institucionales, presentaciones y foros de IPIDET.", heroe_interior(
    "Eventos", "Eventos y <em>conversatorios</em>",
    "Conversatorios institucionales, presentaciones y foros con especialistas en tributación. Toca un evento para ver el detalle.",
    "", "mockup_assets/CI_20abri26.jpg") + f"""
    <section class="seccion">
      <div class="contenedor" data-render="eventos">
        <div class="destacado aparece" data-destacado></div>
        <div class="filtros aparece" role="group" aria-label="Filtrar eventos">
          <button class="pastilla" type="button" data-filtro="todos" aria-pressed="true">Todos</button>
          <button class="pastilla" type="button" data-filtro="proximos" aria-pressed="false">Próximos</button>
          <button class="pastilla" type="button" data-filtro="conversatorio" aria-pressed="false">Conversatorios</button>
          <button class="pastilla" type="button" data-filtro="presentacion" aria-pressed="false">Presentaciones</button>
          <button class="pastilla" type="button" data-filtro="evento" aria-pressed="false">Otros</button>
          <label class="buscador"><span class="sr">Buscar evento</span>{i("buscar")}<input type="search" placeholder="Buscar por tema…" data-buscar-eventos></label>
        </div>
        <p class="aparece" style="color:var(--gris-claro);font-size:.9rem;margin:-14px 0 18px" data-cuenta-eventos></p>
        <div class="rejilla rejilla--3" data-rejilla-eventos></div>
      </div>
    </section>
{BOLETIN}""")


# ═════════════════════════════════════════ TRÁMITES ════════════════════════════
DOCUMENTOS = [
    ("Solicitud de inscripción", "https://ipidet.org/wp-content/uploads/2026/10/Solicitud-de-Admision.pdf"),
    ("Requisitos de admisión", "https://ipidet.org/wp-content/uploads/2025/10/REQUISITOS-DE-ADMISION.pdf"),
    ("Reglamento de admisión", "https://ipidet.org/wp-content/uploads/2026/10/Reglamento-de-admision.pdf"),
    ("Beneficios de los asociados", "https://ipidet.org/wp-content/uploads/2026/10/Beneficios-de-los-asociados.pdf"),
    ("Guía de postulación · Lima", "https://ipidet.org/wp-content/uploads/2026/10/Guia-de-postulacion-IPIDET-.pdf"),
    ("Guía de postulación · Filiales", "https://ipidet.org/wp-content/uploads/2026/10/Guia-de-postulacion-IPIDET-Fiales.pdf"),
]
PREGUNTAS = [
    ("¿Quiénes pueden asociarse?", "Profesionales con título universitario que, por razones científicas, académicas o profesionales, se han especializado en tributación. Los requisitos completos están en el documento «Requisitos de admisión»."),
    ("¿Cuánto es la cuota anual?", "S/ 780 para asociados de Lima y S/ 350 para asociados de filiales en provincias. En Lima puedes fraccionarla en 3 cuotas de S/ 260."),
    ("¿Mi empresa puede pagar mi cuota?", "Sí. Escríbenos con los datos de la empresa y emitimos la factura a su nombre."),
    ("¿Dónde envío mi solicitud y documentos?", 'A <a class="enlace" href="mailto:administracion@ipidet.org">administracion@ipidet.org</a> o por WhatsApp al +51 922 474 067. También puedes acercarte a nuestra sede en Calle German Schreiber 276.'),
]
pagina("tramites", "Admisión y trámites — IPIDET", "Cómo asociarte a IPIDET: cuota anual, requisitos, documentos y pasos.", heroe_interior(
    "Trámites", "Asóciate a <em>IPIDET</em>",
    "Cuatro pasos, todos los documentos en un solo lugar y alguien del equipo para acompañarte.",
    f'<a class="btn btn--laton" href="#pasos">Ver los pasos {i("abajo")}</a><a class="btn btn--claro" href="#cuota">{i("tarjeta")} Cuota anual</a>',
    "mockup_assets/CI-10feb26.jpg") + f"""
    <section class="seccion" id="cuota">
      <div class="contenedor">
        <div class="fila-cabeza aparece">
          <div><span class="etiqueta">Cuota anual</span><h2>¿Dónde ejerces?</h2></div>
          <div class="selector" role="group" aria-label="Sede">
            <button type="button" data-sede="lima" aria-pressed="true">Lima</button>
            <button type="button" data-sede="provincia" aria-pressed="false">Provincias</button>
          </div>
        </div>
        <div class="precio aparece" data-precio>
          <div class="precio__monto">
            <span class="chip" data-precio-sede>Asociado ordinario · Lima</span>
            <div class="precio__cifra num" data-precio-monto><small>S/</small>780</div>
            <span style="color:rgba(236,246,239,.75)">por año</span>
          </div>
          <div class="precio__detalle">
            <ul class="lista-check">
              <li>{i("check")} Pagas en línea desde la sección Pagos de ipidet.org.</li>
              <li>{i("check")} Tu empresa puede pagarla: emitimos la factura a su nombre.</li>
              <li data-solo-lima>{i("check")} En Lima puedes fraccionarla en 3 cuotas de S/ 260.</li>
            </ul>
            <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:6px">
              <a class="btn btn--primario" href="https://ipidet.org/pagos/" target="_blank" rel="noopener">Ir a Pagos {i("externo")}</a>
              <a class="btn btn--borde" href="https://ipidet.org/wp-content/uploads/2026/10/Beneficios-de-los-asociados.pdf" target="_blank" rel="noopener">{i("archivo")} Beneficios</a>
            </div>
          </div>
        </div>
      </div>
    </section>

    <section class="seccion seccion--blanca" id="pasos">
      <div class="contenedor">
        <div class="cabeza aparece"><span class="etiqueta">Proceso de admisión</span><h2>Cuatro pasos para asociarte</h2></div>
        <div class="pasos" data-escalonar>
          <div class="paso"><h3>Descarga la solicitud</h3><p>Llena la solicitud de inscripción con tus datos personales y profesionales.</p>
            <a class="enlace" href="{DOCUMENTOS[0][1]}" target="_blank" rel="noopener">{i("descargar")} Solicitud (PDF)</a></div>
          <div class="paso"><h3>Revisa los requisitos</h3><p>Reúne los documentos que pide la guía de tu sede.</p>
            <a class="enlace" data-guia href="{DOCUMENTOS[4][1]}" target="_blank" rel="noopener">{i("descargar")} <span class="guia-nombre">Guía de postulación · Lima</span></a></div>
          <div class="paso"><h3>Envía tu postulación</h3><p>Manda la solicitud y los documentos. El equipo revisa tu expediente y te indica los siguientes pasos.</p>
            <a class="enlace" href="mailto:administracion@ipidet.org?subject=Postulaci%C3%B3n%20a%20IPIDET">{i("correo")} Enviar por correo</a></div>
          <div class="paso"><h3>Paga tu cuota</h3><p>Cuando seas admitido, paga tu cuota anual y accede al portal de asociados.</p>
            <a class="enlace" href="https://ipidet.org/pagos/" target="_blank" rel="noopener">{i("tarjeta")} Ir a Pagos</a></div>
        </div>
      </div>
    </section>

    <section class="seccion seccion--libro">
      <div class="contenedor">
        <div class="cabeza aparece"><span class="etiqueta">Documentos</span><h2>Todo lo que necesitas, en un solo lugar</h2></div>
        <div class="rejilla rejilla--3" data-escalonar>
          {"".join(f'<a class="documento" href="{u}" target="_blank" rel="noopener"><span class="documento__icono">{i("archivo")}</span><span><strong>{n}</strong><small>PDF</small></span>{i("externo", "icono--externo")}</a>' for n, u in DOCUMENTOS)}
        </div>
      </div>
    </section>

    <section class="seccion seccion--blanca">
      <div class="contenedor" style="max-width:860px">
        <div class="cabeza aparece"><span class="etiqueta">Preguntas frecuentes</span><h2>¿Tienes dudas?</h2></div>
        <div class="acordeon aparece">
          {"".join(f'<details{" open" if n == 0 else ""}><summary>{q}{i("abajo")}</summary><div class="respuesta">{a}</div></details>' for n, (q, a) in enumerate(PREGUNTAS))}
        </div>
        <p class="aparece" style="margin-top:28px;color:var(--gris)">¿Otra consulta? <a class="enlace" href="{URL['contacto']}">Escríbenos {i("flecha")}</a></p>
      </div>
    </section>""")


# ═════════════════════════════════════════ ASOCIADOS ═══════════════════════════
def renglon(icono, titulo, detalle, derecha="", estado="", pendiente=False):
    return (f'<div class="renglon"><span class="renglon__icono">{i(icono)}</span><span class="renglon__texto"><strong>{titulo}</strong><small>{detalle}</small></span>'
            f'{f"<span class=monto>{derecha}</span>" if derecha else ""}{f"<span class={chr(34)}estado{" estado--pendiente" if pendiente else ""}{chr(34)}>{estado}</span>" if estado else ""}</div>')


pagina("asociados", "Portal de asociados — IPIDET", "Tu cuota, tus eventos y tus documentos de IPIDET en un solo lugar.", heroe_interior(
    "Asociados", "Portal de <em>asociados</em>", "Tu cuota, tus eventos y tus documentos en un solo lugar.") + f"""
    <section class="seccion">
      <div class="contenedor">
        <p class="aviso-ejemplo aparece">{i("estrella")} Vista de ejemplo con datos ficticios: así se verá el portal cuando ingreses con tu cuenta.</p>

        <div data-demo-login class="aparece" style="max-width:460px;margin:34px auto 0">
          <form class="tarjeta" data-demo-entrar>
            <div class="tarjeta__icono">{i("candado")}</div>
            <h3>Ingresa a tu cuenta</h3>
            <p style="margin-bottom:18px">Usa el correo con el que te registraste en IPIDET.</p>
            <div class="formulario" style="grid-template-columns:1fr">
              <div><label for="demo-email">Correo</label><input class="campo" id="demo-email" type="email" placeholder="tucorreo@ejemplo.com" autocomplete="username"></div>
              <div><label for="demo-clave">Contraseña</label><input class="campo" id="demo-clave" type="password" autocomplete="current-password"></div>
              <button class="btn btn--primario" type="submit">Ver la vista de ejemplo {i("flecha", "icono--flecha")}</button>
              <a class="enlace" href="https://ipidet.org/asociados/" target="_blank" rel="noopener" style="justify-content:center">¿Olvidaste tu contraseña?</a>
            </div>
          </form>
        </div>

        <div data-demo-portal hidden style="margin-top:34px">
          <div class="portal">
            <aside>
              <div class="credencial">
                <img src="mockup_assets/logo-blanco.png" alt="IPIDET">
                <div class="credencial__nombre">Ana Torres Ruiz</div>
                <div style="color:var(--laton-claro);font-size:.9rem">Contadora pública</div>
                <div class="credencial__fila"><span>Registro<strong class="num">IPIDET-0000</strong></span><span>Vigente hasta<strong>31 dic 2026</strong></span></div>
              </div>
              <div style="display:grid;gap:10px;margin-top:16px">
                <a class="btn btn--laton" href="https://ipidet.org/pagos/" target="_blank" rel="noopener">{i("tarjeta")} Pagar cuota 2027</a>
                <button class="btn btn--borde" type="button" data-ir-pestana="p-documentos">{i("descargar")} Mi constancia</button>
                <a class="btn btn--borde" href="https://ipidet.org/biblioteca-ipidet/" target="_blank" rel="noopener">{i("libro")} Biblioteca</a>
                <button class="btn btn--borde" type="button" data-demo-salir>{i("salir")} Salir</button>
              </div>
            </aside>
            <div class="tarjeta">
              <div class="pestanas" role="tablist" aria-label="Mi cuenta">
                <button role="tab" aria-selected="true" aria-controls="p-resumen" id="t-resumen">Resumen</button>
                <button role="tab" aria-selected="false" aria-controls="p-cuotas" id="t-cuotas" tabindex="-1">Cuotas</button>
                <button role="tab" aria-selected="false" aria-controls="p-eventos" id="t-eventos" tabindex="-1">Mis eventos</button>
                <button role="tab" aria-selected="false" aria-controls="p-documentos" id="t-documentos" tabindex="-1">Documentos</button>
                <button role="tab" aria-selected="false" aria-controls="p-datos" id="t-datos" tabindex="-1">Mis datos</button>
              </div>
              <div role="tabpanel" id="p-resumen" aria-labelledby="t-resumen" class="panel-pestana">
                <h3 style="margin-bottom:6px">Hola, Ana</h3><p style="color:var(--gris)">Estás al día con tu cuota 2026.</p>
                {renglon("check", "Cuota 2026", "Pagada el 15 de enero de 2026", "S/ 780", "Pagada")}
                {renglon("calendario", "Cuota 2027", "Vence el 31 de marzo de 2027", "S/ 780", "Pendiente", True)}
                {renglon("micro", "Próximo evento", "Inscrita · revisa la agenda en Eventos", "", "Inscrita")}
              </div>
              <div role="tabpanel" id="p-cuotas" aria-labelledby="t-cuotas" class="panel-pestana" hidden>
                {renglon("calendario", "Cuota anual 2027", "Vence el 31 de marzo de 2027", "S/ 780", "Pendiente", True)}
                {renglon("recibo", "Cuota anual 2026", "Pagada el 15 de enero de 2026 · Boleta B001-00000", "S/ 780", "Pagada")}
                {renglon("recibo", "Cuota anual 2025", "Pagada el 8 de febrero de 2025 · Boleta B001-00000", "S/ 780", "Pagada")}
              </div>
              <div role="tabpanel" id="p-eventos" aria-labelledby="t-eventos" class="panel-pestana" hidden>
                {renglon("micro", "Conversatorio institucional", "Asististe · 55 minutos", "", "Asistió")}
                {renglon("libro", "Presentación de la Revista Institucional N.º 6", "Inscrita", "", "Inscrita")}
                <a class="enlace" href="{URL['eventos']}" style="margin-top:14px">Ver la agenda {i("flecha")}</a>
              </div>
              <div role="tabpanel" id="p-documentos" aria-labelledby="t-documentos" class="panel-pestana" hidden>
                {renglon("premio", "Constancia de asociado 2026", "PDF firmado", "", "Descargar")}
                {renglon("recibo", "Boleta de la cuota 2026", "PDF · S/ 780", "", "Descargar")}
              </div>
              <div role="tabpanel" id="p-datos" aria-labelledby="t-datos" class="panel-pestana" hidden>
                {renglon("usuario", "Ana Torres Ruiz", "Contadora pública")}
                {renglon("correo", "ana.torres@ejemplo.com", "Correo principal")}
                {renglon("maletin", "Empresa de ejemplo S.A.C.", "Centro de trabajo")}
                <p style="color:var(--gris);font-size:.92rem;margin-top:14px">¿Cambió algún dato? Actualízalo desde tu cuenta o escríbenos.</p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>""", wa=False)


# ═════════════════════════════════════════ CONTACTO ════════════════════════════
pagina("contacto", "Contacto — IPIDET", "Escríbenos, llámanos o visítanos en Lima.", heroe_interior(
    "Contacto", "¿En qué podemos <em>ayudarte</em>?", "Escríbenos por el canal que prefieras. Te respondemos lo antes posible.") + f"""
    <section class="seccion">
      <div class="contenedor contacto">
        <div data-escalonar>
          <a class="canal canal--wa" href="https://wa.me/51922474067" target="_blank" rel="noopener"><span class="canal__icono">{i("whatsapp")}</span><span><small>WhatsApp</small><strong>+51 922 474 067</strong></span></a>
          <a class="canal" href="tel:+5114226048"><span class="canal__icono">{i("telefono")}</span><span><small>Teléfonos</small><strong>(01) 422-6048 · (01) 421-7698</strong></span></a>
          <a class="canal" data-c="email" data-c-href="mailto" data-c-texto="no" href="mailto:administracion@ipidet.org"><span class="canal__icono">{i("correo")}</span><span><small>Correo</small><strong data-c="email">administracion@ipidet.org</strong></span></a>
          <a class="canal" href="https://www.google.com/maps/search/?api=1&amp;query=Calle+German+Schreiber+276+San+Isidro+Lima" target="_blank" rel="noopener"><span class="canal__icono">{i("lugar")}</span><span><small>Sede · cómo llegar</small><strong>Calle German Schreiber 276, Lima 27</strong></span></a>
          <div class="redes" style="margin-top:20px">
            <a data-c="facebook" data-c-href="url" data-c-texto="no" href="https://www.facebook.com/Ipidet/" target="_blank" rel="noopener" aria-label="Facebook" style="background:var(--verde-700)">{i("facebook")}</a>
            <a data-c="linkedin" data-c-href="url" data-c-texto="no" href="https://www.linkedin.com/in/ipidet-org-15186414a/" target="_blank" rel="noopener" aria-label="LinkedIn" style="background:var(--verde-700)">{i("linkedin")}</a>
            <a data-c="youtube" data-c-href="url" data-c-texto="no" href="https://www.youtube.com/channel/UCA8uw2eF3QWs5HiMPeUbz9g" target="_blank" rel="noopener" aria-label="YouTube" style="background:var(--verde-700)">{i("youtube")}</a>
          </div>
        </div>
        <form class="tarjeta aparece" data-api="contacto" data-ok="¡Gracias! Recibimos tu mensaje y te responderemos pronto." novalidate style="--retardo:.1s">
          <h3 style="margin-bottom:6px">Envíanos un mensaje</h3>
          <p style="margin-bottom:20px">Tus datos solo se usan para responderte.</p>
          <div class="formulario">
            <div><label for="c-nombre">Nombre</label><input class="campo" id="c-nombre" name="nombre" autocomplete="given-name" required></div>
            <div><label for="c-apellido">Apellido</label><input class="campo" id="c-apellido" name="apellido" autocomplete="family-name"></div>
            <div><label for="c-email">Correo</label><input class="campo" id="c-email" type="email" name="email" autocomplete="email" placeholder="tucorreo@ejemplo.com" required></div>
            <div><label for="c-celular">Celular <span class="ayuda">(opcional)</span></label><input class="campo" id="c-celular" type="tel" name="celular" autocomplete="tel" placeholder="+51 999 000 000"></div>
            <div class="ancho"><label for="c-tema">Tema</label>
              <select class="campo" id="c-tema" name="tema">
                <option value="admision">Información sobre afiliación</option>
                <option value="pago">Consulta sobre pago de cuota</option>
                <option value="evento">Inscripción a un evento</option>
                <option value="comprobante">Facturación y comprobantes</option>
                <option value="institucional">Convenios institucionales</option>
                <option value="otro">Otro</option>
              </select></div>
            <div class="ancho"><label for="c-mensaje">Mensaje</label><textarea class="campo" id="c-mensaje" name="mensaje" rows="5" required></textarea></div>
            <input class="trampa" type="text" name="web" tabindex="-1" autocomplete="off" aria-hidden="true">
            <label class="casilla ancho"><input type="checkbox" name="acepta" required> Autorizo a IPIDET a usar mis datos para responder mi consulta.</label>
            <p class="estado-form ancho" role="status"></p>
            <div class="ancho"><button class="btn btn--primario" type="submit">Enviar mensaje {i("enviar")}</button></div>
          </div>
        </form>
      </div>
    </section>""")
