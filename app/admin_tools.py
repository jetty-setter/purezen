# ============================================================
# admin_tools.py — Tool registry and executor
# ============================================================
# Each tool is a named data operation the orchestrator can call.
# To add a tool: add an entry to TOOLS and a handler in execute_tool().
#
# data_fns injection keeps this file decoupled from DynamoDB —
# the orchestrator passes in callables from admin_routes.py.

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

TOOLS: List[Dict[str, Any]] = [
    {
        "name":        "get_bookings_by_date",
        "description": "Get all bookings for a specific date.",
        "parameters":  {"date": "YYYY-MM-DD"},
    },
    {
        "name":        "get_bookings_range",
        "description": "Get all bookings between two dates.",
        "parameters":  {"date_from": "YYYY-MM-DD", "date_to": "YYYY-MM-DD"},
    },
    {
        "name":        "get_all_bookings",
        "description": "Get all bookings in the system.",
        "parameters":  {},
    },
    {
        "name":        "get_staff_roster",
        "description": "Get all staff members with roles and active status.",
        "parameters":  {},
    },
    {
        "name":        "get_customer_history",
        "description": "Get booking history for a customer by name or email.",
        "parameters":  {"query": "customer name or email address"},
    },
    {
        "name":        "get_staff_bookings",
        "description": "Get all bookings assigned to a specific staff member by name. Use when asked about a staff member's schedule, appointments, or history.",
        "parameters":  {"name": "staff member name or first name"},
    },
    {
        "name":        "get_trends",
        "description": "Get booking statistics: totals by service/staff, cancellation rate.",
        "parameters":  {"date_from": "optional YYYY-MM-DD", "date_to": "optional YYYY-MM-DD"},
    },
    {
        "name":        "get_upcoming_bookings",
        "description": "Get upcoming appointments from today onward.",
        "parameters":  {"limit": "optional integer, default 10"},
    },
]


# ---------------------------------------------------------------------------
# Tool executor
# ---------------------------------------------------------------------------

def execute_tool(name: str, params: dict, data_fns: Dict[str, Any]) -> str:
    """
    Execute a named tool and return its result as a JSON string.

    data_fns keys:
        get_all_bookings: () -> List[Dict]
        scan_staff:       () -> List[Dict]
    """
    get_all_bookings = data_fns["get_all_bookings"]
    scan_staff       = data_fns["scan_staff"]

    try:
        if name == "get_bookings_by_date":
            date = params.get("date", "")
            bks  = [b for b in get_all_bookings() if b.get("date") == date]
            return json.dumps(bks[:25])

        if name == "get_bookings_range":
            df  = params.get("date_from", "")
            dt  = params.get("date_to", "")
            bks = [b for b in get_all_bookings() if df <= (b.get("date") or "") <= dt]
            return json.dumps(bks[:40])

        if name == "get_all_bookings":
            return json.dumps(get_all_bookings()[:50])

        if name == "get_staff_roster":
            items = scan_staff()
            return json.dumps([{
                "name":   s.get("display_name") or f"{s.get('first_name','')} {s.get('last_name','')}".strip(),
                "role":   s.get("role"),
                "active": s.get("is_active", True),
                "email":  s.get("email"),
                "skills": s.get("skills", []),
            } for s in items if s.get("is_active", True)])

        if name == "get_customer_history":
            query = params.get("query", params.get("email", "")).lower().strip()
            if not query:
                return json.dumps([])
            bks = [
                b for b in get_all_bookings()
                if query in (b.get("customer_email") or "").lower()
                or query in (b.get("customer_name") or "").lower()
                # Also match first name only e.g. "sofia" matches "Sofia N."
                or any(query == part.lower() for part in (b.get("customer_name") or "").split())
            ]
            return json.dumps(bks[:25])

        if name == "get_staff_bookings":
            query = params.get("name", "").lower().strip()
            if not query:
                return json.dumps([])
            bks = [
                b for b in get_all_bookings()
                if query in (b.get("staff_name") or "").lower()
                or any(query == part.lower() for part in (b.get("staff_name") or "").split())
            ]
            bks.sort(key=lambda b: b.get("date", ""))
            return json.dumps(bks[:25])

        if name == "get_trends":
            df  = params.get("date_from")
            dt  = params.get("date_to")
            bks = get_all_bookings()
            if df: bks = [b for b in bks if (b.get("date") or "") >= df]
            if dt: bks = [b for b in bks if (b.get("date") or "") <= dt]
            booked    = [b for b in bks if b.get("status") in ("Upcoming", "Completed")]
            cancelled = [b for b in bks if b.get("status") == "Cancelled"]
            return json.dumps({
                "total_bookings":    len(booked),
                "total_cancelled":   len(cancelled),
                "cancellation_rate": round(len(cancelled) / max(len(bks), 1) * 100, 1),
                "by_service":        dict(Counter(b.get("service_name", "Unknown") for b in booked).most_common(10)),
                "by_staff":          dict(Counter(b.get("staff_name", "Unassigned") for b in booked).most_common(10)),
            })

        if name == "get_upcoming_bookings":
            limit = int(params.get("limit", 10))
            today = datetime.utcnow().date().isoformat()
            bks   = [b for b in get_all_bookings()
                     if b.get("status") == "Upcoming" and (b.get("date") or "") >= today]
            return json.dumps(bks[:limit])

    except Exception as exc:
        log.warning("Tool %s failed: %s", name, exc)
        return json.dumps({"error": str(exc)})

    return json.dumps({"error": f"Unknown tool: {name}"})
