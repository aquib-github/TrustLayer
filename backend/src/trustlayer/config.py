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

    # ── Gemini API (claim extraction) ─────────────────────────────────────
    gemini_api_key: str = ""

    # ── Hugging Face API Token (model downloads: BGE-M3, DeBERTa-v3) ──────
    hf_token: str = ""

    # ── Hugging Face model cache directory (local to project) ──────────────
    hf_home: str = ""


# Singleton – import this wherever settings are needed.
settings = Settings()

# ── Configure Hugging Face environment variables early ───────────────────
import os

if settings.hf_token:
    os.environ["HF_TOKEN"] = settings.hf_token
    os.environ["HUGGING_FACE_HUB_TOKEN"] = settings.hf_token

# Always ensure HF models cache into project directory (backend/models_cache by default)
hf_cache_dir = settings.hf_home.strip() if settings.hf_home else "./models_cache"
cache_path = Path(hf_cache_dir)
if not cache_path.is_absolute():
    cache_path = (_ENV_FILE.parent / cache_path).resolve()
else:
    cache_path = cache_path.resolve()

cache_path.mkdir(parents=True, exist_ok=True)
os.environ["HF_HOME"] = str(cache_path)
os.environ["HF_HUB_CACHE"] = str(cache_path / "hub")

