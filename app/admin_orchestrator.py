# ============================================================
# ai_orchestrator.py — MCP-inspired LLM orchestration loop
# ============================================================
# Handles all LLM communication and the tool-calling loop.
# Decoupled from routing and data access — receives data_fns
# as an injected dependency from admin_routes.py.

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional

import requests as http_requests

from app.admin_tools import TOOLS, execute_tool, tool_list_text

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration — set once, used by all LLM calls
# ---------------------------------------------------------------------------

class OrchestratorConfig:
    model:        str = "llama3.2:3b"   # Used for tool selection
    answer_model: str = "qwen2.5:3b"    # Used for final plain-text answer (faster)
    timeout:      int = 60
    ollama_url:   str = "http://127.0.0.1:11434"


_config = OrchestratorConfig()


def configure(model: str = None, timeout: int = None, ollama_url: str = None, answer_model: str = None) -> None:
    """Update orchestrator settings. Called from admin_routes on startup."""
    if model:        _config.model        = model
    if answer_model: _config.answer_model = answer_model
    if timeout:      _config.timeout      = timeout
    if ollama_url:   _config.ollama_url   = ollama_url


# ---------------------------------------------------------------------------
# Core LLM calls
# ---------------------------------------------------------------------------

def _call_llm(prompt: str, strict: bool = False, model: str = None) -> str:
    """
    Make a single LLM call.
    strict=True: adds a system prompt enforcing plain-text, no-tool output.
    model: override the default model (uses _config.model if not specified).
    """
    use_model = model or _config.model
    if strict:
        system = (
            "You are a concise administrative assistant for PureZen Spa. "
            "Respond only with factual, professional observations. "
            "Use 1-3 short sentences maximum. "
            "Never ask a question. Never sign off. Never output JSON. "
            "Never mention yourself or tool names. Just state the facts clearly."
        )
        full_prompt = f"{system}\n\n{prompt}"
    else:
        full_prompt = prompt

    try:
        payload = {
            "model":   use_model,
            "prompt":  full_prompt,
            "stream":  False,
            "options": {"temperature": 0.1},
        }
        r = http_requests.post(
            f"{_config.ollama_url}/api/generate",
            json=payload,
            timeout=_config.timeout,
        )
        r.raise_for_status()
        return (r.json().get("response") or "").strip()
    except Exception as exc:
        log.warning("LLM call failed (%s): %s", use_model, exc)
        return ""


def _clean_response(raw: str) -> str:
    """Strip sign-offs and questions from a plain-text LLM response."""
    cleaned = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line or line.endswith("?"):
            continue
        lower = line.lower()
        if any(lower.startswith(p) for p in (
            "warm regards", "regards", "sincerely", "best regards",
            "owen", "qwen", "how can i", "let me know", "feel free",
            "if you", "please let", "i hope",
        )):
            continue
        cleaned.append(line)
    return " ".join(cleaned).strip()


# ---------------------------------------------------------------------------
# Public API — used by admin_routes.py
# ---------------------------------------------------------------------------

def llm(prompt: str) -> str:
    """Strict LLM call for summaries, conflict checks, and narrative output. Uses faster answer_model."""
    raw = _call_llm(prompt, strict=True, model=_config.answer_model)
    result = _clean_response(raw)
    return result or "No summary available."


