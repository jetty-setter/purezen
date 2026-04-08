# ============================================================
# admin_intent.py — Intent classifier for the admin AI Query
# ============================================================
# Mirrors the customer chatbot's intent_router.py pattern:
# regex fast-path first, LLM fallback second.
# Returns a structured dict that admin_orchestrator routes on.

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Intent values
# ---------------------------------------------------------------------------
# staff_query      → questions about who is working, staff list, roles
# schedule_query   → questions about a specific date's schedule or gaps
# trends_query     → statistics, cancellations, busiest, most popular
# customer_query   → questions about a specific customer's history
# upcoming_query   → what's coming up, next appointments
# range_query      → questions spanning a date range
# general          → anything else, pass to LLM with all bookings

KNOWN_INTENTS = {
    "staff_query",
    "schedule_query",
    "trends_query",
    "customer_query",
    "upcoming_query",
    "range_query",
    "general",
}


# ---------------------------------------------------------------------------
# Date extraction — mirrors customer intent_router._extract_date
# ---------------------------------------------------------------------------

_MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


def extract_date(message: str) -> Optional[str]:
    today = datetime.utcnow().date()

    if re.search(r"\btoday\b", message, re.IGNORECASE):
        return today.strftime("%Y-%m-%d")
    if re.search(r"\btomorrow\b", message, re.IGNORECASE):
        return (today + timedelta(days=1)).strftime("%Y-%m-%d")
    if re.search(r"\byesterday\b", message, re.IGNORECASE):
        return (today - timedelta(days=1)).strftime("%Y-%m-%d")

    day_names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
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

    iso = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", message)
    if iso:
        return iso.group(1)

    return None


