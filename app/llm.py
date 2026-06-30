from __future__ import annotations
import logging
import os
from typing import Optional
import anthropic

log = logging.getLogger(__name__)

# Model is configurable via env so it can change without a code change.
LLM_MODEL = os.getenv("LLM_MODEL", "claude-haiku-4-5-20251001")

_client: Optional[anthropic.Anthropic] = None
_cached_api_key: Optional[str] = None


def _load_api_key() -> str:
    """Load the Anthropic key from env first, then SSM Parameter Store.

    Local development can still use ANTHROPIC_API_KEY. Deployed environments can
    set ANTHROPIC_KEY_PARAM_NAME to a SecureString parameter name so the raw key
    is not copied into source, CDK code, or GitHub Actions logs.
    """
    global _cached_api_key

    if _cached_api_key:
        return _cached_api_key

    env_key = os.getenv("ANTHROPIC_API_KEY")
    if env_key:
        _cached_api_key = env_key
        return env_key

    param_name = os.getenv("ANTHROPIC_KEY_PARAM_NAME")
    if not param_name:
        return ""

    try:
        import boto3

        region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
        ssm = boto3.client("ssm", region_name=region)
        response = ssm.get_parameter(Name=param_name, WithDecryption=True)
        _cached_api_key = response["Parameter"]["Value"]
        os.environ["ANTHROPIC_API_KEY"] = _cached_api_key
        return _cached_api_key
    except Exception as exc:
        log.error("Failed to load Anthropic key from SSM parameter %s: %s", param_name, exc)
        return ""


def _get_client() -> anthropic.Anthropic:
    """Lazily build a single Anthropic client, with a clear error if missing."""
    global _client
    if _client is None:
        if not _load_api_key():
            raise RuntimeError(
                "Anthropic API key is not configured. Set ANTHROPIC_API_KEY for local runs "
                "or ANTHROPIC_KEY_PARAM_NAME for deployed SSM-backed environments."
            )
        _client = anthropic.Anthropic()
    return _client


def call_llm(prompt: str, system: Optional[str] = None) -> str:
    try:
        client = _get_client()
        kwargs = {
            "model": LLM_MODEL,
            # Concierge replies are short; a tighter cap keeps answers concise
            # (more human, less rambly) and avoids the occasional long generation.
            "max_tokens": 600,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        response = client.messages.create(**kwargs)
        return response.content[0].text.strip()
    except Exception as exc:
        log.error("LLM request failed (model=%s): %s", LLM_MODEL, exc)
        raise
