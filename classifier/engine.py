import json
import re
import anthropic
from config.settings import ANTHROPIC_API_KEY, CONFIDENCE_THRESHOLD, CRITICAL_KEYWORDS
from knowledge_base.db import get_active_faqs

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# Máximo 3 imágenes por email para no exceder límites de la API
_MAX_IMAGES = 3


def _build_content(text: str, images: list) -> list:
    """Construye lista de bloques para la API: texto + imágenes adjuntas."""
    blocks = [{"type": "text", "text": text}]
    for img in images[:_MAX_IMAGES]:
        blocks.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": img["media_type"],
                "data": img["data"],
            },
        })
    return blocks


def classify_email(email_data: dict) -> dict:
    """
    Classifies an incoming email.
    Returns dict with keys:
      type: spam | critical | faq | clarification_needed | unknown
      confidence: float 0-1
      faq_id: str | None
      faq_answer: str | None
      category: str
      summary: str
      reason: str
      clarification_question: str | None
    """
    content = (
        f"Asunto: {email_data.get('subject', '')}\n"
        f"De: {email_data.get('from', '')}\n\n"
        f"{email_data.get('body', '')[:3000]}"
    )

    active_faqs = get_active_faqs()
    faqs_text = (
        "\n".join(f"ID:{str(f['_id'])} | {f['category']} | {f['question']}" for f in active_faqs)
        if active_faqs else "Sin FAQs registradas aún."
    )

    from knowledge_base.db import get_upcoming_events
    upcoming = get_upcoming_events()
    events_text = (
        "\n".join(
            f"ID:{str(e['_id'])} | {e['name']} | {e['date'].strftime('%d/%m/%Y')} | {e['location']}"
            for e in upcoming
        )
        if upcoming else "Sin eventos próximos registrados."
    )

    prompt = f"""Eres el clasificador de emails de IPIDET. Analiza el siguiente email.

EMAIL:
{content}

FAQs DISPONIBLES:
{faqs_text}

EVENTOS PRÓXIMOS:
{events_text}

TEMAS CRÍTICOS (siempre escalar, nunca responder automáticamente):
{', '.join(CRITICAL_KEYWORDS)}

Umbral mínimo de confianza para respuesta automática: {CONFIDENCE_THRESHOLD}

Responde ÚNICAMENTE con un JSON válido con esta estructura exacta:
{{
  "type": "spam" | "administrative" | "critical" | "faq" | "event_inquiry" | "payment_proof" | "clarification_needed" | "unknown",
  "confidence": <float 0.0-1.0>,
  "faq_id": "<id de la FAQ si type=faq, sino null>",
  "event_id": "<id del evento si type=event_inquiry, sino null>",
  "category": "<categoría breve del email>",
  "summary": "<resumen en 1-2 oraciones>",
  "reason": "<explicación de la decisión>",
  "clarification_question": "<pregunta al remitente si type=clarification_needed, sino null>"
}}

Definiciones:
- spam: publicidad no solicitada, notificaciones automáticas sin valor, irrelevante
- administrative: correos operativos de la propia organización — pagos que debe hacer IPIDET, facturas de proveedores, renovaciones de suscripciones, comunicaciones con proveedores de servicios, plataformas, hosting, etc. NO son de miembros ni del público.
- critical: temas legales, quejas graves, prensa, urgencias — SIEMPRE escalar
- faq: coincide con una FAQ disponible Y confianza >= {CONFIDENCE_THRESHOLD}
- event_inquiry: pregunta sobre un evento próximo específico
- payment_proof: el remitente adjunta o describe un comprobante, constancia, recibo o confirmación de pago de una cuota o membresía. Puede incluir imágenes adjuntas, capturas de transferencia, o texto indicando que realizó un pago.
- clarification_needed: tema conocido pero ambiguo, falta info, se puede preguntar
- unknown: tema que necesita atención humana"""

    content_blocks = _build_content(prompt, email_data.get("images", []))
    response = _client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        messages=[{"role": "user", "content": content_blocks}],
    )

    raw = response.content[0].text.strip()
    # Strip markdown code fences if present (e.g. ```json ... ```)
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1)
    result = json.loads(raw)

    if result["type"] == "faq" and result.get("faq_id"):
        matched = next((f for f in active_faqs if str(f["_id"]) == result["faq_id"]), None)
        if matched:
            result["faq_answer"] = matched["answer"]
        else:
            result["type"] = "unknown"
            result["faq_id"] = None

    return result


