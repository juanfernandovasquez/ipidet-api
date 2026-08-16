"""
Carga los FAQs desde los archivos YAML y los sincroniza a MongoDB.
Se ejecuta al arrancar el bot. Los archivos YAML son la fuente de verdad.

Para agregar o editar FAQs: edita los archivos en knowledge_base/faqs/*.yaml
Para ver el tono y estilo: edita knowledge_base/tone_guide.yaml
Para ver plantillas: edita knowledge_base/response_templates.yaml
"""
import os
import yaml
from knowledge_base.db import faqs as faqs_col

FAQS_DIR = os.path.join(os.path.dirname(__file__), "faqs")
TONE_FILE = os.path.join(os.path.dirname(__file__), "tone_guide.yaml")
TEMPLATES_FILE = os.path.join(os.path.dirname(__file__), "response_templates.yaml")

_tone_cache: dict = {}
_templates_cache: dict = {}


def sync_faqs_to_db():
    """Lee todos los YAML de faqs/ y hace upsert en MongoDB por ID."""
    total_synced = 0
    for filename in os.listdir(FAQS_DIR):
        if not filename.endswith(".yaml"):
            continue
        path = os.path.join(FAQS_DIR, filename)
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        for faq in data.get("faqs", []):
            faq_id = faq.get("id")
            if not faq_id:
                continue
            faqs_col.update_one(
                {"yaml_id": faq_id},
                {"$set": {
                    "yaml_id": faq_id,
                    "question": faq["question"],
                    "answer": faq["answer"].strip(),
                    "category": filename.replace(".yaml", ""),
                    "active": faq.get("active", True),
                    "auto_approve": faq.get("auto_approve", False),
                    "notes": faq.get("notes", ""),
                    "source": "yaml",
                    "times_used": faqs_col.find_one({"yaml_id": faq_id}, {"times_used": 1}) and
                                  faqs_col.find_one({"yaml_id": faq_id}).get("times_used", 0) or 0,
                }},
                upsert=True,
            )
            total_synced += 1
    return total_synced


def load_tone_guide() -> dict:
    global _tone_cache
    if _tone_cache:
        return _tone_cache
    try:
        with open(TONE_FILE, encoding="utf-8") as f:
            _tone_cache = yaml.safe_load(f) or {}
    except Exception:
        _tone_cache = {}
    return _tone_cache


def load_templates() -> dict:
    global _templates_cache
    if _templates_cache:
        return _templates_cache
    try:
        with open(TEMPLATES_FILE, encoding="utf-8") as f:
            _templates_cache = yaml.safe_load(f) or {}
    except Exception:
        _templates_cache = {}
    return _templates_cache


def get_tone_summary() -> str:
    """Devuelve un resumen del tono y estilo para incluir en prompts de Claude."""
    tone = load_tone_guide()
    if not tone:
        return ""
    lines = []
    if tone.get("tono", {}).get("descripcion"):
        lines.append(f"Tono: {tone['tono']['descripcion']}")
    if tone.get("evitar"):
        lines.append("Evitar: " + ", ".join(str(x) for x in tone["evitar"]))
    if tone.get("siempre"):
        lines.append("Siempre: " + " | ".join(tone["siempre"]))
    return "\n".join(lines)


def export_db_faqs_to_yaml():
    """Exporta FAQs de MongoDB que NO vienen de YAML (source != yaml) a un archivo de revisión."""
    import datetime
    non_yaml = list(faqs_col.find({"source": {"$ne": "yaml"}, "active": True}))
    if not non_yaml:
        print("No hay FAQs fuera de YAML para exportar.")
        return
    output = {"faqs": []}
    for f in non_yaml:
        output["faqs"].append({
            "id": f"importada_{str(f['_id'])}",
            "question": f.get("question", ""),
            "answer": f.get("answer", ""),
            "active": f.get("active", True),
            "auto_approve": False,
            "notes": f"Importada desde MongoDB. Source original: {f.get('source', '?')}",
        })
    out_path = os.path.join(FAQS_DIR, f"_importadas_{datetime.date.today()}.yaml")
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.dump(output, f, allow_unicode=True, default_flow_style=False)
    print(f"Exportadas {len(non_yaml)} FAQs a {out_path}")
    print("Revisalas, asignales un ID limpio y muévelas a la categoría correcta.")
