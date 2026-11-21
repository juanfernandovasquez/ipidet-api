/* Contenido de respaldo del sitio, tomado de ipidet.org (noviembre 2026).
   Cuando la API de la plataforma responde (/api/web/sitio), sitio.js reemplaza consejo, eventos,
   contacto, cuotas, aliadas y cifras por lo que el equipo cargó allí. Lo histórico (expresidentes,
   socios honorarios, objetivos, valores) vive solo aquí. */
window.IPIDET_DATOS = {
  fundacion: "1979-01-23",
  api: "https://ipidet-api.onrender.com/api/web",

  contacto: {
    direccion: "Calle German Schreiber 276, Lima 27",
    mapa: "https://www.google.com/maps/search/?api=1&query=Calle+German+Schreiber+276+San+Isidro+Lima",
    telefonos: ["(01) 422-6048", "(01) 421-7698"],
    whatsapp: "+51 922 474 067",
    whatsapp_url: "https://wa.me/51922474067",
    email: "administracion@ipidet.org",
    facebook: "https://www.facebook.com/Ipidet/",
    linkedin: "https://www.linkedin.com/in/ipidet-org-15186414a/",
    youtube: "https://www.youtube.com/channel/UCA8uw2eF3QWs5HiMPeUbz9g",
  },

  consejo_periodo: "2026 – 2028",
  consejo: [
    { nombre: "Katarzyna Dunin-Borkowski", cargo: "Presidenta", foto: "mockup_assets/consejo/katarzyna-dunin.webp" },
    { nombre: "Miguel Bobadilla La Madrid", cargo: "Vicepresidente", foto: "mockup_assets/consejo/miguel-bobadilla.webp" },
    { nombre: "Gloria Guevara", cargo: "Secretaria", foto: "mockup_assets/consejo/gloria-guevara.webp" },
    { nombre: "Tadeo Caballero", cargo: "Tesorero", foto: "mockup_assets/consejo/tadeo-caballero.webp" },
    { nombre: "Fernando Miranda", cargo: "Vocal", foto: "mockup_assets/consejo/fernando-miranda.webp" },
    { nombre: "José Barja", cargo: "Vocal", foto: "mockup_assets/consejo/jose-barja.webp" },
    { nombre: "Diego Sarmiento", cargo: "Vocal", foto: "mockup_assets/consejo/diego-sarmiento.webp" },
  ],

  // ipidet.org/consejo-directivo muestra nombres y períodos en bloques separados: van en el mismo orden.
  expresidentes: [
    ["1981 – 1983", "Enrique Noriega Del Valle Noguerol"], ["1983 – 1985", "Manuel Luna Victoria Sánchez"],
    ["1985 – 1987", "Enrique Noriega Del Valle Noguerol"], ["1987 – 1989", "César Rodríguez Dueñas"],
    ["1989 – 1991", "César Chang Koo"], ["1991 – 1993", "Nelson Santos Guardamino"],
    ["1993 – 1995", "Víctor Vargas Calderón"], ["1995 – 1997", "Alberto Nue Bracamonte"],
    ["1999 – 2001", "Katica Svarcic Morales"], ["2001 – 2003", "Rolando León Tenicela"],
    ["2003 – 2004", "Alberto Nue Bracamonte"], ["2004 – 2006", "Guillermo Ruiz Secada"],
    ["2006 – 2008", "Rubén Del Rosario Goytizolo"], ["2008 – 2010", "César Rodríguez Dueñas"],
    ["2010 – 2012", "Luis Alberto Durán Rojo"], ["2012 – 2014", "María Perales Bazalar"],
    ["2014 – 2016", "Carlos Llosa Saldaña"], ["2016 – 2018", "John Casas Aguilar"],
    ["2018 – 2020", "Walker Villanueva Gutiérrez"], ["2020 – 2022", "Víctor Cruzado Ribeyro"],
    ["2022 – 2024", "Khaled Luyo Acosta"], ["2024 – 2026", "Carlos Valle Larrea"],
  ],

  honorarios: [
    "Humberto Allemant Salazar", "Francisco Escribano López", "Alberto Tarsitano", "Víctor Gallart Tapia",
    "Luis Hernández Berenguel", "Manuel Luna Victoria Sánchez", "César Talledo Mazú", "Fernando Serrano",
    "César Chang Koo", "Víctor Vargas Calderón", "César Rodríguez Dueñas", "Alberto Nue Bracamonte",
    "Graciela Fernández Baca", "Luis Durán Rojo",
  ],

  // Fechas tomadas del nombre de cada flyer. Con la API, salen de la ficha del evento en la plataforma.
  eventos: [
    { titulo: "Presentación de libro: Puentes entre la Tributación y Contabilidad. Homenaje a César Rodríguez Dueñas", fecha: "2026-05-21", hora: "6:30 p. m.", lugar: "Auditorio NOS de la PUCP",
      flyer: "mockup_assets/evento-21may26.jpg" },
    { titulo: "Conversatorio institucional: Principales diferencias entre lineamientos globales y locales de precio de transferencia",
      fecha: "2026-04-20", flyer: "mockup_assets/CI_20abri26.jpg" },
    { titulo: "Cierre contable y tributario 2025", fecha: "2026-03-03", flyer: "mockup_assets/evento-03y04mar26-755x325.webp" },
    { titulo: "Conversatorio institucional: Claves tributarias 2025, desde la práctica", fecha: "2026-02-10",
      flyer: "mockup_assets/CI-10feb26.jpg" },
    { titulo: "Presentación: Revista Institucional N.º 6 «Tributación y Desarrollo»", fecha: "2025-08-28",
      flyer: "mockup_assets/Evento-28ago25-755x325.png" },
    { titulo: "Conversatorio institucional: Modelos de sostenibilidad tributaria para empresas latinoamericanas",
      fecha: "2025-06-12", flyer: "mockup_assets/CI-12jun25-755x325.webp" },
  ],

  comunicados: [
    { titulo: "Aviso sobre la línea de atención", fecha: "abril 2026", imagen: "mockup_assets/comunicado20abr26.jpeg" },
    { titulo: "VI Foro Internacional de Tributación y Contabilidad", fecha: "25 y 26 de noviembre de 2025", imagen: "mockup_assets/flyer-VI-FORO.jpeg" },
  ],

  cuotas: { lima: 780, provincia: 350, fraccion_3_meses: 260 },
  pagos_url: "https://ipidet.org/pagos/",
  biblioteca_url: "https://ipidet.org/biblioteca-ipidet/",

  documentos: [
    { nombre: "Solicitud de inscripción", url: "https://ipidet.org/wp-content/uploads/2026/10/Solicitud-de-Admision.pdf" },
    { nombre: "Requisitos de admisión", url: "https://ipidet.org/wp-content/uploads/2025/10/REQUISITOS-DE-ADMISION.pdf" },
    { nombre: "Reglamento de admisión", url: "https://ipidet.org/wp-content/uploads/2026/10/Reglamento-de-admision.pdf" },
    { nombre: "Beneficios de los asociados", url: "https://ipidet.org/wp-content/uploads/2026/10/Beneficios-de-los-asociados.pdf" },
    { nombre: "Guía de postulación · Lima", url: "https://ipidet.org/wp-content/uploads/2026/10/Guia-de-postulacion-IPIDET-.pdf", sede: "lima" },
    { nombre: "Guía de postulación · Filiales", url: "https://ipidet.org/wp-content/uploads/2026/10/Guia-de-postulacion-IPIDET-Fiales.pdf", sede: "provincia" },
  ],
};
