from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from app.llm import call_ollama
from app.prompts import build_intent_prompt

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Service alias table — ordered most-specific to least-specific
# ---------------------------------------------------------------------------

_SERVICE_ALIASES: list[tuple[str, str]] = [
    (r"swedish",              "Swedish Massage"),
    (r"deep\s*tissue",        "Deep Tissue Massage"),
    (r"hot\s*stone",          "Hot Stone Massage"),
    (r"sports\s*massage",     "Sports Massage"),
    (r"prenatal",             "Prenatal Massage"),
    (r"couples",              "Couples Massage"),
    (r"aroma\s*therap\w*",    "Aromatherapy Add-On"),
    (r"aromatherapy",         "Aromatherapy Add-On"),
    (r"massage",              "Swedish Massage"),       # generic → default Swedish
    (r"hydrat\w*\s*deluxe",   "Hydrating Deluxe Facial"),
    (r"hydrat\w*\s*facial",   "Hydrating Deluxe Facial"),
    (r"luminous",             "Hydrating Deluxe Facial"),
    (r"anti.?aging\s*facial", "Anti-Aging Facial"),
    (r"acne\s*facial",        "Acne Facial"),
    (r"classic\s*facial",     "Classic Facial"),
    (r"facial",               "Classic Facial"),       # generic → default Classic Facial
    (r"sea\s*salt",           "Sea Salt Body Scrub"),
    (r"body\s*scrub|scrub",   "Sea Salt Body Scrub"),
    (r"body\s*wrap|wrap",     "Sea Salt Body Scrub"),  # no wrap service — closest match
]

_MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


# ---------------------------------------------------------------------------
# Empty result skeleton
# ---------------------------------------------------------------------------

def _empty_result() -> Dict[str, Any]:
    return {
        "intent":         "unknown",
        "service_name":   None,
        "date":           None,
        "start_time":     None,
        "booking_id":     None,
        "customer_name":  None,
        "customer_email": None,
        "customer_phone": None,
        "notes":          None,
    }


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

def _extract_service(message: str) -> Optional[str]:
    for pattern, canonical in _SERVICE_ALIASES:
        if re.search(pattern, message, re.IGNORECASE):
            return canonical
    return None


def _extract_date(message: str) -> Optional[str]:
    today = datetime.now().date()

    if re.search(r"\btoday\b", message, re.IGNORECASE):
        return today.strftime("%Y-%m-%d")
    if re.search(r"\btomorrow\b", message, re.IGNORECASE):
        return (today + timedelta(days=1)).strftime("%Y-%m-%d")

    # "the 28th", "on the 5th"
    # Day-of-week names -> nearest future occurrence
    day_names = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]
    for i, name in enumerate(day_names):
        if re.search(rf"\b{name}\b", message, re.IGNORECASE):
            days_ahead = (i - today.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    ordinal = re.search(r"\b(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)\b", message, re.IGNORECASE)
    if ordinal:
        day = int(ordinal.group(1))
        try:
            candidate = today.replace(day=day)
            if candidate <= today:
                m = today.month % 12 + 1
                y = today.year + (1 if m == 1 else 0)
                candidate = candidate.replace(year=y, month=m)
            return candidate.strftime("%Y-%m-%d")
        except ValueError:
            pass

    # "March 15" or "15 March"
    for month_name, month_num in _MONTH_MAP.items():
        m = re.search(
            rf"\b{month_name}\s+(\d{{1,2}})\b|\b(\d{{1,2}})\s+{month_name}\b",
            message, re.IGNORECASE,
        )
        if m:
            day = int(m.group(1) or m.group(2))
            try:
                candidate = today.replace(month=month_num, day=day)
                if candidate < today:
                    candidate = candidate.replace(year=today.year + 1)
                return candidate.strftime("%Y-%m-%d")
            except ValueError:
                pass

    # ISO
    iso = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", message)
    if iso:
        return iso.group(1)

    # Month-only: "in July", "July", "next July" — return 1st of that month
    # (or today if we're already in it)
    for month_name, month_num in _MONTH_MAP.items():
        if re.search(rf"\b{month_name}\b", message, re.IGNORECASE):
            year = today.year
            if month_num < today.month:
                year += 1  # already passed this year
            try:
                if month_num == today.month and year == today.year:
                    return today.strftime("%Y-%m-%d")  # already in this month
                return today.replace(year=year, month=month_num, day=1).strftime("%Y-%m-%d")
            except ValueError:
                pass

    return None


def _extract_time(message: str) -> Optional[str]:
    m = re.search(r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm))\b", message, re.IGNORECASE)
    return m.group(1).strip() if m else None


def _extract_booking_id(message: str) -> Optional[str]:
    m = re.search(r"\b(bk_[a-zA-Z0-9]+)\b", message)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Regex fallback — produces a fully populated result, not just an intent label
# ---------------------------------------------------------------------------

