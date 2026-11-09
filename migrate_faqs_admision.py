"""Carga las FAQs de admisión de asociados (idempotente: no duplica ni pisa ediciones).

Fuentes: la página https://ipidet.org/admision/ (guías de postulación Lima y Filiales,
Requisitos y Reglamento de Admisión, Beneficios de los asociados) y las respuestas que el
equipo envió a los postulantes en noviembre de 2026.

Se crean SIN autorizar: el agente las usa solo para borradores hasta que el equipo las
revise y active «Responde sola» en /faqs.

    python migrate_faqs_admision.py
"""
from webapp import db

CATEGORIA = "Admisión"
GUIA_LIMA = "https://ipidet.org/wp-content/uploads/2026/10/Guia-de-postulacion-IPIDET-.pdf"
GUIA_FILIALES = "https://ipidet.org/wp-content/uploads/2026/10/Guia-de-postulacion-IPIDET-Fiales.pdf"
SOLICITUD = "https://ipidet.org/wp-content/uploads/2026/10/Solicitud-de-Admision.pdf"
REGLAMENTO = "https://ipidet.org/wp-content/uploads/2026/10/Reglamento-de-admision.pdf"
BENEFICIOS = "https://ipidet.org/wp-content/uploads/2026/10/Beneficios-de-los-asociados.pdf"

FAQS = [
    ("¿Qué es el IPIDET y quiénes lo integran?",
     "El Instituto Peruano de Investigación y Desarrollo Tributario (IPIDET) es una asociación civil de derecho "
     "privado, de carácter cultural y científico, sin fines de lucro. Promueve la investigación y el estudio de la "
     "tributación desde su fundación, el 23 de enero de 1979, y forma parte del Instituto Latinoamericano de "
     "Derecho Tributario (ILADT).\n\n"
     "Reúne a profesionales (abogados, contadores, economistas y de otras ramas) especializados en tributación. "
     "Los asociados participan en Asambleas Académicas, Conversatorios Institucionales, Comisiones Temáticas de "
     "Estudio y en las publicaciones del Instituto."),

    ("¿Cuáles son los requisitos para ser asociado del IPIDET?",
     "Para postular como asociado se requiere:\n"
     "- Ser egresado de uno o más centros de estudios universitarios y estar especializado en tributación por "
     "razones científicas, académicas o profesionales.\n"
     "- Presentar su Curriculum Vitae no documentado.\n"
     "- Presentar el formulario de Solicitud de Admisión, completo y firmado por dos asociados con una antigüedad "
     "no menor de cinco años. Debe estar impreso en papel membretado del IPIDET.\n"
     "- Solo para Filiales: acreditar residencia y trabajo en la Filial a la que postula.\n"
     "- Demostrar ante el Comité Calificador su interés por la tributación, mediante una entrevista y la "
     "documentación que este estime pertinente.\n"
     "- Ser aprobado por la Asamblea General, previa recomendación del Comité Calificador.\n"
     "- Pagar la cuota única de ingreso y la cuota ordinaria anual.\n\n"
     f"Guía de postulación Lima: {GUIA_LIMA}\n"
     f"Guía de postulación Filiales: {GUIA_FILIALES}\n"
     "Toda la información está en https://ipidet.org/admision/"),

    ("¿Qué documentos debo enviar para postular y a dónde?",
     "Se envían dos documentos, en PDF, al correo ipidet@ipidet.org:\n"
     "1. La Solicitud de Admisión completa, con su firma y la de dos asociados con no menos de cinco años de "
     f"antigüedad. Formato: {SOLICITUD}\n"
     "2. Su Curriculum Vitae detallado no documentado.\n\n"
     "El interés en la tributación se acredita con las constancias o certificaciones que el postulante considere "
     "necesarias. El Comité Calificador puede hacer una entrevista y las verificaciones que estime convenientes."),

    ("¿Cómo es el procedimiento de admisión?",
     "1. El postulante envía su Solicitud de Admisión firmada y su CV.\n"
     "2. El Comité Calificador analiza la documentación y emite su opinión al Consejo Directivo.\n"
     "3. El Consejo Directivo revisa la propuesta y, de proceder, la eleva a la Asamblea General.\n"
     "4. La Asamblea General de Asociados aprueba la admisión.\n"
     "5. El nuevo asociado deposita la cuota única de ingreso y la cuota anual ordinaria.\n"
     "6. La incorporación se realiza en una ceremonia especial del IPIDET.\n\n"
     "En Lima, el Comité Calificador está conformado por cuatro asociados activos. En las Filiales lo conforman el "
     "Presidente de la Filial y un asociado, quienes presentan a sus candidatos al Consejo Directivo.\n"
     f"Reglamento de Admisión: {REGLAMENTO}"),

    ("¿Cuánto cuesta asociarse al IPIDET?",
     "Las cuotas se pagan después de que la Asamblea General aprueba la admisión:\n"
     "- Sede Lima: cuota única de ingreso S/ 780.00 y cuota anual ordinaria S/ 780.00.\n"
     "- Filiales: cuota única de ingreso S/ 50.00 y cuota anual ordinaria S/ 350.00.\n\n"
     "Damos facilidades de pago."),

    ("¿Qué beneficios tienen los asociados del IPIDET?",
     "- Integrar los Comités Tributarios, dedicados al estudio e investigación de las normas y principios "
     "tributarios.\n"
     "- Participar sin costo en los conversatorios del IPIDET sobre temas de actualidad tributaria.\n"
     "- Tarifa preferencial en las Asambleas Académicas, Seminarios, Congresos y Jornadas Nacionales que organiza "
     "el Instituto.\n"
     "- Participar en las publicaciones del Instituto.\n"
     "- Posibilidad de integrar el Consejo Directivo.\n"
     "- Integrar el equipo de miembros que representa al Instituto en congresos internacionales.\n\n"
     f"Detalle: {BENEFICIOS}"),
]


def main():
    creadas = 0
    for pregunta, respuesta in FAQS:
        if db.faqs_col.find_one({"question": pregunta, "active": True}):
            print("ya existe:", pregunta)
            continue
        db.save_faq(pregunta, respuesta, CATEGORIA, autorizada=False)
        creadas += 1
        print("creada:", pregunta)
    print(f"{creadas} FAQs nuevas en «{CATEGORIA}» (sin autorizar).")


if __name__ == "__main__":
    main()
