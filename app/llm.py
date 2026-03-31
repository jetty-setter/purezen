from __future__ import annotations

import logging
from typing import Optional

import requests

from app.config import OLLAMA_MODEL, OLLAMA_URL

log = logging.getLogger(__name__)

OLLAMA_TIMEOUT = 120


def call_ollama(prompt: str, system: Optional[str] = None) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.1},
    }
    if system:
        payload["system"] = system

    try:
        response = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json=payload,
            timeout=OLLAMA_TIMEOUT,
        )
        response.raise_for_status()
        return (response.json().get("response") or "").strip()
    except requests.RequestException as exc:
        log.error("Ollama request failed: %s", exc)
        raise
