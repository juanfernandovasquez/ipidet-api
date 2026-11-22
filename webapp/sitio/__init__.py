"""Sitio público de IPIDET (/web): página propia, separada de WordPress.

config.py   configuración y contenido editable (config.sitio_config, inicial.json) y medios de pago
cuentas.py  cuentas sin contraseña (código al correo) y sesión propia, separada de la del panel
eventos.py  qué se publica, quién ve cada grabación, precios e inscripción
ordenes.py  inscripciones y pagos: único camino para confirmar un cobro de la web
pagos.py    tipos de medio de pago y el lugar donde se conectará una pasarela (Niubiz: pendiente)
avisos.py   correos a la persona y avisos al equipo
rutas.py    páginas y API de /web
panel.py    administración en /sitio y en la ficha de cada evento
"""
