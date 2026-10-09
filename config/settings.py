import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
DB_NAME = "ipidet_agent"

GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

ADMIN_FORWARD_EMAIL = os.getenv("ADMIN_FORWARD_EMAIL", "administracion@ipidet.org")
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.85"))
AUTO_APPROVE_CONFIDENCE = float(os.getenv("AUTO_APPROVE_CONFIDENCE", "0.88"))
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "60"))
CLARIFICATION_TIMEOUT_HOURS = int(os.getenv("CLARIFICATION_TIMEOUT_HOURS", "24"))

# Cobranzas
BILLING_REMINDER_DAYS = int(os.getenv("BILLING_REMINDER_DAYS", "7"))
BILLING_OVERDUE_ALERT_HOURS = int(os.getenv("BILLING_OVERDUE_ALERT_HOURS", "24"))
BILLING_CHECK_INTERVAL_HOURS = int(os.getenv("BILLING_CHECK_INTERVAL_HOURS", "6"))

# Auth
SECRET_KEY  = os.getenv("SECRET_KEY", "")
_INSECURE_DEFAULTS = {"", "cambia-esto-en-produccion", "secret", "changeme", "dev"}
if SECRET_KEY in _INSECURE_DEFAULTS:
    import sys
    print(
        "ERROR CRÍTICO: SECRET_KEY no está configurado o usa un valor inseguro. "
        "Configura SECRET_KEY en .env antes de arrancar.",
        file=sys.stderr,
    )
    sys.exit(1)
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

# Brevo (correos transaccionales)
BREVO_WEBHOOK_TOKEN = os.getenv("BREVO_WEBHOOK_TOKEN", "")    # token para verificar webhooks de rebotes
BREVO_API_KEY       = os.getenv("BREVO_API_KEY", "")          # preferido: HTTP API (sin restricciones de IP)
BREVO_SMTP_HOST     = os.getenv("BREVO_SMTP_HOST", "smtp-relay.brevo.com")
BREVO_SMTP_PORT     = int(os.getenv("BREVO_SMTP_PORT", "587"))
BREVO_SMTP_USER     = os.getenv("BREVO_SMTP_USER", "")
BREVO_SMTP_PASSWORD = os.getenv("BREVO_SMTP_PASSWORD", "")
BREVO_FROM_EMAIL    = os.getenv("BREVO_FROM_EMAIL", "administracion@ipidet.org")
BREVO_FROM_NAME     = os.getenv("BREVO_FROM_NAME", "IPIDET")

# Portal de socios
PORTAL_SECRET = os.getenv("PORTAL_SECRET", "")          # secret para server-to-server WP→FastAPI
WC_WEBHOOK_SECRET = os.getenv("WC_WEBHOOK_SECRET", "")  # secret del webhook WooCommerce
PORTAL_API_BASE = os.getenv("PORTAL_API_BASE", "http://localhost:8000")  # URL pública de FastAPI

# WooCommerce REST API (solo lectura)
WC_API_KEY    = os.getenv("WC_API_KEY", "")
WC_API_SECRET = os.getenv("WC_API_SECRET", "")
WC_STORE_URL  = os.getenv("WC_STORE_URL", "https://ipidet.org")

# Scheduler de cobranzas
SCHEDULER_ENABLED        = os.getenv("SCHEDULER_ENABLED", "false").lower() == "true"
SCHEDULER_INTERVAL_HOURS = int(os.getenv("SCHEDULER_INTERVAL_HOURS", "24"))
SCHEDULER_PERIODO        = os.getenv("SCHEDULER_PERIODO", "2026")
SCHEDULER_ESTADOS        = os.getenv("SCHEDULER_ESTADOS", "debe,fraccionamiento,parcial").split(",")

# SUNAT / APISPERU — facturación electrónica
APISPERU_TOKEN         = os.getenv("APISPERU_TOKEN", "")          # Bearer token company-level (no expira)
IPIDET_RUC             = os.getenv("IPIDET_RUC", "")              # RUC de IPIDET (20 dígitos)
IPIDET_RAZON_SOCIAL    = os.getenv("IPIDET_RAZON_SOCIAL", "IPIDET")
IPIDET_NOMBRE_COMERCIAL= os.getenv("IPIDET_NOMBRE_COMERCIAL", "IPIDET")
IPIDET_DIRECCION       = os.getenv("IPIDET_DIRECCION", "")
IPIDET_UBIGUEO         = os.getenv("IPIDET_UBIGUEO", "150101")    # ubigeo Lima Centro
SUNAT_ENVIRONMENT      = os.getenv("SUNAT_ENVIRONMENT", "beta")   # "beta" | "produccion"

CRITICAL_KEYWORDS = [
    "amenaza legal", "demanda", "abogado", "tribunal",
    "prensa", "medios", "periodista", "queja formal",
    "reembolso urgente", "autoridad regulatoria", "denuncia",
    "incidente de seguridad", "fraude",
]