def _strip_markdown(text: str) -> str:
    """Elimina símbolos markdown que se verían mal en email de texto plano."""
    import re
    text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)  # # Títulos
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)                # **negrita**
    text = re.sub(r'\*(.+?)\*', r'\1', text)                    # *cursiva*
    text = re.sub(r'_{1,2}(.+?)_{1,2}', r'\1', text)            # _cursiva_
    text = re.sub(r'^-{3,}\s*$', '', text, flags=re.MULTILINE)  # --- separadores
    text = re.sub(r'^={3,}\s*$', '', text, flags=re.MULTILINE)  # === separadores
    text = re.sub(r'`(.+?)`', r'\1', text)                      # `código`
    text = re.sub(r'\n{3,}', '\n\n', text)                      # saltos excesivos
    return text.strip()


def generate_faq_response(email_data: dict, faq: dict) -> str:
    from knowledge_base.faq_loader import get_tone_summary
    sender_name = email_data["from"].split("<")[0].strip() or "estimado/a"
    tone = get_tone_summary()

    prompt = f"""Redacta una respuesta de email profesional y amable para IPIDET.

Remitente: {sender_name}
Su consulta: {email_data['subject']} — {email_data['body'][:400]}

Respuesta base (FAQ):
{faq['answer']}

Guía de estilo:
{tone}

Reglas adicionales:
- Saluda por nombre
- No inventes información fuera de la FAQ
- Ofrécete a resolver dudas adicionales
- Máximo 200 palabras
- TEXTO PLANO únicamente: sin asteriscos, sin almohadillas, sin markdown de ningún tipo"""

    content_blocks = _build_content(prompt, email_data.get("images", []))
    response = _client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        messages=[{"role": "user", "content": content_blocks}],
    )
    return _strip_markdown(response.content[0].text)


def generate_clarification_email(email_data: dict, question: str) -> str:
    sender_name = email_data["from"].split("<")[0].strip() or "estimado/a"

    prompt = f"""Redacta un email profesional para pedir aclaración a un cliente de IPIDET.

Remitente: {sender_name}
Consulta original: {email_data['subject']}
Pregunta de aclaración: {question}

Reglas:
- Tono amable y profesional
- Explica brevemente que necesitas más detalle para ayudarle mejor
- Haz UNA sola pregunta clara
- Máximo 100 palabras
- TEXTO PLANO únicamente: sin asteriscos, sin almohadillas, sin guiones como separadores, sin markdown de ningún tipo"""

    response = _client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    return _strip_markdown(response.content[0].text)


def regenerate_response(email_data: dict, current_response: str, instruction: str) -> str:
    """Regenera la respuesta incorporando la instrucción del administrador."""
    sender_name = email_data["from"].split("<")[0].strip() or "estimado/a"

    prompt = f"""Tienes una respuesta de email para IPIDET que el administrador quiere mejorar.

Remitente: {sender_name}
Asunto: {email_data['subject']}
Correo recibido: {email_data['body'][:400]}

Respuesta actual:
{current_response}

Instrucción del administrador:
{instruction}

Reescribe la respuesta aplicando exactamente la instrucción.
- TEXTO PLANO únicamente: sin asteriscos, sin almohadillas, sin markdown
- Máximo 200 palabras"""

    content_blocks = _build_content(prompt, email_data.get("images", []))
    response = _client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        messages=[{"role": "user", "content": content_blocks}],
    )
    return _strip_markdown(response.content[0].text)


def generate_event_response(email_data: dict, event: dict) -> str:
    sender_name = email_data["from"].split("<")[0].strip() or "estimado/a"
    deadline = event.get("registration_deadline")
    deadline_str = deadline.strftime("%d/%m/%Y") if deadline else "consultar"
    date_str = event["date"].strftime("%d/%m/%Y") if event.get("date") else "por confirmar"

    prompt = f"""Redacta una respuesta de email profesional y amable para IPIDET sobre un evento.

Remitente: {sender_name}
Su consulta: {email_data['subject']} — {email_data['body'][:300]}

Información del evento:
- Nombre: {event['name']}
- Descripción: {event['description']}
- Fecha: {date_str}
- Lugar: {event['location']}
- Precio: {event['price']}
- Cupos: {event.get('spots', 'limitados')}
- Fecha límite de inscripción: {deadline_str}
- Formulario de inscripción: {event.get('registration_url', 'próximamente')}

Reglas:
- Saluda por nombre
- Menciona todos los datos relevantes del evento
- Incluye el link del formulario con llamada a acción clara
- Indica que se adjunta el flyer con más detalles (si corresponde)
- TEXTO PLANO únicamente: sin asteriscos, sin almohadillas, sin markdown
- Máximo 250 palabras"""

    response = _client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )
    return _strip_markdown(response.content[0].text)
