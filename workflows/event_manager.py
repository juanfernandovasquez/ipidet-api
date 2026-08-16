"""Gestión de eventos via Telegram.

Comandos:
  /nuevo_evento [texto libre con info del evento]
  /listar_eventos
  /cancelar_evento <id>

Flujo de creación:
  1. /nuevo_evento + pegar texto del flyer (o sin texto para modo paso a paso)
  2. Claude extrae los campos que puede del texto
  3. El bot solo pregunta por los campos que falten
  4. Enviar foto = flyer adjunto (opcional)
"""
import os
import json
import re
from datetime import datetime, timezone
from knowledge_base import db
from telegram_bot import notifications

FLYERS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "flyers")

# Campos requeridos y sus preguntas
_FIELDS = [
    ("name",                  "Nombre del evento:"),
    ("description",           "Descripcion breve del evento:"),
    ("date",                  "Fecha y hora (DD/MM/YYYY HH:MM):"),
    ("location",              "Lugar:"),
    ("price",                 "Precio (ej: S/ 150 o Gratuito):"),
    ("spots",                 "Cupos disponibles (numero):"),
    ("registration_deadline", "Fecha limite de inscripcion (DD/MM/YYYY):"),
    ("registration_url",      "Link del formulario de inscripcion:"),
]

# Estado en memoria (se reinicia con el agente)
_pending_event: dict = {}


def handle_command(text: str, photo_data: bytes = None) -> bool:
    """Retorna True si el mensaje fue procesado como comando de eventos."""
    global _pending_event

    # Foto recibida → guardar como flyer si hay evento en progreso
    text = (text or "").strip()

    if photo_data:
        # Foto + cualquier caption → si estamos esperando flyer+copy, procesar caption como texto del evento
        if _pending_event.get("awaiting_flyer_with_copy"):
            _start_event_creation(text, photo_data=photo_data)
            return True
        # Foto + caption que empieza con /nuevo_evento (modo avanzado)
        if text.lower().startswith("/nuevo_evento"):
            body = text.split(None, 1)[1].strip() if len(text.split(None, 1)) > 1 else ""
            _start_event_creation(body, photo_data=photo_data)
            return True
        # Foto sola → flyer del evento en progreso
        if _pending_event.get("awaiting_flyer"):
            _save_flyer_and_finish(photo_data)
            return True
        return False

    # /sin_flyer durante espera de imagen
    if text.lower() == "/sin_flyer" and _pending_event.get("awaiting_flyer"):
        _finish_event_creation()
        return True

    # Respuesta a un campo pendiente
    if _pending_event.get("missing_fields") and not text.startswith("/"):
        _handle_missing_field(text)
        return True

    if not text.startswith("/"):
        return False

    parts = text.split(None, 1)
    cmd = parts[0].lower()
    body = parts[1].strip() if len(parts) > 1 else ""

    if cmd == "/nuevo_evento":
        if body:
            # Texto pegado directamente en el comando (sin foto)
            _start_event_creation(body)
        else:
            # Flujo principal: pedir flyer + copy en un solo mensaje
            _pending_event = {"awaiting_flyer_with_copy": True}
            notifications._send(
                "Listo. Envia el flyer con todo el copy del evento como caption de la imagen.\n\n"
                "Incluye en el texto: nombre, fecha, lugar, precio, cupos, fecha limite de inscripcion y link del formulario."
            )
        return True

    if cmd == "/listar_eventos":
        _list_events()
        return True

    if cmd == "/cancelar_evento":
        _cancel_event(body)
        return True

    if cmd == "/auto_evento":
        _toggle_auto_evento(body)
        return True

    return False


_READABLE = {
    "name": "Nombre",
    "description": "Descripcion",
    "date": "Fecha",
    "location": "Lugar",
    "price": "Precio",
    "spots": "Cupos",
    "registration_deadline": "Fecha limite",
    "registration_url": "Formulario",
}


