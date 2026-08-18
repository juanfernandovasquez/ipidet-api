import mailbox
import anthropic
import json
from config.settings import ANTHROPIC_API_KEY
from knowledge_base.db import save_faq, get_active_faqs

MBOX = r"CORREOS/Todo el correo, con Spam y Papelera incluidos-002-001.mbox"
SKIP_SUBJECTS = ["bancos al", "entrega automatica", "auto:", "delivery", "noreply"]


def extract_body(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    return part.get_payload(decode=True).decode("utf-8", errors="ignore").strip()
                except Exception:
                    pass
    try:
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode("utf-8", errors="ignore").strip()
    except Exception:
        pass
    return ""


def sample_sent_emails(max_emails=40, scan_limit=6000):
    mb = mailbox.mbox(MBOX)
    results = []
    count = 0

    for msg in mb:
        count += 1
        if count > scan_limit:
            break

        from_addr = str(msg.get("From", "")).lower()
        if "administracion@ipidet.org" not in from_addr:
            continue

        subject = str(msg.get("Subject", ""))
        if any(x in subject.lower() for x in SKIP_SUBJECTS):
            continue

        body = extract_body(msg)
        if len(body) < 80:
            continue
        # skip if only signature
        if body.startswith("Juan V") and len(body) < 200:
            continue

        results.append({"subject": subject[:100], "body": body[:500]})
        if len(results) >= max_emails:
            break

    print(f"Revisados: {count} | Seleccionados: {len(results)}")
    return results


def extract_faqs_with_claude(emails: list) -> list:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    emails_text = "\n\n".join(
        f"[{i+1}] Asunto: {e['subject']}\nRespuesta: {e['body'][:400]}"
        for i, e in enumerate(emails)
    )

    prompt = f"""Analiza estos correos enviados por IPIDET y extrae las FAQs más útiles.

CORREOS:
{emails_text}

Extrae SOLO las respuestas que sean reutilizables (preguntas frecuentes de clientes/miembros).
Ignora reportes internos, recordatorios de pago específicos, o respuestas muy personalizadas.

Responde con un JSON array:
[
  {{
    "pregunta": "pregunta tipo que haría un cliente",
    "respuesta": "respuesta general reutilizable",
    "categoria": "categoria breve"
  }}
]

Máximo 8 FAQs. Solo las más genéricas y reutilizables."""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text
    start = text.find("[")
    end = text.rfind("]") + 1
    return json.loads(text[start:end])


def main():
    print("Leyendo correos del historial...")
    emails = sample_sent_emails()

    if not emails:
        print("No se encontraron correos enviados.")
        return

    print(f"\nEnviando {len(emails)} correos a Claude para extraer FAQs...")
    faqs = extract_faqs_with_claude(emails)

    print(f"\nFAQs extraidas: {len(faqs)}\n")
    for i, faq in enumerate(faqs, 1):
        categoria = faq['categoria'].encode('ascii', errors='replace').decode()
        pregunta = faq['pregunta'].encode('ascii', errors='replace').decode()
        respuesta = faq['respuesta'][:100].encode('ascii', errors='replace').decode()
        print(f"[{i}] {categoria.upper()}")
        print(f"  P: {pregunta}")
        print(f"  R: {respuesta}...")
        print()

    confirmar = input("¿Guardar estas FAQs en la base de datos? (s/n): ").strip().lower()
    if confirmar == "s":
        for faq in faqs:
            save_faq(faq["pregunta"], faq["respuesta"], faq["categoria"], source="historial")
        total = len(get_active_faqs())
        print(f"\nGuardadas. Total FAQs activas: {total}")
    else:
        print("Cancelado.")


if __name__ == "__main__":
    main()
