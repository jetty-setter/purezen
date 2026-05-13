from __future__ import annotations
import logging
from typing import Optional
import anthropic

log = logging.getLogger(__name__)

def call_ollama(prompt: str, system: Optional[str] = None) -> str:
    try:
        client = anthropic.Anthropic()
        kwargs = {
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        response = client.messages.create(**kwargs)
        return response.content[0].text.strip()
    except Exception as exc:
        log.error("Anthropic request failed: %s", exc)
        raise
