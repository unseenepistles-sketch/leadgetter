"""Central configuration, loaded from environment / .env.

Nothing here is required. Every integration degrades gracefully when its
credentials are absent, so the app is fully runnable out of the box.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # AI layer (open-source, OpenAI-compatible)
    llm_provider: str = os.getenv("LLM_PROVIDER", "ollama")
    llm_model: str = os.getenv("LLM_MODEL", "llama3.1:8b")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "http://localhost:11434/v1")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")

    # Creator discovery
    apify_token: str = os.getenv("APIFY_TOKEN", "")
    apify_creator_actor: str = os.getenv("APIFY_CREATOR_ACTOR", "apify/instagram-search-scraper")

    # Google Sheets sync
    sheets_enabled: bool = _bool("SHEETS_ENABLED", False)
    sheets_spreadsheet_id: str = os.getenv("SHEETS_SPREADSHEET_ID", "")
    google_service_account_json: str = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "./service-account.json")

    # Email (SMTP)
    smtp_host: str = os.getenv("SMTP_HOST", "")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587") or "587")
    smtp_user: str = os.getenv("SMTP_USER", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    smtp_from: str = os.getenv("SMTP_FROM", "you@example.com")
    smtp_from_name: str = os.getenv("SMTP_FROM_NAME", "LeadSystem")

    # App
    app_base_url: str = os.getenv("APP_BASE_URL", "http://localhost:8020").rstrip("/")
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./data/leadsystem.db")

    # Derived capability flags
    @property
    def ai_configured(self) -> bool:
        # Ollama needs no key; hosted providers do.
        if self.llm_provider == "ollama":
            return bool(self.llm_base_url)
        return bool(self.llm_api_key and self.llm_base_url)

    @property
    def discovery_live(self) -> bool:
        return bool(self.apify_token)

    @property
    def email_live(self) -> bool:
        return bool(self.smtp_host and self.smtp_user and self.smtp_password)


@lru_cache
def get_settings() -> Settings:
    return Settings()
