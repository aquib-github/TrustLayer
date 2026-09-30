"""
Claim extraction module for TrustLayer (Module 1).

Extracts atomic factual claims from agent response text.
Uses Gemini API with structured JSON output, loaded via pydantic-settings in trustlayer.config.
Implements failure and fallback handling per docs/02 §6:
- ~5-8s timeout per attempt
- Retry on 503 / 429 / timeout with model fallback (gemini-flash-latest -> gemini-flash-lite-latest)
- Deterministic heuristic fallback when API is unavailable.
"""

import json
import logging
import re
from typing import Any
import requests

from trustlayer.config import settings

logger = logging.getLogger(__name__)


def extract_claims_from_text_heuristic(text: str) -> list[str]:
    """
    Fallback deterministic claim extractor if the external API is unreachable or rate-limited.
    Splits text into atomic sentences/clauses containing factual statements.
    Excludes greetings, questions, and imperative action requests (e.g. 'please transfer $25').
    """
    # Imperative action prefixes that represent commands, not verifiable factual claims
    action_prefixes = [
        "hello", "hi", "hey", "how can", "is there", "would you", "could you",
        "thank", "please note", "please transfer", "transfer ", "please send",
        "send ", "pay ", "check ", "show ", "view ", "block ", "freeze ",
        "lock ", "dispute ", "i want to", "i would like to", "can you",
    ]

    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    claims: list[str] = []
    for s in sentences:
        s_clean = s.strip().rstrip(".!?")
        if not s_clean:
            continue
        s_lower = s_clean.lower()
        if any(s_lower.startswith(w) for w in action_prefixes):
            continue
        claims.append(s_clean)
    return claims


def _parse_claims_response(raw_text: str) -> list[str]:
    """Parse JSON or markdown code fenced list from Gemini response."""
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed.get("claims", [])
        elif isinstance(parsed, list):
            res = []
            for item in parsed:
                if isinstance(item, str):
                    res.append(item)
                elif isinstance(item, dict) and "claim" in item:
                    res.append(item["claim"])
            return res
    except Exception:
        pass
    return []


def extract_claims(
    text: str,
    timeout_sec: float = 6.0,
    max_retries: int = 1,
) -> dict[str, Any]:
    """
    Extracts atomic claims from a piece of text using the Gemini API.

    API key is loaded from settings.gemini_api_key (pydantic-settings reading from .env).
    Follows docs/02 §6 failure handling.
    """
    api_key = settings.gemini_api_key

    if not api_key:
        logger.warning("GEMINI_API_KEY is not configured in settings. Using fallback extractor.")
        return {
            "claims": extract_claims_from_text_heuristic(text),
            "source": "fallback",
            "error": "GEMINI_API_KEY is empty",
        }

    models_to_try = ["gemini-flash-latest", "gemini-flash-lite-latest"]

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": (
                            "Extract all factual claims regarding bank policies, fees, limits, rules, procedures, "
                            "or account terms mentioned in the following text. "
                            "Do NOT extract user action requests, transaction commands, recipient names, or "
                            "pleasantries (e.g. 'transfer $25 to Bob' is a command, not a policy claim). "
                            "If the text contains no factual policy claims, return an empty list: {\"claims\": []}.\n"
                            "Output ONLY a valid JSON object in this format:\n"
                            '{"claims": ["claim 1", "claim 2"]}\n\n'
                            f'Text: "{text}"'
                        )
                    }
                ]
            }
        ],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": 0.0,
        },
    }

    last_error: str | None = None

    for model_name in models_to_try:
        endpoint_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        for attempt in range(max_retries + 1):
            try:
                res = requests.post(endpoint_url, json=payload, timeout=timeout_sec)
                if res.status_code == 200:
                    data = res.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        raw_text = candidates[0]["content"]["parts"][0]["text"]
                        claims = _parse_claims_response(raw_text)
                        if claims:
                            return {
                                "claims": claims,
                                "source": f"gemini ({model_name})",
                                "error": None,
                            }
                elif res.status_code in (503, 429):
                    last_error = f"HTTP {res.status_code}: Service Unavailable / Rate Limit on {model_name}"
                    logger.warning("Gemini API %s %s attempt %d: %s", model_name, res.status_code, attempt + 1, last_error)
                else:
                    last_error = f"HTTP {res.status_code}: {res.text[:120]}"
                    logger.warning("Gemini API %s attempt %d failed: %s", model_name, attempt + 1, last_error)
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {str(exc)}"
                logger.warning("Gemini API %s attempt %d error: %s", model_name, attempt + 1, last_error)

    # Fallback when all attempts are exhausted per docs/02 §6
    logger.warning("Gemini API call exhausted all models/retries (%s). Using fallback extractor.", last_error)
    return {
        "claims": extract_claims_from_text_heuristic(text),
        "source": "fallback",
        "error": last_error,
    }