def _start_event_creation(raw_text: str, photo_data: bytes = None):
    """Extrae campos del texto libre y pregunta solo por los que falten."""
    global _pending_event
    _pending_event = {}

    if raw_text:
        extracted = _extract_fields(raw_text)
        _pending_event.update(extracted)

    # Guardar flyer si ya vino con el mensaje
    if photo_data:
        import time
        filename = f"flyer_{int(time.time())}.jpg"
        path = os.path.join(FLYERS_DIR, filename)
        with open(path, "wb") as f:
            f.write(photo_data)
        _pending_event["flyer_path"] = path

    # Determinar qué falta
    missing = [f for f, _ in _FIELDS if _pending_event.get(f) is None]
    _pending_event["missing_fields"] = missing

    if not missing:
        if photo_data:
            _finish_event_creation()
        else:
            _ask_for_flyer()
    else:
        # Resumen único: qué se encontró y qué falta
        found = [f for f, _ in _FIELDS if _pending_event.get(f) is not None]
        lines = []
        if found:
            lines.append("Encontre:\n" + "\n".join(f"  - {_READABLE[f]}" for f in found))
        lines.append("Necesito completar:\n" + "\n".join(f"  - {_READABLE[f]}" for f in missing))
        _, first_question = next((f, q) for f, q in _FIELDS if f == missing[0])
        lines.append(first_question)
        notifications._send("\n\n".join(lines))


def _extract_fields(text: str) -> dict:
    """Usa Claude para extraer campos del texto libre."""
    import anthropic
    from config.settings import ANTHROPIC_API_KEY

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = f"""Extrae la información de este evento. Devuelve SOLO un JSON con estos campos exactos.
Si un campo no está en el texto, usa null.

Texto del evento:
{text}

JSON a devolver:
{{
  "name": "<nombre del evento o null>",
  "description": "<descripcion breve o null>",
  "date": "<fecha en formato DD/MM/YYYY HH:MM o null>",
  "location": "<lugar o null>",
  "price": "<precio como texto (ej: S/ 150, Gratuito) o null>",
  "spots": <número entero de cupos o null>,
  "registration_deadline": "<fecha límite en DD/MM/YYYY o null>",
  "registration_url": "<URL del formulario o null>"
}}

Devuelve ÚNICAMENTE el JSON, sin explicaciones."""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        if fence:
            raw = fence.group(1)
        data = json.loads(raw)

        # Parsear fecha si vino como string
        if isinstance(data.get("date"), str):
            try:
                data["date"] = datetime.strptime(data["date"], "%d/%m/%Y %H:%M").replace(tzinfo=timezone.utc)
            except ValueError:
                data["date"] = None

        if isinstance(data.get("registration_deadline"), str):
            try:
                data["registration_deadline"] = datetime.strptime(
                    data["registration_deadline"], "%d/%m/%Y"
                ).replace(tzinfo=timezone.utc)
            except ValueError:
                data["registration_deadline"] = None

        return data
    except Exception as e:
        print(f"Error extrayendo campos: {e}")
        return {}


