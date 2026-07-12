"""Runtime configuration, read from environment (with a .env fallback).

Every integration degrades gracefully: if its keys are unset, that feature is
simply disabled and the rest of the app keeps working.
"""
from __future__ import annotations

import os
from functools import lru_cache

try:  # optional: load a local .env for convenience
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional
    pass


class Settings:
    def __init__(self) -> None:
        # storage
        self.database_url = os.getenv("DATABASE_URL", "sqlite:///./leadsystem.db")

        # branding / offer
        self.public_base_url = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")
        self.brand_name = os.getenv("BRAND_NAME", "Your Brand")
        self.product = os.getenv("PRODUCT", "your offer")
        self.lead_magnet = os.getenv("LEAD_MAGNET", "a free resource")

        # AI layer
        self.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", "")
        self.haiku_model = os.getenv("HAIKU_MODEL", "claude-haiku-4-5")
        self.copy_model = os.getenv("COPY_MODEL", "claude-sonnet-5")

        # discovery
        self.youtube_api_key = os.getenv("YOUTUBE_API_KEY", "")
        self.apify_token = os.getenv("APIFY_TOKEN", "")

        # email (Listmonk)
        self.listmonk_url = os.getenv("LISTMONK_URL", "http://localhost:9000").rstrip("/")
        self.listmonk_user = os.getenv("LISTMONK_USER", "")
        self.listmonk_password = os.getenv("LISTMONK_PASSWORD", "")
        self.listmonk_list_id = _int(os.getenv("LISTMONK_LIST_ID", "0"))

        # sheets
        self.google_sa_json = os.getenv("GOOGLE_SA_JSON", "")
        self.sheet_id = os.getenv("SHEET_ID", "")

    # --- capability flags (used to show status in the dashboard) ---
    @property
    def ai_enabled(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def youtube_enabled(self) -> bool:
        return bool(self.youtube_api_key)

    @property
    def apify_enabled(self) -> bool:
        return bool(self.apify_token)

    @property
    def listmonk_enabled(self) -> bool:
        return bool(self.listmonk_user and self.listmonk_password)

    @property
    def sheets_enabled(self) -> bool:
        return bool(self.google_sa_json and self.sheet_id)

    def capabilities(self) -> dict[str, bool]:
        return {
            "AI (Anthropic)": self.ai_enabled,
            "YouTube discovery": self.youtube_enabled,
            "Apify (IG/TikTok)": self.apify_enabled,
            "Email (Listmonk)": self.listmonk_enabled,
            "Google Sheets sync": self.sheets_enabled,
        }


def _int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


@lru_cache
def get_settings() -> Settings:
    return Settings()
