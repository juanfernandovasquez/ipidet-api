from extract_faqs import sample_sent_emails, extract_faqs_with_claude
from knowledge_base.db import save_faq, get_active_faqs

print("Leyendo historial...")
emails = sample_sent_emails()
print(f"Extrayendo FAQs con Claude...")
faqs = extract_faqs_with_claude(emails)

for faq in faqs:
    save_faq(faq["pregunta"], faq["respuesta"], faq["categoria"], source="historial")

total = get_active_faqs()
print(f"\nGuardadas {len(faqs)} FAQs. Total activas: {len(total)}")
for f in total:
    print(f"  [{f['category']}] {f['question'][:70]}")
