from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import logging
from boto3.dynamodb.conditions import Attr, Key
from app.dynamodb_client import get_availability_table

log = logging.getLogger(__name__)

# In-memory session store. Not persistent across restarts.
SESSION_STATE: Dict[str, Dict[str, Any]] = {}
SESSION_TTL_SECONDS = 3600  # 1 hour


def _purge_expired_sessions() -> None:
    now = datetime.utcnow().timestamp()
    expired = [
        sid for sid, state in SESSION_STATE.items()
        if now - state.get("last_active", now) > SESSION_TTL_SECONDS
    ]
    for sid in expired:
        del SESSION_STATE[sid]


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def _normalize_text(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def _normalize_time(value: str) -> str:
    text = _normalize_text(value).upper().replace(".", "")
    text = re.sub(r"\s+", " ", text)

    # Handle 24hr format (e.g. "14:00") -> convert to 12hr
    match_24 = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if match_24:
        h, m = int(match_24.group(1)), match_24.group(2)
        suffix = "AM" if h < 12 else "PM"
        h12 = h % 12 or 12
        return f"{h12}:{m} {suffix}"

    for fmt in ("%I:%M %p", "%I %p"):
        try:
            parsed = datetime.strptime(text, fmt)
            try:
                return parsed.strftime("%-I:%M %p")
            except Exception:
                return parsed.strftime("%I:%M %p").lstrip("0")
        except Exception:
            continue

    return (value or "").strip()


def _format_display_date(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return "your selected date"
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%d")
        try:
            return parsed.strftime("%B %-d, %Y")
        except Exception:
            return parsed.strftime("%B %d, %Y").replace(" 0", " ")
    except Exception:
        return raw


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

def _extract_time(message: str) -> Optional[str]:
    if not message:
        return None
    match = re.search(r"\b(\d{1,2}(?::\d{2})?\s?(AM|PM|am|pm))\b", message)
    if not match:
        return None
    return _normalize_time(match.group(1))


def _extract_date(message: str) -> Optional[str]:
    if not message:
        return None
    match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", message)
    return match.group(1) if match else None


def _extract_booking_id(message: str) -> Optional[str]:
    if not message:
        return None
    match = re.search(r"\b(bk_[a-zA-Z0-9]+)\b", message.strip())
    return match.group(1) if match else None


def _extract_ordinal_index(message: str) -> Optional[int]:
    normalized = _normalize_text(message)
    ordinal_map = {
        "first": 0, "1st": 0, "one": 0,
        "second": 1, "2nd": 1, "two": 1,
        "third": 2, "3rd": 2, "three": 2,
        "fourth": 3, "4th": 3, "four": 3,
        "fifth": 4, "5th": 4, "five": 4,
    }
    for key, index in ordinal_map.items():
        if re.search(rf"\b{re.escape(key)}\b", normalized):
            return index

    bare = (message or "").strip()
    if re.fullmatch(r"[1-8]", bare):
        return int(bare) - 1

    return None


def _extract_name(message: str) -> Optional[str]:
    text = (message or "").strip()

    # Reject obvious non-names immediately
    _NON_NAMES = {
        "none", "no", "nope", "yes", "yeah", "ok", "okay", "sure",
        "fine", "good", "great", "next", "skip", "cancel", "stop",
        "done", "continue", "back", "help", "thanks", "thank you",
    }
    if text.lower().strip() in _NON_NAMES:
        return None

    patterns = [
        r"(?i)\bmy name is\s+([A-Za-z][A-Za-z\s'\-]{1,60})$",
        r"(?i)\bi am\s+([A-Za-z][A-Za-z\s'\-]{1,60})$",
        r"(?i)\bi'm\s+([A-Za-z][A-Za-z\s'\-]{1,60})$",
        r"(?i)\bthis is\s+([A-Za-z][A-Za-z\s'\-]{1,60})$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            name = " ".join(match.group(1).strip().split())
            candidate = " ".join(part.capitalize() for part in name.split())
            if candidate.lower() not in _NON_NAMES:
                return candidate

    cleaned = " ".join(text.split())
    if re.fullmatch(r"[A-Za-z][A-Za-z\s'\-]{1,60}", cleaned):
        candidate = " ".join(part.capitalize() for part in cleaned.split())
        if candidate.lower() not in _NON_NAMES:
            return candidate

    return None


def _extract_phone(message: str) -> Optional[str]:
    digits = re.sub(r"\D", "", message or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return None
    return f"({digits[0:3]}) {digits[3:6]}-{digits[6:10]}"


def _extract_email(message: str) -> Optional[str]:
    match = re.search(
        r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b",
        message or "",
        re.IGNORECASE,
    )
    return match.group(0).strip().lower() if match else None


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

def get_session_state(session_id: str) -> Dict[str, Any]:
    _purge_expired_sessions()
    state = SESSION_STATE.setdefault(session_id, {})
    state["last_active"] = datetime.utcnow().timestamp()
    return state


def save_presented_slots(
    session_id: str,
    slots: List[Dict[str, Any]],
    service_name: Optional[str] = None,
) -> None:
    state = get_session_state(session_id)
    if service_name:
        state["last_service_name"] = service_name
    state["last_presented_slots"] = slots


def clear_intake_state(session_id: str) -> None:
    state = get_session_state(session_id)
    for key in (
        "pending_booking_slot",
        "pending_booking_service",
        "booking_name",
        "booking_phone",
        "booking_email",
        "booking_special_requests",
        "awaiting_field",
    ):
        state.pop(key, None)


def clear_reschedule_state(session_id: str) -> None:
    state = get_session_state(session_id)
    for key in (
        "pending_reschedule_booking_id",
        "pending_reschedule_booking",
        "last_reschedule_slots",
        "awaiting_reschedule_booking_id",
    ):
        state.pop(key, None)


def clear_cancel_state(session_id: str) -> None:
    get_session_state(session_id).pop("awaiting_cancel_booking_id", None)


# ---------------------------------------------------------------------------
# Slot matching
# ---------------------------------------------------------------------------

def _find_slot_from_last_presented(session_id: str, message: str) -> Optional[Dict[str, Any]]:
    state = get_session_state(session_id)
    slots = state.get("last_presented_slots", [])
    if not slots:
        return None

    msg = message.strip()
    # Treat bare hour digit as time: "2" -> "2:00 PM", "11" -> "11:00 AM"
    bare_hour = re.fullmatch(r"(\d{1,2})", msg)
    if bare_hour:
        h = int(bare_hour.group(1))
        if 1 <= h <= 12:
            suffix = "PM" if h < 9 else ("AM" if h >= 9 and h < 12 else "PM")
            # Default PM for afternoon (assume spa hours 9am-6pm, ambiguous -> PM)
            suffix = "AM" if h >= 9 and h <= 11 else "PM"
            msg = f"{h}:00 {suffix}"
    compact = re.fullmatch(r"(\d{1,2})(\d{2})", msg)
    if compact:
        h, m = int(compact.group(1)), compact.group(2)
        suffix = "PM" if h >= 12 else "AM"
        dh = h if h <= 12 else h - 12
        if dh == 0: dh = 12
        msg = "{}:{} {}".format(dh, m, suffix)

    requested_date = _extract_date(msg)
    requested_time = _extract_time(msg)
    ordinal_index = _extract_ordinal_index(msg)
    normalized = _normalize_text(msg)

    if ordinal_index is not None and not re.fullmatch(r"\d{1,2}", msg.strip()):
        def _sort_key(s):
            try:
                h, m = s.get("start_time","00:00").split(":")
                return (s.get("date",""), int(h)*60+int(m))
            except:
                return (s.get("date",""), 0)
        sorted_slots = sorted(slots, key=_sort_key)
        if 0 <= ordinal_index < len(sorted_slots):
            return sorted_slots[ordinal_index]

    if any(phrase in normalized for phrase in ("that one", "that time", "that slot")):
        if len(slots) == 1:
            return slots[0]

    if requested_date and requested_time:
        for slot in slots:
            if (
                str(slot.get("date", "")).strip() == requested_date
                and _normalize_time(str(slot.get("start_time", ""))) == requested_time
            ):
                return slot

    if requested_time:
        matching = [
            slot for slot in slots
            if _normalize_time(str(slot.get("start_time", ""))) == requested_time
        ]
        if len(matching) == 1:
            return matching[0]

    return None


def _find_slot_from_last_reschedule_options(session_id: str, message: str) -> Optional[Dict[str, Any]]:
    state = get_session_state(session_id)
    slots = state.get("last_reschedule_slots", [])
    if not slots:
        return None

    msg = message.strip()
    # Treat bare hour digit as time: "2" -> "2:00 PM", "11" -> "11:00 AM"
    bare_hour = re.fullmatch(r"(\d{1,2})", msg)
    if bare_hour:
        h = int(bare_hour.group(1))
        if 1 <= h <= 12:
            suffix = "PM" if h < 9 else ("AM" if h >= 9 and h < 12 else "PM")
            # Default PM for afternoon (assume spa hours 9am-6pm, ambiguous -> PM)
            suffix = "AM" if h >= 9 and h <= 11 else "PM"
            msg = f"{h}:00 {suffix}"
    compact = re.fullmatch(r"(\d{1,2})(\d{2})", msg)
    if compact:
        h, m = int(compact.group(1)), compact.group(2)
        suffix = "PM" if h >= 12 else "AM"
        dh = h if h <= 12 else h - 12
        if dh == 0: dh = 12
        msg = "{}:{} {}".format(dh, m, suffix)

    requested_date = _extract_date(msg)
    requested_time = _extract_time(msg)
    ordinal_index = _extract_ordinal_index(msg)
    normalized = _normalize_text(msg)

    if ordinal_index is not None and not re.fullmatch(r"\d{1,2}", msg.strip()):
        def _sort_key(s):
            try:
                h, m = s.get("start_time","00:00").split(":")
                return (s.get("date",""), int(h)*60+int(m))
            except:
                return (s.get("date",""), 0)
        sorted_slots = sorted(slots, key=_sort_key)
        if 0 <= ordinal_index < len(sorted_slots):
            return sorted_slots[ordinal_index]

    if any(phrase in normalized for phrase in ("that one", "that time", "that slot")):
        if len(slots) == 1:
            return slots[0]

    if requested_date and requested_time:
        for slot in slots:
            slot_date = str(slot.get("date", "")).strip()
            slot_time = _normalize_time(str(slot.get("start_time", "")))
            if slot_date == requested_date and slot_time == requested_time:
                return slot

    if requested_time:
        matching = [
            slot for slot in slots
            if _normalize_time(str(slot.get("start_time", ""))) == requested_time
        ]
        if len(matching) == 1:
            return matching[0]

    return None


# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------

def booking_intent_detected(message: str) -> bool:
    normalized = _normalize_text(message)
    phrases = ["book", "reserve", "take", "i want", "i'll take", "schedule", "confirm"]
    if re.search(r"\b\d{1,2}(:\d{2})?\s?(am|pm)\b", normalized):
        return True
    return any(phrase in normalized for phrase in phrases)


def cancel_intent_detected(message: str) -> bool:
    normalized = _normalize_text(message)
    phrases = [
        "cancel my booking", "cancel my appointment",
        "cancel appointment", "cancel booking", "cancel it",
    ]
    return any(phrase in normalized for phrase in phrases)


def reschedule_intent_detected(message: str) -> bool:
    normalized = _normalize_text(message)
    phrases = [
        "reschedule", "move my appointment", "move my booking",
        "change my appointment", "change my booking",
    ]
    return any(phrase in normalized for phrase in phrases)


# ---------------------------------------------------------------------------
# Booking intake
# ---------------------------------------------------------------------------

def _first_awaiting_field(state: Dict[str, Any]) -> str:
    """
    Determine which field to collect first based on what's already pre-filled.
    Order: name → phone → email → special_requests
    """
    if not state.get("booking_name"):
        return "name"
    if not state.get("booking_phone"):
        return "phone"
    if not state.get("booking_email"):
        return "email"
    return "special_requests"


def begin_booking_intake(
    session_id: str,
    slot: Dict[str, Any],
    service_name: Optional[str] = None,
) -> Dict[str, Any]:
    state = get_session_state(session_id)
    clear_intake_state(session_id)
    state["pending_booking_slot"] = slot
    state["pending_booking_service"] = service_name or slot.get("service_name")

    # Re-apply pre-filled fields after clear_intake_state wiped them
    # (user_name / user_email remain in state since clear_intake_state doesn't touch them)
    if state.get("user_name"):
        state["booking_name"] = state["user_name"]
    if state.get("user_email"):
        state["booking_email"] = state["user_email"]

    first_field = _first_awaiting_field(state)
    state["awaiting_field"] = first_field

    service  = service_name or slot.get("service_name", "your service")
    date_text = _format_display_date(str(slot.get("date", "")))
    time_text = _to_12hr(str(slot.get("start_time", "your selected time")))

    # Build opening message based on what we already know
    intro = (
        f"Great choice. I can book {service} on {date_text} at {time_text}."
    )

    if first_field == "name":
        prompt = "What name should I put on the appointment?"
    elif first_field == "phone":
        name = state.get("booking_name", "")
        prompt = f"Got it{', ' + name.split()[0] if name else ''}. What phone number should we use?"
    elif first_field == "email":
        prompt = "What email address should we send your confirmation to?"
    else:
        prompt = "Any special requests? You can type them now, or say 'none'."

    return {
        "success": True,
        "needs_clarification": True,
        "message": f"{intro} {prompt}",
    }


def continue_booking_intake(session_id: str, message: str) -> Dict[str, Any]:
    state = get_session_state(session_id)
    awaiting_field = state.get("awaiting_field")

    if not awaiting_field:
        return {"success": False, "message": "I'm not currently collecting booking details."}

    if awaiting_field == "name":
        name = _extract_name(message)
        if not name:
            return {
                "success": False,
                "needs_clarification": True,
                "message": "What name should I put on the appointment?",
            }
        state["booking_name"] = name
        state["awaiting_field"] = "phone"
        return {
            "success": True,
            "needs_clarification": True,
            "message": "What phone number should we use for your appointment?",
        }

    if awaiting_field == "phone":
        phone = _extract_phone(message)
        if not phone:
            return {
                "success": False,
                "needs_clarification": True,
                "message": "Please send a 10-digit phone number, like 402-555-1234.",
            }
        state["booking_phone"] = phone
        # Skip email if already pre-filled
        if state.get("booking_email"):
            state["awaiting_field"] = "special_requests"
            return {
                "success": True,
                "needs_clarification": True,
                "message": (
                    f"We'll send your confirmation to {state['booking_email']}. "
                    "Any special requests? You can type them now, or say 'none'."
                ),
            }
        state["awaiting_field"] = "email"
        return {
            "success": True,
            "needs_clarification": True,
            "message": "And what email address should we send your confirmation to?",
        }

    if awaiting_field == "email":
        email = _extract_email(message)
        if not email:
            return {
                "success": False,
                "needs_clarification": True,
                "message": "Please send a valid email address for the confirmation.",
            }
        state["booking_email"] = email
        state["awaiting_field"] = "special_requests"
        return {
            "success": True,
            "needs_clarification": True,
            "message": (
                "Any special requests for the appointment? "
                "You can type them now, or say 'none'."
            ),
        }

    if awaiting_field == "special_requests":
        normalized = _normalize_text(message)
        state["booking_special_requests"] = (
            None if normalized in {"none", "no", "nope", "n/a"}
            else " ".join((message or "").strip().split())
        )
        return finalize_pending_booking(session_id)

    return {"success": False, "message": "I ran into an issue collecting your booking details."}


def finalize_pending_booking(session_id: str) -> Dict[str, Any]:
    state = get_session_state(session_id)
    slot = state.get("pending_booking_slot")

    if not slot:
        return {"success": False, "message": "I couldn't find the appointment you were trying to reserve."}

    pending_service = state.get("pending_booking_service")
    if pending_service:
        slot = dict(slot)
        slot["service_name"] = pending_service

    booking_result = book_slot(
        slot_id=str(slot["slot_id"]),
        customer_name=state.get("booking_name"),
        customer_phone=state.get("booking_phone"),
        customer_email=state.get("booking_email"),
        special_requests=state.get("booking_special_requests"),
        service_name=pending_service or slot.get("service_name"),
    )

    if booking_result.get("success"):
        state["last_booked_slot"] = booking_result["slot"]
        state["last_booking_id"] = booking_result["booking_id"]
        clear_intake_state(session_id)

    return booking_result


# ---------------------------------------------------------------------------
# DynamoDB operations
# ---------------------------------------------------------------------------

def get_slot_by_id(slot_id: str) -> Optional[Dict[str, Any]]:
    table = get_availability_table()
    response = table.get_item(Key={"slot_id": slot_id})
    return response.get("Item")


def find_booking_by_booking_id(booking_id: str) -> Optional[Dict[str, Any]]:
    table = get_availability_table()

    items = []
    scan_kwargs = {"FilterExpression": Attr("booking_id").eq(booking_id)}
    response = table.scan(**scan_kwargs)
    items.extend(response.get("Items", []))

    while "LastEvaluatedKey" in response:
        scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
        response = table.scan(**scan_kwargs)
        items.extend(response.get("Items", []))

    return items[0] if items else None


def book_slot(
    slot_id: str,
    customer_name: Optional[str] = None,
    customer_phone: Optional[str] = None,
    customer_email: Optional[str] = None,
    special_requests: Optional[str] = None,
    service_name: Optional[str] = None,
) -> Dict[str, Any]:
    table = get_availability_table()
    response = table.get_item(Key={"slot_id": slot_id})
    slot = response.get("Item")

    if not slot:
        return {"success": False, "message": "I couldn't find that appointment slot."}

    if str(slot.get("status", "")).upper() != "AVAILABLE":
        return {"success": False, "message": "That time is no longer available. Please choose another option."}

    booking_id = f"bk_{uuid.uuid4().hex[:12]}"

    update_expression = (
        "SET #status = :booked, booking_id = :booking_id, booked_at = :booked_at"
    )
    expression_names = {"#status": "status"}
    expression_values = {
        ":booked": "BOOKED",
        ":booking_id": booking_id,
        ":booked_at": datetime.utcnow().isoformat(),
        ":available": "AVAILABLE",
    }

    if service_name:
        update_expression += ", service_name = :service_name"
        expression_values[":service_name"] = service_name
    if customer_name:
        update_expression += ", customer_name = :customer_name"
        expression_values[":customer_name"] = customer_name
    if customer_phone:
        update_expression += ", customer_phone = :customer_phone"
        expression_values[":customer_phone"] = customer_phone
    if customer_email:
        update_expression += ", customer_email = :customer_email"
        expression_values[":customer_email"] = customer_email
    if special_requests:
        update_expression += ", special_requests = :special_requests"
        expression_values[":special_requests"] = special_requests

    try:
        table.update_item(
            Key={"slot_id": slot_id},
            UpdateExpression=update_expression,
            ExpressionAttributeNames=expression_names,
            ExpressionAttributeValues=expression_values,
            ConditionExpression="#status = :available",
        )
    except Exception as exc:
        log.error("book_slot: DynamoDB update failed for slot_id=%s: %s", slot_id, exc, exc_info=True)
        return {"success": False, "message": "That time was just taken. Please choose another option."}

    updated_slot = {**slot, "status": "BOOKED", "booking_id": booking_id}
    if service_name:
        updated_slot["service_name"] = service_name
    if customer_name:
        updated_slot["customer_name"] = customer_name
    if customer_phone:
        updated_slot["customer_phone"] = customer_phone
    if customer_email:
        updated_slot["customer_email"] = customer_email
    if special_requests:
        updated_slot["special_requests"] = special_requests

    try:
        staff_id   = slot.get("staff_id")
        slot_date  = slot.get("date")
        start_time = slot.get("start_time")
        if staff_id and slot_date and start_time:
            siblings = []
            scan_kwargs = {
                "FilterExpression": (
                    Attr("staff_id").eq(staff_id) &
                    Attr("date").eq(slot_date) &
                    Attr("start_time").eq(start_time)
                )
            }
            while True:
                scan_resp = table.scan(**scan_kwargs)
                siblings += scan_resp.get("Items", [])
                if "LastEvaluatedKey" not in scan_resp:
                    break
                scan_kwargs["ExclusiveStartKey"] = scan_resp["LastEvaluatedKey"]
            for other in siblings:
                if str(other["slot_id"]) != slot_id:
                    if str(other.get("status", "")).upper() not in ("BOOKED",):
                        table.update_item(
                            Key={"slot_id": other["slot_id"]},
                            UpdateExpression="SET #status = :unavailable",
                            ExpressionAttributeNames={"#status": "status"},
                            ExpressionAttributeValues={":unavailable": "UNAVAILABLE"},
                        )
    except Exception as exc:
        log.warning("book_slot: failed to mark sibling slots unavailable: %s", exc)

    return {
        "success": True,
        "booking_id": booking_id,
        "slot": updated_slot,
        "message": format_booking_confirmation(updated_slot),
    }


def cancel_booking(booking_id: str) -> Dict[str, Any]:
    table = get_availability_table()
    booking = find_booking_by_booking_id(booking_id)

    if not booking:
        return {"success": False, "message": "I couldn't find a booking with that confirmation number."}

    if str(booking.get("status", "")).upper() != "BOOKED":
        return {"success": False, "message": "That booking is not currently active."}

    slot_id = str(booking["slot_id"])

    try:
        table.update_item(
            Key={"slot_id": slot_id},
            UpdateExpression="SET #status = :cancelled, cancelled_at = :cancelled_at",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":cancelled": "CANCELLED",
                ":cancelled_at": datetime.utcnow().isoformat(),
                ":booked": "BOOKED",
            },
            ConditionExpression="#status = :booked",
        )
    except Exception as exc:
        log.error("cancel_booking: DynamoDB update failed for slot_id=%s booking_id=%s: %s", slot_id, booking_id, exc, exc_info=True)
        return {"success": False, "message": "I wasn't able to cancel that appointment. Please try again."}

    canceled_slot = {**booking, "status": "CANCELLED", "cancelled_at": datetime.utcnow().isoformat()}

    try:
        staff_id   = booking.get("staff_id")
        slot_date  = booking.get("date")
        start_time = booking.get("start_time")
        if staff_id and slot_date and start_time:
            scan_resp = table.scan(
                FilterExpression=(
                    Attr("staff_id").eq(staff_id) &
                    Attr("date").eq(slot_date) &
                    Attr("start_time").eq(start_time) &
                    Attr("status").eq("UNAVAILABLE")
                )
            )
            for other in scan_resp.get("Items", []):
                table.update_item(
                    Key={"slot_id": other["slot_id"]},
                    UpdateExpression="SET #status = :available",
                    ExpressionAttributeNames={"#status": "status"},
                    ExpressionAttributeValues={":available": "AVAILABLE"},
                )
    except Exception as exc:
        log.warning("cancel_booking: failed to restore sibling slots: %s", exc)

    return {
        "success": True,
        "booking_id": booking_id,
        "slot": canceled_slot,
        "message": format_cancellation_confirmation(canceled_slot),
    }


def reschedule_booking(booking_id: str, new_slot_id: str) -> Dict[str, Any]:
    table = get_availability_table()
    current_booking = find_booking_by_booking_id(booking_id)

    if not current_booking:
        return {"success": False, "message": "I couldn't find a booking with that confirmation number."}

    if str(current_booking.get("status", "")).upper() != "BOOKED":
        return {"success": False, "message": "That booking is not currently active, so it can't be rescheduled."}

    new_slot = get_slot_by_id(new_slot_id)

    if not new_slot:
        return {"success": False, "message": "I couldn't find the new appointment slot you selected."}

    if str(new_slot.get("status", "")).upper() != "AVAILABLE":
        return {"success": False, "message": "That new time is no longer available. Please choose another option."}

    old_slot_id = str(current_booking["slot_id"])

    if old_slot_id == new_slot_id:
        return {"success": False, "message": "That is already your current appointment time."}

    customer_name    = current_booking.get("customer_name")
    customer_phone   = current_booking.get("customer_phone")
    customer_email   = current_booking.get("customer_email")
    special_requests = current_booking.get("special_requests")

    update_expr = (
        "SET #status = :booked, booking_id = :booking_id, booked_at = :booked_at"
    )
    expr_names  = {"#status": "status"}
    expr_values = {
        ":booked":     "BOOKED",
        ":available":  "AVAILABLE",
        ":booking_id": booking_id,
        ":booked_at":  datetime.utcnow().isoformat(),
    }
    remove_fields = []

    if customer_name:
        update_expr += ", customer_name = :customer_name"
        expr_values[":customer_name"] = customer_name
    else:
        remove_fields.append("customer_name")

    if customer_phone:
        update_expr += ", customer_phone = :customer_phone"
        expr_values[":customer_phone"] = customer_phone
    else:
        remove_fields.append("customer_phone")

    if customer_email:
        update_expr += ", customer_email = :customer_email"
        expr_values[":customer_email"] = customer_email
    else:
        remove_fields.append("customer_email")

    if special_requests:
        update_expr += ", special_requests = :special_requests"
        expr_values[":special_requests"] = special_requests
    else:
        remove_fields.append("special_requests")

    if remove_fields:
        update_expr += " REMOVE " + ", ".join(remove_fields)

    try:
        table.update_item(
            Key={"slot_id": new_slot_id},
            UpdateExpression=update_expr,
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_values,
            ConditionExpression="#status = :available",
        )
    except Exception as exc:
        log.error("reschedule_booking: failed to book new slot_id=%s booking_id=%s: %s", new_slot_id, booking_id, exc, exc_info=True)
        return {"success": False, "message": "That new time was just taken. Please choose another option."}

    try:
        table.update_item(
            Key={"slot_id": old_slot_id},
            UpdateExpression=(
                "SET #status = :available "
                "REMOVE booking_id, booked_at, customer_name, customer_phone, customer_email, special_requests"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":available": "AVAILABLE",
                ":booked": "BOOKED",
                ":booking_id": booking_id,
            },
            ConditionExpression="booking_id = :booking_id AND #status = :booked",
        )
    except Exception as exc:
        log.error("reschedule_booking: failed to release old slot_id=%s booking_id=%s: %s", old_slot_id, booking_id, exc, exc_info=True)
        return {
            "success": False,
            "message": "I reserved the new time but couldn't fully clear the old one. Please review that record.",
        }

    updated_slot = {**new_slot, "status": "BOOKED", "booking_id": booking_id}
    if customer_name:
        updated_slot["customer_name"] = customer_name
    if customer_phone:
        updated_slot["customer_phone"] = customer_phone
    if customer_email:
        updated_slot["customer_email"] = customer_email
    if special_requests:
        updated_slot["special_requests"] = special_requests

    return {
        "success": True,
        "booking_id": booking_id,
        "old_slot": current_booking,
        "slot": updated_slot,
        "message": format_reschedule_confirmation(updated_slot),
    }


# ---------------------------------------------------------------------------
# Cancel / reschedule flows
# ---------------------------------------------------------------------------

def begin_cancel_flow(session_id: str, message: str) -> Dict[str, Any]:
    booking_id = _extract_booking_id(message)
    if booking_id:
        clear_cancel_state(session_id)
        return cancel_booking(booking_id)
    get_session_state(session_id)["awaiting_cancel_booking_id"] = True
    return {
        "success": True,
        "needs_clarification": True,
        "message": "Please send your confirmation number so I can cancel the appointment.",
    }


def continue_cancel_flow(session_id: str, message: str) -> Dict[str, Any]:
    state = get_session_state(session_id)
    if not state.get("awaiting_cancel_booking_id"):
        return {"success": False, "message": "I'm not currently waiting on a cancellation confirmation number."}

    booking_id = _extract_booking_id(message)
    if not booking_id:
        return {
            "success": False,
            "needs_clarification": True,
            "message": "Please send the confirmation number in a format like bk_123abc456def.",
        }

    clear_cancel_state(session_id)
    result = cancel_booking(booking_id)
    if not result.get("success"):
        get_session_state(session_id)["awaiting_cancel_booking_id"] = True
    return result


def begin_reschedule_flow(session_id: str, message: str) -> Dict[str, Any]:
    booking_id = _extract_booking_id(message)
    state = get_session_state(session_id)

    if not booking_id:
        state["awaiting_reschedule_booking_id"] = True
        return {
            "success": True,
            "needs_clarification": True,
            "message": "Please send your confirmation number so I can help reschedule the appointment.",
        }

    booking = find_booking_by_booking_id(booking_id)
    if not booking:
        state["awaiting_reschedule_booking_id"] = True
        return {
            "success": False,
            "needs_clarification": True,
            "message": "I couldn't find a booking with that confirmation number. Please double-check and send it again.",
        }

    if str(booking.get("status", "")).upper() != "BOOKED":
        state["awaiting_reschedule_booking_id"] = True
        return {
            "success": False,
            "needs_clarification": True,
            "message": "That booking doesn't appear to be active. Please check the confirmation number and try again.",
        }

    state["pending_reschedule_booking_id"] = booking_id
    state["pending_reschedule_booking"] = booking

    service_name = booking.get("service_name", "your service")
    date_text    = _format_display_date(str(booking.get("date", "")))
    time_text    = _to_12hr(str(booking.get("start_time", "your current time")))

    return {
        "success": True,
        "needs_clarification": True,
        "message": (
            f"Got it. I found your {service_name} appointment on {date_text} at {time_text}. "
            "Ask me for a new date or say a time you'd prefer next."
        ),
    }


def continue_reschedule_booking_id_flow(session_id: str, message: str) -> Dict[str, Any]:
    state = get_session_state(session_id)
    if not state.get("awaiting_reschedule_booking_id"):
        return {"success": False, "message": "I'm not currently waiting on a reschedule confirmation number."}

    booking_id = _extract_booking_id(message)
    if not booking_id:
        return {
            "success": False,
            "needs_clarification": True,
            "message": "Please send the confirmation number in a format like bk_123abc456def.",
        }

    state.pop("awaiting_reschedule_booking_id", None)
    return begin_reschedule_flow(session_id, booking_id)


def set_reschedule_options(session_id: str, slots: List[Dict[str, Any]]) -> None:
    get_session_state(session_id)["last_reschedule_slots"] = slots


def finalize_reschedule_from_message(session_id: str, message: str) -> Dict[str, Any]:
    state = get_session_state(session_id)
    booking_id = state.get("pending_reschedule_booking_id")

    if not booking_id:
        return {"success": False, "message": "I couldn't find the booking you're trying to reschedule."}

    selected_slot = _find_slot_from_last_reschedule_options(session_id, message)
    if not selected_slot:
        return {
            "success": False,
            "needs_clarification": True,
            "message": "Please choose one of the new time options I shared.",
        }

    result = reschedule_booking(booking_id=booking_id, new_slot_id=str(selected_slot["slot_id"]))
    if result.get("success"):
        clear_reschedule_state(session_id)
    return result


# ---------------------------------------------------------------------------
# Confirmation formatters
# ---------------------------------------------------------------------------

def _to_12hr(t: str) -> str:
    try:
        h, m = t.split(":")
        hr = int(h)
        return f"{hr % 12 or 12}:{m} {'AM' if hr < 12 else 'PM'}"
    except Exception:
        return t

def _to_12hr(t: str) -> str:
    try:
        h, m = t.split(":")
        hr = int(h)
        return f"{hr % 12 or 12}:{m} {'AM' if hr < 12 else 'PM'}"
    except Exception:
        return t

def format_booking_confirmation(slot: Dict[str, Any]) -> str:
    service_name     = slot.get("service_name", "your service")
    date_text        = _format_display_date(str(slot.get("date", "")))
    time_text        = _to_12hr(str(slot.get("start_time", "your selected time")))
    staff_name       = slot.get("staff_name")
    customer_name    = slot.get("customer_name")
    customer_phone   = slot.get("customer_phone")
    customer_email   = slot.get("customer_email")
    special_requests = slot.get("special_requests")
    booking_id       = slot.get("booking_id")

    lines = [f"Perfect, {customer_name} — you're all set." if customer_name else "Perfect — your appointment is booked."]

    appointment_line = f"{service_name} on {date_text} at {time_text}"
    if staff_name:
        appointment_line += f" with {staff_name}"
    lines.append(appointment_line)

    if booking_id:
        lines.append(f"Confirmation number: {booking_id}")
    if customer_phone:
        lines.append(f"Phone: {customer_phone}")
    if customer_email:
        lines.append(f"Email: {customer_email}")
        lines.append("We'll send your confirmation details there.")
    if special_requests:
        lines.append(f"Special requests: {special_requests}")

    return "\n".join(lines)


def format_cancellation_confirmation(slot: Dict[str, Any]) -> str:
    service_name   = slot.get("service_name", "your service")
    date_text      = _format_display_date(str(slot.get("date", "")))
    time_text      = _to_12hr(str(slot.get("start_time", "your selected time")))
    staff_name     = slot.get("staff_name")
    customer_name  = slot.get("customer_name")
    customer_email = slot.get("customer_email")
    booking_id     = slot.get("booking_id")

    opener = f"Got it, {customer_name} — your appointment has been canceled." if customer_name else "Your appointment has been canceled."
    appointment_line = f"{service_name} on {date_text} at {time_text}"
    if staff_name:
        appointment_line += f" with {staff_name}"

    lines = [opener, appointment_line]
    if booking_id:
        lines.append(f"Confirmation number: {booking_id}")
    if customer_email:
        lines.append(f"A cancellation notice will be sent to {customer_email}.")
    return "\n".join(lines)


def format_reschedule_confirmation(slot: Dict[str, Any]) -> str:
    service_name   = slot.get("service_name", "your service")
    date_text      = _format_display_date(str(slot.get("date", "")))
    time_text      = _to_12hr(str(slot.get("start_time", "your selected time")))
    staff_name     = slot.get("staff_name")
    customer_name  = slot.get("customer_name")
    customer_email = slot.get("customer_email")
    booking_id     = slot.get("booking_id")

    opener = f"Done, {customer_name} — your appointment has been rescheduled." if customer_name else "Your appointment has been rescheduled."
    appointment_line = f"{service_name} on {date_text} at {time_text}"
    if staff_name:
        appointment_line += f" with {staff_name}"

    lines = [opener, appointment_line]
    if booking_id:
        lines.append(f"Confirmation number: {booking_id}")
    if customer_email:
        lines.append(f"Updated confirmation will be sent to {customer_email}.")
    return "\n".join(lines)
