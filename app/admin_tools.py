# ============================================================
# ai_tools.py — MCP-inspired tool registry and executor
# ============================================================
# This file defines the available tools and executes them.
# To add a new tool: add an entry to TOOLS and a handler in execute_tool().

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool registry — each tool is a capability the LLM can invoke
# ---------------------------------------------------------------------------

TOOLS: List[Dict[str, Any]] = [
    {
        "name":        "get_bookings_by_date",
        "description": "Get all bookings for a specific date. Use when asked about a particular day's schedule.",
        "parameters":  {"date": "YYYY-MM-DD string"},
    },
    {
        "name":        "get_bookings_range",
        "description": "Get all bookings between two dates. Use for week or month range questions.",
        "parameters":  {"date_from": "YYYY-MM-DD", "date_to": "YYYY-MM-DD"},
    },
    {
        "name":        "get_all_bookings",
        "description": "Get all bookings in the system. Use for general questions about totals, patterns, or when no date is specified.",
        "parameters":  {},
    },
    {
        "name":        "get_staff_roster",
        "description": "Get all staff members with their roles and active status.",
        "parameters":  {},
    },
    {
        "name":        "get_customer_history",
        "description": "Get full booking history for a specific customer by email address.",
        "parameters":  {"email": "customer email address"},
    },
    {
        "name":        "get_trends",
        "description": "Get booking statistics: totals by service, totals by staff, cancellation rate, peak hour.",
        "parameters":  {"date_from": "optional YYYY-MM-DD", "date_to": "optional YYYY-MM-DD"},
    },
    {
        "name":        "get_upcoming_bookings",
        "description": "Get upcoming appointments from today onward.",
        "parameters":  {"limit": "optional integer, default 20"},
    },
]


def tool_list_text() -> str:
    """Return a formatted string of all tools for inclusion in prompts."""
    return "\n".join(
        f"- {t['name']}({', '.join(f'{k}: {v}' for k, v in t['parameters'].items()) or 'no params'}): {t['description']}"
        for t in TOOLS
    )


# ---------------------------------------------------------------------------
# Tool executor — called by the orchestrator when the LLM picks a tool
# ---------------------------------------------------------------------------

def execute_tool(name: str, params: dict, data_fns: Dict[str, Any]) -> str:
    """
    Execute a named tool and return its result as a JSON string.

    data_fns is a dict of callables injected by the orchestrator:
        - get_all_bookings: () -> List[Dict]
        - scan_staff: () -> List[Dict]
    This keeps ai_tools.py decoupled from DynamoDB imports.
    """
    get_all_bookings = data_fns["get_all_bookings"]
    scan_staff       = data_fns["scan_staff"]

    try:
        if name == "get_bookings_by_date":
            date = params.get("date", "")
            bks  = [b for b in get_all_bookings() if b.get("date") == date]
            return json.dumps(bks[:25])

        elif name == "get_bookings_range":
            df  = params.get("date_from", "")
            dt  = params.get("date_to", "")
            bks = [b for b in get_all_bookings()
                   if df <= (b.get("date") or "") <= dt]
            return json.dumps(bks[:40])

        elif name == "get_all_bookings":
            return json.dumps(get_all_bookings()[:50])

        elif name == "get_staff_roster":
            items = scan_staff()
            return json.dumps([{
                "name":   s.get("display_name") or f"{s.get('first_name','')} {s.get('last_name','')}".strip(),
                "role":   s.get("role"),
                "active": s.get("is_active", True),
                "email":  s.get("email"),
                "skills": s.get("skills", []),
            } for s in items])

        elif name == "get_customer_history":
            email = params.get("email", "").lower().strip()
            bks   = [b for b in get_all_bookings()
                     if (b.get("customer_email") or "").lower() == email]
            return json.dumps(bks[:25])

        elif name == "get_trends":
            df  = params.get("date_from")
            dt  = params.get("date_to")
            bks = get_all_bookings()
            if df:
                bks = [b for b in bks if (b.get("date") or "") >= df]
            if dt:
                bks = [b for b in bks if (b.get("date") or "") <= dt]
            booked    = [b for b in bks if b.get("status") in ("Upcoming", "Completed")]
            cancelled = [b for b in bks if b.get("status") == "Cancelled"]
            return json.dumps({
                "total_bookings":    len(booked),
                "total_cancelled":   len(cancelled),
                "cancellation_rate": round(len(cancelled) / max(len(bks), 1) * 100, 1),
                "by_service":        dict(Counter(b.get("service_name", "Unknown") for b in booked).most_common(10)),
                "by_staff":          dict(Counter(b.get("staff_name", "Unassigned") for b in booked).most_common(10)),
            })

        elif name == "get_upcoming_bookings":
            limit = int(params.get("limit", 20))
            today = datetime.utcnow().date().isoformat()
            bks   = [b for b in get_all_bookings()
                     if b.get("status") == "Upcoming" and (b.get("date") or "") >= today]
            return json.dumps(bks[:limit])

    except Exception as exc:
        log.warning("Tool %s failed: %s", name, exc)
        return json.dumps({"error": str(exc)})

    return json.dumps({"error": f"Unknown tool: {name}"})
