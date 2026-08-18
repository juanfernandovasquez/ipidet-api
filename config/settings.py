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
GOOGLE_CREDENTIALS_PATH = os.getenv("GOOGLE_CREDENTIALS_PATH", "google_credentials.json")
BILLING_SHEET_ID = os.getenv("BILLING_SHEET_ID", "")
BILLING_REMINDER_DAYS = int(os.getenv("BILLING_REMINDER_DAYS", "7"))
BILLING_OVERDUE_ALERT_HOURS = int(os.getenv("BILLING_OVERDUE_ALERT_HOURS", "24"))
BILLING_CHECK_INTERVAL_HOURS = int(os.getenv("BILLING_CHECK_INTERVAL_HOURS", "6"))

# Auth
SECRET_KEY  = os.getenv("SECRET_KEY", "cambia-esto-en-produccion")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

# Portal de socios
PORTAL_SECRET = os.getenv("PORTAL_SECRET", "")          # secret para server-to-server WP→FastAPI
WC_WEBHOOK_SECRET = os.getenv("WC_WEBHOOK_SECRET", "")  # secret del webhook WooCommerce
PORTAL_API_BASE = os.getenv("PORTAL_API_BASE", "http://localhost:8000")  # URL pública de FastAPI

CRITICAL_KEYWORDS = [
    "amenaza legal", "demanda", "abogado", "tribunal",
    "prensa", "medios", "periodista", "queja formal",
    "reembolso urgente", "autoridad regulatoria", "denuncia",
    "incidente de seguridad", "fraude",
]
