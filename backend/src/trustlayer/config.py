"""
Application settings loaded from environment variables via pydantic-settings.

Reads DATABASE_URL (and optionally GEMINI_API_KEY) from the .env file located
in the backend/ directory.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


# Resolve the .env file relative to this file's location:
# config.py -> src/trustlayer/ ... backend/.env
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    """Central configuration – values come from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Database ──────────────────────────────────────────────────────────
    database_url: str  # e.g. postgresql+asyncpg://user:pass@host:port/db

    # ── Gemini API (claim extraction – used by other modules later) ──────
    gemini_api_key: str = ""


# Singleton – import this wherever settings are needed.
settings = Settings()
