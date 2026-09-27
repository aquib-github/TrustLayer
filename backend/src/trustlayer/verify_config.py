"""Verification script to confirm .env keys are loaded via config.py."""
import os
from trustlayer.config import settings


def mask_secret(secret: str) -> str:
    if not secret:
        return "<NOT SET>"
    if len(secret) <= 8:
        return secret[:2] + "****"
    return secret[:4] + "...." + secret[-4:]


def main():
    print("=== Configuration & Key Loading Check ===")
    print(f"DATABASE_URL    : {mask_secret(settings.database_url)}")
    print(f"GEMINI_API_KEY  : {mask_secret(settings.gemini_api_key)} (length: {len(settings.gemini_api_key)})")
    print(f"HF_TOKEN        : {mask_secret(settings.hf_token)} (length: {len(settings.hf_token)})")
    print(f"OS env HF_TOKEN : {mask_secret(os.environ.get('HF_TOKEN', ''))}")
    assert settings.gemini_api_key, "GEMINI_API_KEY was not loaded!"
    assert settings.hf_token, "HF_TOKEN was not loaded!"
    assert os.environ.get("HF_TOKEN") == settings.hf_token, "HF_TOKEN was not set in os.environ!"
    print("STATUS: All keys successfully loaded and verified.")


if __name__ == "__main__":
    main()
