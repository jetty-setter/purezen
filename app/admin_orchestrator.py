# ============================================================
# admin_orchestrator.py — Intent-driven admin query handler
# ============================================================
# Architecture mirrors the customer chatbot (intent_router → orchestrator):
#   1. classify()  — regex fast-path, LLM fallback — returns structured intent
#   2. _route()    — picks the right tool based on intent, no LLM loop
#   3. _answer()   — single LLM call to summarize tool result as plain text
#
# This replaces the iterative tool-calling loop with deterministic routing,
# which is faster, more predictable, and easier to debug.

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Dict, Optional

import requests as http_requests

from app.admin_tools import execute_tool
from app.admin_intent import classify

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class OrchestratorConfig:
    routing_model: str = "llama3.2:3b"   # Intent classification fallback
    answer_model:  str = "qwen2.5:3b"    # Plain-text answer generation
    timeout:       int = 60
    ollama_url:    str = "http://127.0.0.1:11434"


_config = OrchestratorConfig()


def configure(
    model: str = None,
    answer_model: str = None,
    timeout: int = None,
    ollama_url: str = None,
) -> None:
    if model:        _config.routing_model = model
    if answer_model: _config.answer_model  = answer_model
    if timeout:      _config.timeout       = timeout
    if ollama_url:   _config.ollama_url    = ollama_url


# ---------------------------------------------------------------------------
# LLM communication
# ---------------------------------------------------------------------------

def _call_llm(prompt: str, model: str = None, strict: bool = False) -> str:
    use_model = model or _config.answer_model
    if strict:
        system = (
            "You are a concise administrative assistant for PureZen Spa. "
            "Respond only with factual, professional observations. "
            "Use 2-3 short sentences maximum. "
            "Never ask a question. Never sign off. Never output JSON. "
            "Never mention internal tool names or data field names. "
            "Just state the facts clearly."
        )
        full_prompt = f"{system}\n\n{prompt}"
    else:
        full_prompt = prompt

    try:
        r = http_requests.post(
            f"{_config.ollama_url}/api/generate",
            json={
                "model":   use_model,
                "prompt":  full_prompt,
                "stream":  False,
                "options": {"temperature": 0.1},
            },
            timeout=_config.timeout,
        )
        r.raise_for_status()
        return (r.json().get("response") or "").strip()
    except Exception as exc:
        log.warning("LLM call failed (%s): %s", use_model, exc)
        return ""


def _clean(raw: str) -> str:
    cleaned = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line or line.endswith("?"):
            continue
        lower = line.lower()
        if any(lower.startswith(p) for p in (
            "warm regards", "regards", "sincerely", "best regards",
            "owen", "qwen", "llama", "how can i", "let me know",
            "feel free", "if you", "please let", "i hope",
            "note:", "please note",
        )):
            continue
        cleaned.append(line)
    return " ".join(cleaned).strip()


# ---------------------------------------------------------------------------
# Public llm() — for schedule summaries, conflicts, narrative in admin_routes
# ---------------------------------------------------------------------------

def llm(prompt: str) -> str:
    raw    = _call_llm(prompt, model=_config.answer_model, strict=True)
    result = _clean(raw)
    return result or "No summary available."


# ---------------------------------------------------------------------------
# Routing — deterministic intent → tool mapping
# ---------------------------------------------------------------------------

def _route(intent: Dict[str, Any], data_fns: Dict[str, Callable]) -> str:
    kind      = intent["intent"]
    date      = intent.get("date")
    date_from = intent.get("date_from")
    date_to   = intent.get("date_to")
    email     = intent.get("email")
    today     = datetime.utcnow().date().isoformat()

    if kind == "staff_query":
        return execute_tool("get_staff_roster", {}, data_fns)

    elif kind == "schedule_query":
        return execute_tool("get_bookings_by_date", {"date": date or today}, data_fns)

    elif kind == "trends_query":
        params = {}
        if date_from: params["date_from"] = date_from
        if date_to:   params["date_to"]   = date_to
        return execute_tool("get_trends", params, data_fns)

    elif kind == "customer_query":
        if email:
            return execute_tool("get_customer_history", {"email": email}, data_fns)
        return execute_tool("get_all_bookings", {}, data_fns)

    elif kind == "upcoming_query":
        return execute_tool("get_upcoming_bookings", {"limit": 10}, data_fns)

    elif kind == "range_query":
        params = {}
        if date_from: params["date_from"] = date_from
        if date_to:   params["date_to"]   = date_to
        return execute_tool("get_bookings_range", params, data_fns)

    else:
        return execute_tool("get_all_bookings", {}, data_fns)


# ---------------------------------------------------------------------------
# Deterministic formatters — no LLM needed for structured data
# ---------------------------------------------------------------------------

def _format_staff(data: str) -> Optional[str]:
    """Format staff roster without LLM."""
    try:
        staff = __import__("json").loads(data)
        if not staff:
            return "No active staff members found."
        active = [s for s in staff if s.get("active", True)]
        names  = [s.get("name", "Unknown") for s in active]
        if not names:
            return "No active staff members found."
        if len(names) == 1:
            return f"{names[0]} is the active staff member on the roster."
        return f"The active staff are: {', '.join(names[:-1])}, and {names[-1]}."
    except Exception:
        return None


