from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from boto3.dynamodb.conditions import Attr, Key

from app.dynamodb_client import get_availability_table


# ---------------------------------------------------------------------------
# Time-of-day filtering map (matches orchestrator.py's TIME_OF_DAY_MAP)
# ---------------------------------------------------------------------------

TIME_OF_DAY_HOURS: Dict[str, tuple] = {
    "morning":   (8,  12),
    "afternoon": (12, 17),
    "evening":   (17, 21),
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _convert_decimal(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, list):
        return [_convert_decimal(v) for v in value]
    if isinstance(value, dict):
        return {k: _convert_decimal(v) for k, v in value.items()}
    return value


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _safe_scan_all(table, filter_expression=None) -> List[Dict[str, Any]]:
    scan_kwargs = {}
    if filter_expression is not None:
        scan_kwargs["FilterExpression"] = filter_expression

    items: List[Dict[str, Any]] = []
    response = table.scan(**scan_kwargs)
    items.extend(response.get("Items", []))

    while "LastEvaluatedKey" in response:
        scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
        response = table.scan(**scan_kwargs)
        items.extend(response.get("Items", []))

    return items


def _names_match(requested_name: str, actual_name: str) -> bool:
    requested = _normalize_text(requested_name)
    actual = _normalize_text(actual_name)

    if not requested or not actual:
        return False
    if requested == actual or requested in actual or actual in requested:
        return True

    requested_words = set(requested.split())
    actual_words = set(actual.split())
    return len(requested_words & actual_words) >= 1


def _format_slot(item: Dict[str, Any]) -> Dict[str, Any]:
    item = _convert_decimal(item)

    formatted = {
        "slot_id":          item.get("slot_id"),
        "service_id":       item.get("service_id"),
        "service_name":     item.get("service_name"),
        "date":             item.get("date"),
        "start_time":       item.get("start_time"),
        "end_time":         item.get("end_time"),
        "staff_id":         item.get("staff_id"),
        "staff_name":       item.get("staff_name"),
        "location_id":      item.get("location_id"),
        "room_type":        item.get("room_type"),
        "status":           item.get("status"),
        "date_start":       item.get("date_start"),
        "duration_minutes": item.get("duration_minutes"),
    }

    # Derive start_time from date_start composite key if missing
    if not formatted["start_time"] and formatted["date_start"]:
        raw = str(formatted["date_start"])
        if "#" in raw:
            _, time_part = raw.split("#", 1)
            formatted["start_time"] = time_part.strip()

    return formatted


def _slot_sort_key(slot: Dict[str, Any]) -> datetime:
    date_value = str(slot.get("date", "9999-12-31"))
    time_value = str(slot.get("start_time") or "11:59 PM")
    try:
        return datetime.strptime(f"{date_value} {time_value}", "%Y-%m-%d %I:%M %p")
    except Exception:
        return datetime.max


def _parse_slot_hour(slot: Dict[str, Any]) -> Optional[int]:
    """Return the hour (0-23) of a slot's start_time, or None if unparseable."""
    time_value = str(slot.get("start_time") or "").strip()
    if not time_value:
        return None
    for fmt in ("%I:%M %p", "%I %p"):
        try:
            return datetime.strptime(time_value.upper(), fmt).hour
        except ValueError:
            continue
    return None


def _filter_by_time_of_day(
    slots: List[Dict[str, Any]],
    time_of_day: str,
) -> List[Dict[str, Any]]:
    """
    Filter slots to those whose start_time falls within the time-of-day window.
    If no slots match the window, returns the original list unfiltered so the
    caller always gets something useful rather than an empty response.
    """
    window = TIME_OF_DAY_HOURS.get(time_of_day.lower())
    if not window:
        return slots

    start_hour, end_hour = window
    filtered = [
        slot for slot in slots
        if (hour := _parse_slot_hour(slot)) is not None and start_hour <= hour < end_hour
    ]
    return filtered if filtered else slots


def _pick_representative_slots(slots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Keep one slot per unique start_time, rotating staff when multiple staff
    are available at the same time to give the user clean, varied options.
    """
    if not slots:
        return []

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for slot in slots:
        start_time = str(slot.get("start_time") or "").strip()
        if start_time:
            grouped.setdefault(start_time, []).append(slot)

    ordered_times = sorted(
        grouped.keys(),
        key=lambda t: _slot_sort_key({"date": slots[0].get("date"), "start_time": t}),
    )

    chosen: List[Dict[str, Any]] = []
    last_staff_id = None

    for start_time in ordered_times:
        options = sorted(
            grouped[start_time],
            key=lambda s: (str(s.get("staff_name") or ""), str(s.get("staff_id") or "")),
        )

        selected = next(
            (opt for opt in options if opt.get("staff_id") != last_staff_id),
            options[0],
        )
        chosen.append(selected)
        last_staff_id = selected.get("staff_id")

    return chosen


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_available_slots_for_service(
    service_name: str,
    requested_date: Optional[str] = None,
    time_of_day: Optional[str] = None,
    limit: int = 8,
) -> List[Dict[str, Any]]:
    """
    Return available slot dicts for the requested service, date, and
    optional time-of-day preference (morning / afternoon / evening).

    Uses the date-status-index GSI when a date is provided for efficient lookup.
    Falls back to a full scan when no date is given.
    One slot per displayed time, with staff rotated across times when possible.
    """
    availability_table = get_availability_table()

    if requested_date:
        # Use GSI for efficient date+status lookup
        response = availability_table.query(
            IndexName="date-status-index",
            KeyConditionExpression=(
                Key("date").eq(requested_date) & Key("status").eq("AVAILABLE")
            ),
        )
        all_items = [_convert_decimal(item) for item in response.get("Items", [])]
        # Handle pagination on GSI query
        while "LastEvaluatedKey" in response:
            response = availability_table.query(
                IndexName="date-status-index",
                KeyConditionExpression=(
                    Key("date").eq(requested_date) & Key("status").eq("AVAILABLE")
                ),
                ExclusiveStartKey=response["LastEvaluatedKey"],
            )
            all_items.extend([_convert_decimal(i) for i in response.get("Items", [])])
    else:
        all_items = _safe_scan_all(availability_table, Attr("status").eq("AVAILABLE"))
        all_items = [_convert_decimal(item) for item in all_items]

    # Double-check status in application layer to guard against GSI eventual consistency lag
    all_items = [item for item in all_items if str(item.get("status", "")).upper() == "AVAILABLE"]

    matching = [
        item for item in all_items
        if any(
            _names_match(service_name, str(svc))
            for svc in (item.get("services_offered") or [item.get("service_name", "")])
        )
    ]

    formatted = sorted([_format_slot(item) for item in matching], key=_slot_sort_key)

    if time_of_day:
        formatted = _filter_by_time_of_day(formatted, time_of_day)

    representative = _pick_representative_slots(formatted)
    return representative[:limit]


def format_slots_for_response(slots: List[Dict[str, Any]]) -> str:
    if not slots:
        return "I couldn't find any openings."

    lines = []
    for slot in slots:
        time_text  = slot.get("start_time") or "Unknown time"
        staff_name = slot.get("staff_name")
        lines.append(f"- {time_text} with {staff_name}" if staff_name else f"- {time_text}")

    return "\n".join(lines)


def debug_service_availability(service_name: str, requested_date: str) -> Dict[str, Any]:
    availability_table = get_availability_table()

    all_items = _safe_scan_all(
        availability_table,
        Attr("status").eq("AVAILABLE") & Attr("date").eq(requested_date),
    )
    all_items = [_convert_decimal(item) for item in all_items]

    matching = [
        item for item in all_items
        if _names_match(service_name, str(item.get("service_name", "")))
    ]

    formatted = sorted([_format_slot(item) for item in matching], key=_slot_sort_key)
    representative = _pick_representative_slots(formatted)

    return {
        "service_name":      service_name,
        "requested_date":    requested_date,
        "raw_matching_items": len(formatted),
        "displayed_slots":   len(representative),
        "sample_items":      representative[:10],
    }