def orchestrate(question: str, data_fns: Dict[str, Callable], max_iterations: int = 3) -> str:
    """
    MCP-inspired orchestration loop.

    1. Give the LLM the tool list and the question.
    2. If it responds with a JSON tool call, execute that tool.
    3. Pass the tool result back and ask the LLM to answer in plain text.
    4. If no tool call, treat the response as the final answer.

    data_fns: injected callables from admin_routes — keeps this file
    decoupled from DynamoDB.
    """
    today = __import__("datetime").datetime.utcnow().date().isoformat()

    system = (
        f"You are an administrative assistant for PureZen Spa & Wellness. Today is {today}.\n"
        "You have access to these data tools:\n"
        f"{tool_list_text()}\n\n"
        "RULES:\n"
        "1. If you need data to answer, respond ONLY with a single JSON tool call — nothing else:\n"
        '   {"tool": "tool_name", "params": {"param": "value"}}\n'
        "2. Call only ONE tool per response.\n"
        "3. Never make up data. Only use what the tools return.\n"
        "4. When you have the data, you will be asked to answer in plain text."
    )

    tool_calls_made: List[str] = []

    # Fast-path: route common questions directly to the right tool
    q_lower = question.lower()
    fast_tool = None
    fast_params = {}
    if any(w in q_lower for w in ["most bookings", "busiest staff", "busiest", "most popular staff"]):
        fast_tool = "get_trends"
    elif any(w in q_lower for w in ["cancellation", "cancelled", "most cancelled"]):
        fast_tool = "get_trends"
    elif any(w in q_lower for w in ["working this week", "on the roster", "staff list", "who is working"]):
        fast_tool = "get_staff_roster"
    elif any(w in q_lower for w in ["tomorrow", "today", "schedule for"]):
        import datetime as _dt
        target = (_dt.datetime.utcnow().date() + _dt.timedelta(days=1)).isoformat() if "tomorrow" in q_lower else _dt.datetime.utcnow().date().isoformat()
        fast_tool = "get_bookings_by_date"
        fast_params = {"date": target}

    if fast_tool:
        log.info("Fast-path tool: %s(%s)", fast_tool, fast_params)
        result = execute_tool(fast_tool, fast_params, data_fns)
        if len(result) > 2000:
            result = result[:2000] + "... [truncated]"
        final_prompt = (
            f"Data:\n{result}\n\n"
            f"Question: {question}\n\n"
            "Answer in 2-3 sentences. Facts only. No JSON. No questions. No sign-off. Do not mention tool names."
        )
        answer = _call_llm(final_prompt, strict=True, model=_config.answer_model)
        return _clean_response(answer) or "I could not find a complete answer."

    for iteration in range(max_iterations):
        prompt = f"{system}\n\nQuestion: {question}\n\nResponse:"
        raw    = _call_llm(prompt, strict=False)

        if not raw:
            break

        # Try to parse a tool call from the response
        tool_call = _parse_tool_call(raw)

        if tool_call and "tool" in tool_call:
            tool_name   = tool_call.get("tool", "")
            tool_params = tool_call.get("params", {})

            # Dedup — don't call the same tool twice
            sig = f"{tool_name}:{json.dumps(tool_params, sort_keys=True)}"
            if sig in tool_calls_made:
                break
            tool_calls_made.append(sig)

            log.info("Tool call: %s(%s)", tool_name, tool_params)
            result = execute_tool(tool_name, tool_params, data_fns)

            if len(result) > 2000:
                result = result[:2000] + "... [truncated]"

            # Use faster answer_model for final plain-text response
            final_prompt = (
                f"Data from {tool_name}:\n{result}\n\n"
                f"Question: {question}\n\n"
                "Answer in 2-3 sentences. Facts only. No JSON. No questions. No sign-off. Do not mention tool names."
            )
            answer = _call_llm(final_prompt, strict=True, model=_config.answer_model)
            return _clean_response(answer) or "I could not find a complete answer."

        # No tool call — treat as final answer
        cleaned = _clean_response(raw)
        if cleaned:
            return cleaned

    return "I could not find a complete answer with the available data."


def _parse_tool_call(raw: str) -> Optional[Dict[str, Any]]:
    """Extract and parse the first JSON tool call from a raw LLM response."""
    try:
        cleaned = re.sub(r'```(?:json)?\s*', '', raw).strip()
        # Try direct parse first (model output is clean JSON)
        if cleaned.startswith('{') and '"tool"' in cleaned:
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                pass
        # Fall back to regex extraction
        m = re.search(r'\{[^{}]*"tool"[^{}]*\}', cleaned)
        if m:
            return json.loads(m.group())
    except (json.JSONDecodeError, AttributeError):
        pass
    return None
