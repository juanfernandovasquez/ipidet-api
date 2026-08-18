"""
Panel de administración — ejecutar con: python admin.py
Permite agregar FAQs y revisar el estado del sistema.
"""
from knowledge_base.db import save_faq, get_active_faqs, interactions


def menu():
    while True:
        print("\n=== IPIDET Admin ===")
        print("1. Ver FAQs activas")
        print("2. Agregar FAQ")
        print("3. Ver interacciones pendientes de aprendizaje")
        print("4. Salir")
        opcion = input("\nOpción: ").strip()

        if opcion == "1":
            ver_faqs()
        elif opcion == "2":
            agregar_faq()
        elif opcion == "3":
            ver_pendientes()
        elif opcion == "4":
            break


def ver_faqs():
    faqs = get_active_faqs()
    if not faqs:
        print("\nNo hay FAQs registradas aún.")
        return
    print(f"\n{len(faqs)} FAQs activas:\n")
    for f in faqs:
        print(f"[{f['category']}] {f['question']}")
        print(f"  → {f['answer'][:80]}...")
        print(f"  Usada {f.get('times_used', 0)} veces\n")


def agregar_faq():
    print("\n--- Nueva FAQ ---")
    question = input("Pregunta tipo: ").strip()
    answer = input("Respuesta: ").strip()
    category = input("Categoría: ").strip()

    if question and answer and category:
        faq_id = save_faq(question, answer, category)
        print(f"\n✓ FAQ guardada con ID: {faq_id}")
    else:
        print("Campos incompletos. Cancelado.")


def ver_pendientes():
    pending = list(interactions.find({"human_response": {"$ne": None}, "converted_to_faq": False}))
    if not pending:
        print("\nNo hay interacciones pendientes de revisión.")
        return
    print(f"\n{len(pending)} interacción(es) con respuesta humana:\n")
    for i, p in enumerate(pending, 1):
        print(f"{i}. De: {p['from']}")
        print(f"   Asunto: {p['subject']}")
        print(f"   Respuesta dada: {p['human_response'][:100]}...\n")


if __name__ == "__main__":
    menu()