def extract_email(message: str) -> Optional[str]:
    m = re.search(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", message, re.IGNORECASE)
    return m.group(0).lower() if m else None


def extract_date_range(message: str) -> tuple[Optional[str], Optional[str]]:
    """Extract date_from and date_to from range expressions."""
    today = datetime.utcnow().date()

    # "this week"
    if re.search(r"\bthis\s+week\b", message, re.IGNORECASE):
        start = today - timedelta(days=today.weekday())
        end   = start + timedelta(days=6)
        return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    # "last week"
    if re.search(r"\blast\s+week\b", message, re.IGNORECASE):
        start = today - timedelta(days=today.weekday() + 7)
        end   = start + timedelta(days=6)
        return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    # "this month"
    if re.search(r"\bthis\s+month\b", message, re.IGNORECASE):
        start = today.replace(day=1)
        if today.month == 12:
            end = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end = today.replace(month=today.month + 1, day=1) - timedelta(days=1)
        return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    # "last month"
    if re.search(r"\blast\s+month\b", message, re.IGNORECASE):
        if today.month == 1:
            start = today.replace(year=today.year - 1, month=12, day=1)
        else:
            start = today.replace(month=today.month - 1, day=1)
        end = today.replace(day=1) - timedelta(days=1)
        return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    # "last N days"
    m = re.search(r"\blast\s+(\d+)\s+days?\b", message, re.IGNORECASE)
    if m:
        n = int(m.group(1))
        return (today - timedelta(days=n)).strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")

    return None, None


# ---------------------------------------------------------------------------
# Regex-based intent classifier (fast path — no LLM needed)
# ---------------------------------------------------------------------------

def _regex_classify(message: str) -> Dict[str, Any]:
    msg  = message.lower().strip()
    date = extract_date(message)
    email = extract_email(message)
    date_from, date_to = extract_date_range(message)

    result: Dict[str, Any] = {
        "intent":    "general",
        "date":      date,
        "date_from": date_from,
        "date_to":   date_to,
        "email":     email,
    }

    # trends_query — checked BEFORE staff_query so "busiest staff" / "most bookings" routes here
    if re.search(
        r"\bmost\b|\bbusiest\b|\btop\b|\bcancell\b|\bcancel\b|\brate\b"
        r"|\bpopular\b|\bperform\b|\bstat\b|\btrend\b|\bcount\b|\btotal\b"
        r"|\bhow many\b|\bmost booked\b|\bleast\b|\bfewest\b|\blowest\b",
        msg,
    ):
        result["intent"] = "trends_query"
        return result

    # staff_query
    if re.search(r"\bstaff\b|\bwho('?s| is) working\b|\broster\b|\bteam\b|\bemployee", msg):
        result["intent"] = "staff_query"
        return result

    # customer_query — must have email or explicit customer mention
    if email or re.search(r"\bcustomer\b|\bguest\b|\bclient\b|\bhistory\b", msg):
        if email or re.search(r"\bhistory\b|\bbooking.{0,10}for\b|\baround\b", msg):
            result["intent"] = "customer_query"
            return result

    # range_query — has both dates or range expression
    if date_from and date_to:
        result["intent"] = "range_query"
        return result

    # schedule_query — specific date reference
    if date or re.search(
        r"\bschedule\b|\btoday\b|\btomorrow\b|\btonight\b|\bgap\b|\bfree\b|\bopen\b"
        r"|\bappointment.{0,10}(on|for)\b|\bbooking.{0,10}(on|for)\b",
        msg,
    ):
        result["intent"] = "schedule_query"
        return result

    # upcoming_query
    if re.search(r"\bupcoming\b|\bnext\b|\bsoon\b|\blater\b|\bcoming up\b", msg):
        result["intent"] = "upcoming_query"
        return result

    return result


# ---------------------------------------------------------------------------
# LLM classifier (fallback for ambiguous messages)
# ---------------------------------------------------------------------------

def _build_classify_prompt(message: str) -> str:
    today = datetime.utcnow().date().isoformat()
    tomorrow = (datetime.utcnow().date() + timedelta(days=1)).strftime("%Y-%m-%d")

    return f"""You are an intent classifier for a spa admin portal. Today is {today}.

Output ONLY a single JSON object. No markdown. No explanation.

INTENT VALUES:
- staff_query      → who is working, staff list, roles, roster
- schedule_query   → schedule for a specific date, gaps, what's on today/tomorrow
- trends_query     → statistics, most cancellations, busiest staff, most popular service, totals
- customer_query   → a specific customer's booking history (email present or "customer X")
- upcoming_query   → upcoming appointments, what's next, soon
- range_query      → questions spanning a date range (this week, last month, etc.)
- general          → anything else

EXTRACTION:
- date: YYYY-MM-DD or null. "tomorrow" = {tomorrow}
- date_from / date_to: YYYY-MM-DD range or null
- email: customer email if present, else null

EXAMPLES:
"Who has the most bookings?" → {{"intent":"trends_query","date":null,"date_from":null,"date_to":null,"email":null}}
"What's on the schedule tomorrow?" → {{"intent":"schedule_query","date":"{tomorrow}","date_from":null,"date_to":null,"email":null}}
"Who is working this week?" → {{"intent":"staff_query","date":null,"date_from":null,"date_to":null,"email":null}}
"Show me Sofia's booking history" → {{"intent":"customer_query","date":null,"date_from":null,"date_to":null,"email":null}}
"How many cancellations last month?" → {{"intent":"trends_query","date":null,"date_from":null,"date_to":null,"email":null}}

Message: "{message}"
JSON:"""


def classify(message: str, llm_fn=None) -> Dict[str, Any]:
    """
    Classify admin query intent.
    Uses regex fast-path first. Falls back to LLM if intent is 'general'
    and llm_fn is provided.
    """
    result = _regex_classify(message)

    if result["intent"] != "general" or llm_fn is None:
        return result

    # LLM fallback for ambiguous messages
    try:
        raw    = llm_fn(_build_classify_prompt(message))
        # Strip markdown fences
        clean  = re.sub(r'```(?:json)?\s*', '', raw).strip()
        parsed = json.loads(clean)
        if parsed.get("intent") in KNOWN_INTENTS:
            return {
                "intent":    parsed["intent"],
                "date":      parsed.get("date") or result["date"],
                "date_from": parsed.get("date_from") or result["date_from"],
                "date_to":   parsed.get("date_to") or result["date_to"],
                "email":     parsed.get("email") or result["email"],
            }
    except Exception as exc:
        log.warning("classify LLM fallback failed: %s", exc)

    return result
