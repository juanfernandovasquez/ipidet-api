"""Carga las plantillas de cobranza de Comunicaciones (idempotente; no pisa lo que el equipo editó).

Las plantillas usan variables ({{periodo}}, {{monto}}, {{medios_pago}}, ...) que se llenan al enviar con
los datos de la base (webapp/variables_correo.py): no llevan años ni montos escritos.

- Las dos antiguas («Recordatorio cuota» y «Aviso de mora») se reemplazan solo si nadie las editó;
  si las editaron, se dejan y las nuevas se agregan aparte.
- Una plantilla nueva ya cargada no se vuelve a tocar (aunque se haya editado o eliminado).

    python migrate_plantillas_cobranza.py --probar     # solo muestra lo que haría
    python migrate_plantillas_cobranza.py
"""
import sys
from datetime import datetime, timezone

from webapp import db as pdb

# Sin saltos de línea entre etiquetas: el editor de Comunicaciones los mostraría como líneas vacías.
_SALUDO = "<p>Estimado(a) {{tratamiento}} {{nombre_corto}}:</p>"
_DETALLE = ('<table style="border-collapse:collapse;margin:12px 0;font-size:14px">'
            '<tr><td style="padding:6px 16px 6px 0;color:#64748b">Código de asociado</td><td style="padding:6px 0"><strong>{{numero_registro}}</strong></td></tr>'
            '<tr><td style="padding:6px 16px 6px 0;color:#64748b">Período</td><td style="padding:6px 0"><strong>{{periodo}}</strong></td></tr>'
            '<tr><td style="padding:6px 16px 6px 0;color:#64748b">Monto pendiente</td><td style="padding:6px 0"><strong>{{monto}}</strong></td></tr>'
            '</table>')
_COMO_PAGAR = ("<p><strong>¿Cómo pagar?</strong><br>"
               "Puede pagar en línea desde el portal de asociados en {{org_web}}. "
               "Medios de pago disponibles: {{medios_pago}}. "
               "Si prefiere una transferencia, escríbanos a {{org_email}} y le enviaremos los datos de la cuenta.</p>"
               "<p>Una vez realizado el pago, envíenos la constancia a {{org_email}} para registrarlo y emitir su comprobante.</p>")
_FRACCIONAR = ("<p>Si le resulta más cómodo pagar en partes, podemos ofrecerle un <strong>fraccionamiento</strong>: "
               "escríbanos a {{org_email}} y lo coordinamos.</p>")
_YA_PAGO = ("<p>Si ya realizó el pago, le pedimos disculpas por este mensaje y le agradeceremos enviarnos la constancia "
            "para actualizar nuestros registros.</p>")
_FIRMA = ("<p>Atentamente,<br><strong>Administración — IPIDET</strong><br>"
          "{{org_email}} · {{org_telefono}}</p>")

