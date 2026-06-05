from __future__ import annotations
import logging
import os
from typing import Optional
import anthropic

log = logging.getLogger(__name__)

# Model is configurable via env so you can change it without a code change.
LLM_MODEL = os.getenv("LLM_MODEL", "claude-haiku-4-5-20251001")

_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    """Lazily build a single Anthropic client, with a clear error if the
    API key is missing — this is the #1 reason the chat silently degrades."""
    global _client
    if _client is None:
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. The chat LLM cannot run without it. "
                "Set it in the server environment (e.g. export ANTHROPIC_API_KEY=sk-ant-...)."
            )
        _client = anthropic.Anthropic()
    return _client


def call_ollama(prompt: str, system: Optional[str] = None) -> str:
    try:
        client = _get_client()
        kwargs = {
            "model": LLM_MODEL,
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        response = client.messages.create(**kwargs)
        return response.content[0].text.strip()
    except Exception as exc:
        log.error("LLM request failed (model=%s): %s", LLM_MODEL, exc)
        raise
