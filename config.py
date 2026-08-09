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

REQUEST_TIMEOUT_SECONDS = float(os.environ.get("REQUEST_TIMEOUT_SECONDS", "10"))
SCRAPER_USER_AGENT = os.environ.get(
    "SCRAPER_USER_AGENT",
    "PeptideLeadBot/0.1 (+contact: your-email@example.com)",
)