PLANTILLAS = [
    {
        "key": "cobranza_apertura", "orden": 1,
        "nombre": "Cobranza 1 · Cuota del nuevo período",
        "asunto": "Cuota de asociado {{periodo}} — IPIDET",
        "cuerpo": (_SALUDO
                   + "<p>Gracias por formar parte de IPIDET. Le informamos que ya puede realizar el pago de su "
                     "cuota de asociado correspondiente al período <strong>{{periodo}}</strong>.</p>"
                   + _DETALLE + _COMO_PAGAR + _FRACCIONAR
                   + "<p>Su aporte hace posible nuestras actividades, eventos y capacitaciones. ¡Gracias por su confianza!</p>"
                   + _FIRMA),
    },
    {
        "key": "recordatorio", "orden": 2,
        "nombre": "Cobranza 2 · Recordatorio de cuota pendiente",
        "asunto": "Recordatorio: cuota de asociado {{periodo}} pendiente",
        "cuerpo": (_SALUDO
                   + "<p>Le escribimos para recordarle que su cuota de asociado del período <strong>{{periodo}}</strong> "
                     "se encuentra pendiente de pago.</p>"
                   + _DETALLE
                   + "<p>Mantener su cuota al día le permite seguir disfrutando de los beneficios de su membresía.</p>"
                   + _COMO_PAGAR + _FRACCIONAR + _YA_PAGO + _FIRMA),
    },
    {
        "key": "mora", "orden": 3,
        "nombre": "Cobranza 3 · Cuota vencida",
        "asunto": "Su cuota de asociado {{periodo}} se encuentra vencida",
        "cuerpo": (_SALUDO
                   + "<p>Al revisar nuestros registros, su cuota de asociado del período <strong>{{periodo}}</strong> "
                     "figura como <strong>vencida</strong>.</p>"
                   + _DETALLE
                   + "<p>Le pedimos regularizarla a más tardar el <strong>[COMPLETAR: fecha límite]</strong> para "
                     "mantener su condición de asociado(a) hábil y los beneficios de su membresía.</p>"
                   + _COMO_PAGAR + _FRACCIONAR + _YA_PAGO + _FIRMA),
    },
    {
        "key": "cobranza_perdida_condicion", "orden": 4,
        "nombre": "Cobranza 4 · Último aviso: pérdida de la condición de asociado",
        "asunto": "Último aviso: su condición de asociado(a) de IPIDET — cuota {{periodo}} pendiente",
        "cuerpo": (_SALUDO
                   + "<p>Nos comunicamos con usted porque su cuota de asociado del período <strong>{{periodo}}</strong>, "
                     "por <strong>{{monto}}</strong>, continúa pendiente de pago a pesar de nuestros recordatorios anteriores.</p>"
                   + '<p style="background:#fef2f2;border-left:4px solid #dc2626;padding:12px 16px;color:#7f1d1d">'
                     "De no regularizar este pago hasta el <strong>[COMPLETAR: fecha límite]</strong>, "
                     "<strong>perderá su condición de asociado(a) de IPIDET</strong> y, con ella, los beneficios de la "
                     "membresía, como el acceso al portal de asociados y la emisión de constancias de membresía.</p>"
                   + "<p>A la fecha, sumando todas sus cuotas pendientes ({{periodos_adeudados}}), su deuda total es de "
                     "<strong>{{deuda_total}}</strong>.</p>"
                   + "<p>Queremos que siga formando parte de IPIDET. Si tiene dificultades para pagar el monto completo, "
                     "podemos ofrecerle un <strong>fraccionamiento</strong>: escríbanos a {{org_email}} y lo coordinamos.</p>"
                   + _COMO_PAGAR + _YA_PAGO
                   + "<p>Si su decisión es no continuar como asociado(a), le agradeceremos comunicárnoslo a {{org_email}}.</p>"
                   + _FIRMA),
    },
    {
        "key": "cobranza_varios_periodos", "orden": 5,
        "nombre": "Cobranza 5 · Deuda de varios períodos",
        "asunto": "Estado de cuenta de su membresía IPIDET",
        "cuerpo": (_SALUDO
                   + "<p>Le compartimos el estado de cuenta de su membresía. Según nuestros registros, tiene cuotas "
                     "pendientes de los períodos <strong>{{periodos_adeudados}}</strong>, por un total de "
                     "<strong>{{deuda_total}}</strong>.</p>"
                   + "<p>Para ponerse al día con mayor facilidad, podemos ofrecerle un <strong>fraccionamiento</strong> "
                     "de la deuda: escríbanos a {{org_email}} y lo coordinamos.</p>"
                   + _COMO_PAGAR + _YA_PAGO + _FIRMA),
    },
    {
        "key": "cobranza_gracias", "orden": 6,
        "nombre": "Cobranza 6 · Gracias por su pago",
        "asunto": "Gracias por su pago — cuota {{periodo}}",
        "cuerpo": (_SALUDO
                   + "<p>Confirmamos que recibimos el pago de su cuota de asociado del período <strong>{{periodo}}</strong>. "
                     "¡Muchas gracias por seguir siendo parte de IPIDET!</p>"
                   + "<p>Si aún no recibió su comprobante de pago, escríbanos a {{org_email}} y se lo enviaremos.</p>"
                   + _FIRMA),
    },
]

# Las dos plantillas antiguas que se reemplazan si nadie las editó, y las que bajan de orden.
_REEMPLAZABLES = ("recordatorio", "mora")
_ORIGEN = "plantillas_cobranza"  # marca lo que cargó este script: así no se reemplaza dos veces
_REORDENAR = {"evento": (3, 10), "actualizacion_datos": (4, 11)}


def main(probar: bool) -> None:
    ahora = datetime.now(timezone.utc)
    originales = {p["key"]: p for p in pdb._PLANTILLAS_DEFAULT}
    for p in PLANTILLAS:
        p = {**p, "origen": _ORIGEN}
        actual = pdb.plantillas_col.find_one({"key": p["key"]})
        if p["key"] in _REEMPLAZABLES and actual and actual.get("origen") != _ORIGEN:
            orig = originales[p["key"]]
            sin_editar = (actual.get("cuerpo") == orig["cuerpo"] and actual.get("asunto") == orig["asunto"]
                          and not actual.get("updated_at"))
            if sin_editar:
                print(f"Reemplaza «{actual.get('nombre')}» por «{p['nombre']}»")
                if not probar:
                    pdb.plantillas_col.update_one({"_id": actual["_id"]}, {"$set": {
                        **p, "tipo": "socios", "activa": True, "updated_at": ahora}})
                continue
            p = {**p, "key": f"cobranza_{p['key']}"}  # la editada se queda; la nueva va aparte
            actual = pdb.plantillas_col.find_one({"key": p["key"]})
        if actual:
            print(f"Ya existe «{actual.get('nombre')}»: no se toca")
            continue
        print(f"Agrega «{p['nombre']}»")
        if not probar:
            pdb.plantillas_col.insert_one({**p, "tipo": "socios", "activa": True, "created_at": ahora})
    for key, (antes, despues) in _REORDENAR.items():
        if not probar:
            pdb.plantillas_col.update_one({"key": key, "orden": antes}, {"$set": {"orden": despues}})
    if probar:
        print("(prueba: no se escribió nada)")


if __name__ == "__main__":
    main(probar="--probar" in sys.argv)