def _format_trends(data: str, question: str) -> Optional[str]:
    """Format trends/statistics without LLM."""
    try:
        import json as _json
        t = _json.loads(data)
        q = question.lower()

        by_staff   = t.get("by_staff", {})
        by_service = t.get("by_service", {})
        total      = t.get("total_bookings", 0)
        cancelled  = t.get("total_cancelled", 0)
        rate       = t.get("cancellation_rate", 0)

        # Cancellation questions
        if any(w in q for w in ("cancel", "cancellation")):
            if by_service:
                top_svc  = next(iter(by_service))
                # Find cancelled by service from raw — use totals as proxy
                return (
                    f"Based on overall booking data, {top_svc} has the highest booking volume "
                    f"with a system-wide cancellation rate of {rate}% "
                    f"({cancelled} of {total + cancelled} total bookings cancelled)."
                )

        # Busiest staff / most bookings
        if any(w in q for w in ("most bookings", "busiest", "busiest staff", "most popular staff")):
            if by_staff:
                top      = next(iter(by_staff))
                top_count = by_staff[top]
                pct      = round(top_count / max(total, 1) * 100, 1)
                return f"{top} has the most bookings with {top_count} total, representing {pct}% of all appointments."

        # Most popular service
        if any(w in q for w in ("popular service", "most booked service", "top service")):
            if by_service:
                top      = next(iter(by_service))
                top_count = by_service[top]
                return f"{top} is the most booked service with {top_count} appointments."

        # General stats
        return (
            f"PureZen has {total} total bookings with a {rate}% cancellation rate. "
            f"The most booked service is {next(iter(by_service), 'N/A')} "
            f"and the busiest staff member is {next(iter(by_staff), 'N/A')}."
        )
    except Exception:
        return None


def _format_schedule(data: str, date: Optional[str]) -> Optional[str]:
    """Format daily schedule without LLM."""
    try:
        import json as _json
        bookings = _json.loads(data)
        date_label = date or "that date"
        try:
            from datetime import datetime as _dt
            date_label = _dt.strptime(date, "%Y-%m-%d").strftime("%B %-d")
        except Exception:
            pass

        if not bookings:
            return f"There are no bookings scheduled for {date_label}."

        upcoming  = [b for b in bookings if b.get("status") == "Upcoming"]
        completed = [b for b in bookings if b.get("status") == "Completed"]
        cancelled = [b for b in bookings if b.get("status") == "Cancelled"]

        total = len(bookings)
        parts = [f"There are {total} appointment{'s' if total != 1 else ''} on {date_label}"]
        if upcoming:
            parts.append(f"{len(upcoming)} upcoming")
        if completed:
            parts.append(f"{len(completed)} completed")
        if cancelled:
            parts.append(f"{len(cancelled)} cancelled")

        staff = list({b.get("staff_name") for b in bookings if b.get("staff_name")})
        if staff:
            parts.append(f"Staff on duty: {', '.join(sorted(staff))}")

        return ". ".join(parts) + "."
    except Exception:
        return None


def _format_upcoming(data: str) -> Optional[str]:
    """Format upcoming bookings without LLM."""
    try:
        import json as _json
        bookings = _json.loads(data)
        if not bookings:
            return "There are no upcoming appointments."
        count = len(bookings)
        next_b = bookings[0]
        service = next_b.get("service_name", "an appointment")
        date    = next_b.get("date_display") or next_b.get("date", "")
        time    = next_b.get("start_time", "")
        staff   = next_b.get("staff_name", "")
        next_str = f"{service} on {date} at {time}"
        if staff:
            next_str += f" with {staff}"
        return f"There are {count} upcoming appointment{'s' if count != 1 else ''}. Next up: {next_str}."
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Answer generation — deterministic first, LLM fallback
# ---------------------------------------------------------------------------

def _answer(question: str, tool_result: str, intent: Dict[str, Any]) -> str:
    """
    Generate a plain-text answer from tool data.
    Uses deterministic formatters for structured intents — no LLM needed.
    Falls back to LLM only for general/customer queries.
    """
    kind = intent.get("intent", "general")

    # Deterministic formatters — instant, no LLM call
    formatted = None
    if kind == "staff_query":
        formatted = _format_staff(tool_result)
    elif kind == "trends_query":
        formatted = _format_trends(tool_result, question)
    elif kind == "schedule_query":
        formatted = _format_schedule(tool_result, intent.get("date"))
    elif kind == "upcoming_query":
        formatted = _format_upcoming(tool_result)

    if formatted:
        log.info("Deterministic answer for intent=%s", kind)
        return formatted

    # LLM fallback for general/customer/range queries
    log.info("LLM answer for intent=%s", kind)
    data   = tool_result if len(tool_result) <= 2000 else tool_result[:2000] + "... [truncated]"
    prompt = (
        f"Data:\n{data}\n\n"
        f"Question: {question}\n\n"
        "Answer in 2-3 sentences. Use only the data above. "
        "State facts directly. No JSON. No questions. No sign-off."
    )
    raw    = _call_llm(prompt, model=_config.answer_model, strict=True)
    result = _clean(raw)
    return result or "I could not find a complete answer."


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def orchestrate(question: str, data_fns: Dict[str, Callable]) -> str:
    """
    Handle an admin natural language query.

    Flow:
      1. classify() — regex fast-path (no LLM), LLM fallback only if needed
      2. _route()   — deterministic tool selection, no iteration
      3. _answer()  — single LLM call to summarize result as plain text

    Typical: 1 LLM call (regex classifies + answer).
    Worst case: 2 LLM calls (LLM classifies + answer).
    Previous loop: 2-4 LLM calls every time.
    """
    intent = classify(
        question,
        llm_fn=lambda p: _call_llm(p, model=_config.routing_model, strict=False),
    )

    log.info(
        "Admin query: %r → intent=%s date=%s range=%s/%s email=%s",
        question,
        intent["intent"],
        intent.get("date"),
        intent.get("date_from"),
        intent.get("date_to"),
        intent.get("email"),
    )

    tool_result = _route(intent, data_fns)
    return _answer(question, tool_result, intent)