def _regex_fallback(message: str) -> Dict[str, Any]:
    msg = message.lower()
    result = _empty_result()

    # Always extract fields regardless of intent
    result["service_name"] = _extract_service(message)
    result["date"]         = _extract_date(message)
    result["start_time"]   = _extract_time(message)
    result["booking_id"]   = _extract_booking_id(message)

    # ── Signals ──────────────────────────────────────────────────
    _BOOK_VERBS = (
        "book", "reserve", "schedule", "i want to book", "i'd like to book",
        "can i book", "i'll take", "confirm", "sign me up", "set up",
        "make an appointment", "make a booking",
    )
    _CANCEL_PHRASES = (
        "cancel my booking", "cancel my appointment",
        "cancel appointment", "cancel booking", "cancel it",
    )
    _RESCHEDULE_PHRASES = (
        "reschedule", "move my appointment", "move my booking",
        "change my appointment", "change my booking",
    )
    _AVAIL_WORDS = (
        "available", "availability", "openings", "any slots",
        "any openings", "what times", "when can", "open slots",
    )
    # Phrases that are pure questions about what exists — NOT booking requests
    # Only match these when NO specific service has been extracted
    _SERVICE_QUESTION_PHRASES = (
        "what facials", "what massages", "what services", "what do you offer",
        "what treatments", "show me services", "list services",
        "facials do you", "massages do you", "services do you",
        "what can i get", "what do you have",
        "do you offer", "do you do", "do you provide",
        "do you carry", "do you sell",
        "what kind", "what type", "what sort",
    )
    # These only trigger service_question when NO specific service is mentioned
    _GENERIC_INFO_PHRASES = (
        "tell me about", "tell me more", "more info", "more information",
        "how much", "how long", "what is", "what's", "is there a", "do you have",
    )

    # Intent — ordered most-specific first
    if any(w in msg for w in _RESCHEDULE_PHRASES):
        result["intent"] = "reschedule_request"

    elif any(w in msg for w in _CANCEL_PHRASES):
        result["intent"] = "cancel_request"

    elif result["start_time"] or any(w in msg for w in _BOOK_VERBS):
        result["intent"] = "booking_request"

    elif any(p in msg for p in _SERVICE_QUESTION_PHRASES):
        # Pure "what services exist" questions — always show full list
        result["intent"] = "service_question"

    elif any(p in msg for p in _GENERIC_INFO_PHRASES):
        if result["service_name"]:
            # "tell me about hot stone massage" → service_question WITH service_name set
            # orchestrator will use service_name to show targeted info
            result["intent"] = "service_question"
        else:
            # No service mentioned → show full list
            result["intent"] = "service_question"

    elif any(w in msg for w in _AVAIL_WORDS):
        if result["service_name"] and result["date"]:
            result["intent"] = "availability_check"
        elif result["service_name"]:
            result["intent"] = "availability_check"
        else:
            result["intent"] = "service_question"

    elif result["service_name"] and result["date"]:
        result["intent"] = "booking_request"

    elif result["service_name"]:
        result["intent"] = "service_question"

    return result


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------

def _extract_json_object(raw: str) -> Dict[str, Any]:
    text = re.sub(r"^```(?:json)?\s*", "", (raw or "").strip())
    text = re.sub(r"\s*```$", "", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start: end + 1])
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not extract JSON from: {raw!r}")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def detect_intent(message: str) -> Dict[str, Any]:
    """
    Classify message intent using Qwen via Ollama, with regex fallback.
    Always returns a fully populated dict: intent + all extracted fields.
    """
    prompt = build_intent_prompt(message)

    try:
        raw = call_ollama(prompt)
        parsed = _extract_json_object(raw)

        if "intent" in parsed:
            # Override LLM if message clearly asks what services exist
            msg_lower = message.lower()
            service_question_signals = [
                "what services", "what massages", "what facials", "what treatments",
                "what do you have", "what do you offer", "what products",
                "services do you have", "massages do you have", "facials do you have",
                "what can i get", "tell me about your", "show me your services",
            ]
            if any(s in msg_lower for s in service_question_signals):
                parsed["intent"] = "service_question"

            result = {
                "intent":         parsed.get("intent", "unknown"),
                "service_name":   parsed.get("service_name"),
                "date":           parsed.get("date"),
                "start_time":     parsed.get("start_time"),
                "booking_id":     parsed.get("booking_id"),
                "customer_name":  parsed.get("customer_name"),
                "customer_email": parsed.get("customer_email"),
                "customer_phone": parsed.get("customer_phone"),
                "notes":          parsed.get("notes"),
            }

            # Fill any null fields the LLM missed using regex
            fallback = _regex_fallback(message)
            for field in ("service_name", "date", "start_time", "booking_id"):
                if not result[field] and fallback[field]:
                    log.debug("Enriched %s from regex: %s", field, fallback[field])
                    result[field] = fallback[field]

            return result

    except Exception as exc:
        log.warning("detect_intent LLM failed, using regex: %s", exc)

    return _regex_fallback(message)
