"""
Minimal connectivity check for TrustLayer.

1. Hugging Face connectivity and model loading with HF_TOKEN.
2. Gemini API connectivity with GEMINI_API_KEY.
"""

import os
import requests
from trustlayer.config import settings
from transformers import AutoTokenizer


def check_huggingface() -> None:
    print("\n--- 1. Hugging Face Model Connectivity Check ---")
    token = settings.hf_token
    print(f"HF_TOKEN present: {bool(token)} (masked: {token[:4]}...{token[-4:] if len(token) > 8 else '***'})")
    print("Loading DeBERTa-v3 tokenizer from Hugging Face Hub...")
    tokenizer = AutoTokenizer.from_pretrained(
        "cross-encoder/nli-deberta-v3-small",
        token=token,
    )
    tokens = tokenizer("TrustLayer dual-grounding test", return_tensors="pt")
    print(f"SUCCESS: Loaded {tokenizer.__class__.__name__}")
    print(f"Input IDs shape: {tokens['input_ids'].shape}")
    print("Sample Token IDs:", tokens["input_ids"][0].tolist())


def check_gemini() -> None:
    print("\n--- 2. Gemini API Connectivity Check ---")
    key = settings.gemini_api_key
    print(f"GEMINI_API_KEY present: {bool(key)} (masked: {key[:4]}...{key[-4:] if len(key) > 8 else '***'})")
    
    model_name = "models/gemini-3.8-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/{model_name}:generateContent?key={key}"
    payload = {
        "contents": [{"parts": [{"text": "Reply with only 'TrustLayer Gemini connectivity verified.'"}]}]
    }

    print(f"Sending request to: {model_name}...")
    try:
        response = requests.post(url, json=payload, timeout=5)
        print(f"HTTP Status Code: {response.status_code}")
        if response.status_code == 200:
            result = response.json()
            reply = result["candidates"][0]["content"]["parts"][0]["text"].strip()
            print(f"SUCCESS: Gemini response received -> '{reply}'")
        else:
            err = response.json().get("error", {})
            print(f"Gemini API Response: {err.get('status')} ({response.status_code})")
            print(f"Message: {err.get('message')}")
    except Exception as exc:
        print(f"Connection Exception: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    check_huggingface()
    check_gemini()
