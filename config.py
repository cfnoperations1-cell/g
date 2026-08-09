"""Shared configuration for the scraper agent and CRM app.

All values are read from environment variables (see .env.example), loaded
here via python-dotenv so both the CLI scraper and the Flask app see the
same settings without duplicating setup code.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

DATABASE_URL = os.environ.get("DATABASE_URL") or f"sqlite:///{DATA_DIR / 'leads.db'}"

# Search-API providers (find candidate company websites via web search)
GOOGLE_CSE_API_KEY = os.environ.get("GOOGLE_CSE_API_KEY", "")
GOOGLE_CSE_CX = os.environ.get("GOOGLE_CSE_CX", "")
BING_SEARCH_API_KEY = os.environ.get("BING_SEARCH_API_KEY", "")
SERPER_API_KEY = os.environ.get("SERPER_API_KEY", "")
BRAVE_SEARCH_API_KEY = os.environ.get("BRAVE_SEARCH_API_KEY", "")

# Directory-API provider (find candidate companies via a business directory)
GOOGLE_PLACES_API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "")

FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")

# --- Outreach / email ---
SENDER_NAME = os.environ.get("SENDER_NAME", "Jonathan Cole")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "")
SENDER_COMPANY = os.environ.get("SENDER_COMPANY", "")
# CAN-SPAM requires a valid physical postal address and a working opt-out in
# every commercial email. Both are rendered into the message footer.
SENDER_POSTAL_ADDRESS = os.environ.get("SENDER_POSTAL_ADDRESS", "")
UNSUBSCRIBE_LINE = os.environ.get(
    "UNSUBSCRIBE_LINE",
    "Don't want to hear from us again? Reply with \"unsubscribe\" and we'll remove you.",
)

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")

# Seconds to wait between messages, so a batch doesn't look like a burst.
EMAIL_DELAY_SECONDS = float(os.environ.get("EMAIL_DELAY_SECONDS", "8"))

REQUEST_TIMEOUT_SECONDS = float(os.environ.get("REQUEST_TIMEOUT_SECONDS", "10"))
SCRAPER_USER_AGENT = os.environ.get(
    "SCRAPER_USER_AGENT",
    "PeptideLeadBot/0.1 (+contact: your-email@example.com)",
)
