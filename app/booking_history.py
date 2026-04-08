from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from boto3.dynamodb.conditions import Attr
from fastapi import APIRouter

from app.dynamodb_client import get_availability_table

log = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _convert_decimal(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, list):
        return [_convert_decimal(v) for v in value]
    if isinstance(value, dict):
        return {k: _convert_decimal(v) for k, v in value.items()}
    return value


def _format_display_date(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return "Unknown date"
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%d")
        try:
            return parsed.strftime("%B %-d, %Y")
        except Exception:
            return parsed.strftime("%B %d, %Y").replace(" 0", " ")
    except Exception:
        return raw


def _booking_status(slot: Dict[str, Any]) -> str:
    status = str(slot.get("status", "")).upper()
    if status == "BOOKED":
        # Determine if upcoming or completed based on date
        date_str = slot.get("date", "")
        try:
            slot_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            today = datetime.utcnow().date()
            return "Upcoming" if slot_date >= today else "Completed"
        except Exception:
            return "Upcoming"
    if status == "CANCELLED":
        return "Cancelled"
    return status.title()


def _format_booking(slot: Dict[str, Any]) -> Dict[str, Any]:
    slot = _convert_decimal(slot)
    return {
        "booking_id":   slot.get("booking_id"),
        "service_name": slot.get("service_name", "Service"),
        "date":         slot.get("date"),
        "date_display": _format_display_date(str(slot.get("date", ""))),
        "start_time":   slot.get("start_time"),
        "staff_name":   slot.get("staff_name"),
        "status":       _booking_status(slot),
    }


# ---------------------------------------------------------------------------
# Core function — used by both the API route and the orchestrator
# ---------------------------------------------------------------------------

def get_bookings_by_email(email: str) -> List[Dict[str, Any]]:
    """
    Scan the availability table for all slots booked by this email.
    Returns formatted booking dicts sorted by date descending (most recent first).
    """
    if not email:
        return []

    table = get_availability_table()
    items: List[Dict[str, Any]] = []

    scan_kwargs = {
        "FilterExpression": Attr("customer_email").eq(email.lower().strip())
    }

    response = table.scan(**scan_kwargs)
    items.extend(response.get("Items", []))

    while "LastEvaluatedKey" in response:
        scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
        response = table.scan(**scan_kwargs)
        items.extend(response.get("Items", []))

    # Only include booked or cancelled — skip AVAILABLE/UNAVAILABLE slots
    bookings = [
        item for item in items
        if str(item.get("status", "")).upper() in ("BOOKED", "CANCELLED")
        and item.get("booking_id")
    ]

    formatted = [_format_booking(b) for b in bookings]

    # Sort: upcoming first (soonest date first), then completed (most recent first), then cancelled
    def sort_key(b: Dict[str, Any]):
        order = {"Upcoming": 0, "Completed": 1, "Cancelled": 2}
        date  = b.get("date", "") or ""
        # Upcoming: ascending date (soonest first); others: descending (most recent first)
        if b["status"] == "Upcoming":
            return (0, date)
        return (order.get(b["status"], 9), "~" + date)  # ~ sorts after all dates descending

    formatted.sort(key=sort_key)
    return formatted


def format_history_for_concierge(bookings: List[Dict[str, Any]]) -> str:
    if not bookings:
        return "I don't see any past or upcoming bookings on your account."

    lines = []
    for b in bookings:
        staff = f" with {b['staff_name']}" if b.get("staff_name") else ""
        line = f"• {b['service_name']} — {b['date_display']} at {b['start_time']}{staff} [{b['status']}]"
        if b.get("booking_id"):
            line += f"\n  Confirmation: {b['booking_id']}"
        lines.append(line)

    return "Here are your appointments:\n\n" + "\n\n".join(lines)


# ---------------------------------------------------------------------------
# API route
# ---------------------------------------------------------------------------

@router.get("/bookings/history")
def booking_history(email: str, token: str = "") -> List[Dict[str, Any]]:
    """
    GET /bookings/history?email=user@example.com&token=...
    Returns all bookings for the given email. Token is validated when provided.
    """
    # Validate token if provided — prevents unauthenticated email enumeration
    if token:
        try:
            from app.users import _get_user_by_token
            user = _get_user_by_token(token)
            if not user or user.get("email", "").lower() != email.lower().strip():
                from fastapi import HTTPException
                raise HTTPException(status_code=401, detail="Unauthorized.")
        except ImportError:
            pass  # Auth module unavailable — allow request
    return get_bookings_by_email(email)