def _handle_missing_field(text: str):
    """Procesa la respuesta del usuario para el siguiente campo faltante."""
    global _pending_event
    missing = _pending_event.get("missing_fields", [])
    if not missing:
        return

    field = missing[0]

    if field == "date":
        try:
            _pending_event["date"] = datetime.strptime(text, "%d/%m/%Y %H:%M").replace(tzinfo=timezone.utc)
        except ValueError:
            notifications._send("Formato invalido. Usa DD/MM/YYYY HH:MM (ej: 15/09/2026 09:00):")
            return
    elif field == "registration_deadline":
        try:
            _pending_event["registration_deadline"] = datetime.strptime(
                text, "%d/%m/%Y"
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            notifications._send("Formato invalido. Usa DD/MM/YYYY (ej: 10/09/2026):")
            return
    elif field == "spots":
        try:
            _pending_event["spots"] = int(text)
        except ValueError:
            notifications._send("Escribe solo el numero de cupos (ej: 50):")
            return
    else:
        _pending_event[field] = text.strip()

    missing.pop(0)
    _pending_event["missing_fields"] = missing

    if missing:
        _, next_question = next((f, q) for f, q in _FIELDS if f == missing[0])
        remaining = len(missing)
        suffix = f" ({remaining} campo{'s' if remaining > 1 else ''} mas)" if remaining > 1 else ""
        notifications._send(f"{next_question}{suffix}")
    elif _pending_event.get("flyer_path"):
        _finish_event_creation()
    else:
        _ask_for_flyer()


def _ask_for_flyer():
    global _pending_event
    _pending_event["awaiting_flyer"] = True
    _pending_event.pop("missing_fields", None)
    name = _pending_event.get("name", "el evento")
    notifications._send(
        f"Perfecto. Toda la info de *{name}* esta lista.\n\n"
        f"Envia el flyer como imagen, o escribe /sin\\_flyer para omitirlo:"
    )


def _save_flyer_and_finish(photo_data: bytes):
    import time
    filename = f"flyer_{int(time.time())}.jpg"
    path = os.path.join(FLYERS_DIR, filename)
    with open(path, "wb") as f:
        f.write(photo_data)
    _pending_event["flyer_path"] = path
    _finish_event_creation()


def _finish_event_creation():
    global _pending_event
    try:
        ev = _pending_event
        event_id = db.save_event(
            name=ev.get("name", ""),
            description=ev.get("description", ""),
            date=ev.get("date"),
            location=ev.get("location", ""),
            price=ev.get("price", ""),
            spots=ev.get("spots", 0),
            registration_deadline=ev.get("registration_deadline"),
            registration_url=ev.get("registration_url", ""),
            flyer_path=ev.get("flyer_path"),
        )
        date_str = ev["date"].strftime("%d/%m/%Y %H:%M") if ev.get("date") else "-"
        deadline_str = ev["registration_deadline"].strftime("%d/%m/%Y") if ev.get("registration_deadline") else "-"
        flyer_line = "Flyer: adjunto" if ev.get("flyer_path") else "Flyer: no adjunto"
        notifications._send(
            f"Evento guardado correctamente.\n\n"
            f"Nombre: {ev.get('name', '-')}\n"
            f"Fecha: {date_str}\n"
            f"Lugar: {ev.get('location', '-')}\n"
            f"Precio: {ev.get('price', '-')}\n"
            f"Cupos: {ev.get('spots', '-')}\n"
            f"Cierre inscripciones: {deadline_str}\n"
            f"Formulario: {ev.get('registration_url', '-')}\n"
            f"{flyer_line}\n"
            f"ID: `{event_id}`"
        )
    except Exception as e:
        notifications._send(f"Error al guardar el evento: {e}")
    finally:
        _pending_event = {}


def _list_events():
    upcoming = db.get_upcoming_events()
    if not upcoming:
        notifications._send("No hay eventos proximos registrados.")
        return
    lines = ["*Eventos proximos:*\n"]
    for e in upcoming:
        date_str = e["date"].strftime("%d/%m/%Y") if e.get("date") else "?"
        flyer = "si" if e.get("flyer_path") else "no"
        auto = "AUTO ✅" if e.get("auto_approve") else "manual"
        lines.append(
            f"*{e['name']}*\n"
            f"Fecha: {date_str} | Lugar: {e['location']}\n"
            f"Precio: {e['price']} | Flyer: {flyer} | Respuesta: {auto}\n"
            f"Form: {e.get('registration_url', '-')}\n"
            f"ID: `{str(e['_id'])}`\n"
        )
    notifications._send("\n".join(lines))


def _cancel_event(event_id: str):
    if not event_id:
        notifications._send("Uso: /cancelar\\_evento <id>")
        return
    try:
        db.deactivate_event(event_id.strip())
        notifications._send(f"Evento desactivado.")
    except Exception as e:
        notifications._send(f"Error: {e}")


def _toggle_auto_evento(body: str):
    """Activa o desactiva la auto-respuesta para un evento específico."""
    parts = body.strip().split()
    if not parts:
        notifications._send(
            "Uso:\n"
            "  /auto\\_evento <id>       — activa auto-respuesta\n"
            "  /auto\\_evento <id> off   — desactiva auto-respuesta\n\n"
            "Usa /listar\\_eventos para ver los IDs."
        )
        return
    event_id = parts[0]
    enable = len(parts) < 2 or parts[1].lower() not in ("off", "0", "false", "no")
    try:
        db.set_event_auto_approve(event_id, enable)
        estado = "ACTIVADA" if enable else "DESACTIVADA"
        umbral = int(__import__("config.settings", fromlist=["AUTO_APPROVE_CONFIDENCE"]).AUTO_APPROVE_CONFIDENCE * 100)
        msg = (
            f"Auto-respuesta {estado} para evento `{event_id}`.\n\n"
        )
        if enable:
            msg += (
                f"A partir de ahora, correos sobre este evento con confianza >= {umbral}% "
                f"se responderan automaticamente sin pedir aprobacion. "
                f"Solo recibiras una notificacion.\n\n"
                f"Para desactivar: /auto\\_evento {event_id} off"
            )
        else:
            msg += "Volvera al flujo normal de aprobacion via Telegram."
        notifications._send(msg)
    except Exception as e:
        notifications._send(f"Error: {e}")
