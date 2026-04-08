from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from app.bookings import (
    begin_booking_intake,
    begin_cancel_flow,
    begin_reschedule_flow,
    continue_booking_intake,
    continue_cancel_flow,
    continue_reschedule_booking_id_flow,
    finalize_reschedule_from_message,
    get_session_state,
    save_presented_slots,
)
from app.intent_router import detect_intent, _extract_date
from app.scheduling import format_slots_for_response, get_available_slots_for_service
from app.services import list_services

try:
    from app.booking_history import get_bookings_by_email, format_history_for_concierge
except Exception:
    get_bookings_by_email = None
    format_history_for_concierge = None

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core response builder
# ---------------------------------------------------------------------------

def _response(text: str, session_id: Optional[str] = None, **extra: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"response": text}
    if session_id:
        payload["session_id"] = session_id
    payload.update(extra)
    return payload


def _normalize(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


# ---------------------------------------------------------------------------
# Generic safe-call utility
# ---------------------------------------------------------------------------

def _safe_call(
    fallback_text: str,
    session_id: str,
    *callables: Callable[[], Any],
) -> Dict[str, Any]:
    for fn in callables:
        try:
            result = fn()
        except TypeError as exc:
            log.debug("_safe_call signature mismatch: %s", exc)
            continue
        except Exception as exc:
            log.warning("_safe_call error: %s", exc, exc_info=True)
            continue

        if isinstance(result, str):
            return _response(result, session_id)
        if isinstance(result, dict):
            payload = dict(result)
            payload.setdefault("session_id", session_id)
            payload.setdefault("response", payload.pop("message", fallback_text))
            return payload

    return _response(fallback_text, session_id)


def _begin_cancel(session_id: str, message: str) -> Dict[str, Any]:
    return _safe_call(
        "I can help cancel your booking. Please share your booking ID.",
        session_id,
        lambda: begin_cancel_flow(session_id, message),
        lambda: begin_cancel_flow(session_id=session_id, message=message),
    )


def _continue_cancel(session_id: str, message: str) -> Dict[str, Any]:
    return _safe_call(
        "I can help cancel your booking. Please share your booking ID.",
        session_id,
        lambda: continue_cancel_flow(session_id, message),
        lambda: continue_cancel_flow(session_id=session_id, message=message),
    )


def _begin_reschedule(session_id: str, message: str) -> Dict[str, Any]:
    return _safe_call(
        "I can help reschedule your appointment. Please share your booking ID.",
        session_id,
        lambda: begin_reschedule_flow(session_id, message),
        lambda: begin_reschedule_flow(session_id=session_id, message=message),
    )


def _continue_reschedule(session_id: str, message: str) -> Dict[str, Any]:
    return _safe_call(
        "I can help reschedule your appointment. Please share your booking ID.",
        session_id,
        lambda: continue_reschedule_booking_id_flow(session_id, message),
        lambda: continue_reschedule_booking_id_flow(session_id=session_id, message=message),
        lambda: finalize_reschedule_from_message(session_id, message),
        lambda: finalize_reschedule_from_message(session_id=session_id, message=message),
    )


def _continue_booking(session_id: str, message: str) -> Dict[str, Any]:
    return _safe_call(
        "I'm sorry, I couldn't continue the booking flow.",
        session_id,
        lambda: continue_booking_intake(session_id, message),
        lambda: continue_booking_intake(session_id=session_id, message=message),
    )


# ---------------------------------------------------------------------------
# Slot fetching
# ---------------------------------------------------------------------------

def _fetch_slots(
    service_name: str,
    date: Optional[str],
    time_of_day: Optional[str],
) -> List[Dict[str, Any]]:
    try:
        return get_available_slots_for_service(
            service_name=service_name,
            requested_date=date,
            time_of_day=time_of_day,
        )
    except Exception as exc:
        log.warning("_fetch_slots error: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Service helpers
# ---------------------------------------------------------------------------

def _all_services() -> List[Dict[str, Any]]:
    try:
        svcs = list_services() or []
        if isinstance(svcs, dict):
            svcs = svcs.get("services", svcs.get("items", []))
        return [s for s in svcs if isinstance(s, dict)]
    except Exception as exc:
        log.warning("_all_services error: %s", exc)
        return []


def _service_name(service: Dict[str, Any]) -> str:
    return str(service.get("name", "")).strip()


def _service_category(service: Dict[str, Any]) -> str:
    return str(service.get("category", "")).strip().lower()


# Keyword -> (intro, name-match terms, category-match terms)
_SERVICE_FILTERS: List[tuple] = [
    ("facial",  "Here are our available facials:",
        ["facial"], ["facial"]),
    ("massage", "Here are our available massages:",
        ["massage"], ["massage"]),
    ("scrub",   "Here are our available body treatments:",
        ["scrub", "wrap", "body"], ["body treatment", "body"]),
    ("wrap",    "Here are our available body treatments:",
        ["scrub", "wrap", "body"], ["body treatment", "body"]),
    ("aroma",   "Here is our available enhancement:",
        ["aromatherapy", "add-on"], ["enhancement"]),
]


def _format_service(svc: Dict[str, Any]) -> str:
    name     = _service_name(svc)
    duration = svc.get("duration_minutes")
    price    = svc.get("price")
    desc     = svc.get("description", "")
    parts    = []
    if duration:
        parts.append(f"{duration} min")
    if price is not None:
        try:
            parts.append(f"${int(price)}")
        except (ValueError, TypeError):
            parts.append(str(price))
    line = f"• {name}"
    if parts:
        line += f"  ({' · '.join(parts)})"
    if desc:
        line += f"\n  {desc}"
    return line


def _format_service_list(msg: str) -> str:
    services = _all_services()
    if not services:
        return "I couldn't find any services right now. Please try again in a moment."

    intro   = "Here are our available services:"
    matched = None

    for keyword, kw_intro, name_terms, cat_terms in _SERVICE_FILTERS:
        if keyword in msg:
            matched = [
                s for s in services
                if any(t in _service_name(s).lower() for t in name_terms)
                or any(t in _service_category(s) for t in cat_terms)
            ]
            intro = kw_intro
            break

    display = sorted(
        matched if matched is not None else services,
        key=lambda s: _service_name(s).lower()
    )

    if not display:
        return "I couldn't find any matching services right now."

    return intro + "\n\n" + "\n\n".join(_format_service(s) for s in display)


def _resolve_service(intent: Dict[str, Any], msg: str) -> Optional[str]:
    """Resolve service name from intent or raw message, verified against real service list."""
    if intent.get("service_name"):
        intent_name = intent["service_name"].lower()
        # Try to match against real service names (fuzzy)
        for svc in _all_services():
            name = _service_name(svc).lower()
            if name == intent_name or intent_name in name or name in intent_name:
                return _service_name(svc)
        # Return as-is if not matched — booking layer will handle it
        return intent["service_name"]

    # Scan message for service names — longest match first to avoid partial hits
    for svc in sorted(_all_services(), key=lambda s: len(_service_name(s)), reverse=True):
        name = _service_name(svc)
        if name and name.lower() in msg:
            return name
    return None


# ---------------------------------------------------------------------------
# Date / time helpers
# ---------------------------------------------------------------------------

def _pretty_date(iso_date: str) -> str:
    try:
        return datetime.strptime(iso_date, "%Y-%m-%d").strftime("%B %d, %Y")
    except Exception:
        return iso_date


def _extract_time_of_day(msg: str) -> Optional[str]:
    if "morning" in msg:
        return "morning"
    if "afternoon" in msg:
        return "afternoon"
    if "evening" in msg or "tonight" in msg:
        return "evening"
    return None


def _time_to_minutes(t: str) -> Optional[int]:
    """Convert '9 AM', '9:00AM', '10:30 PM' etc. to minutes since midnight."""
    t = re.sub(r"\s+", "", t).upper()
    for fmt in ("%I:%M%p", "%I%p"):
        try:
            p = datetime.strptime(t, fmt)
            return p.hour * 60 + p.minute
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Session state helpers
# ---------------------------------------------------------------------------

def _state_value(state: Dict[str, Any], *keys: str) -> Optional[Any]:
    for key in keys:
        value = state.get(key)
        if value not in (None, "", []):
            return value
    return None


def _active_flow(state: Dict[str, Any]) -> str:
    return str(
        _state_value(state, "flow", "active_flow", "intent", "mode", "current_intent") or ""
    ).lower()


def _booking_active(state: Dict[str, Any]) -> bool:
    if not state:
        return False
    flow = _active_flow(state)
    if flow in {"booking", "book", "booking_request", "reserve"}:
        return True
    stage = str(_state_value(state, "stage", "status", "booking_stage") or "").lower()
    if any(k in stage for k in ("booking", "collect", "slot")):
        return True
    if state.get("collecting_booking_details") is True:
        return True
    if state.get("awaiting_field"):
        return True
    return False


# ---------------------------------------------------------------------------
# Pending booking state
# ---------------------------------------------------------------------------

def _set_pending_service(session_id: str, service_name: str) -> None:
    state = get_session_state(session_id)
    state["pending_booking_service"] = service_name
    state["awaiting_booking_date"]   = True


def _clear_pending_booking(session_id: str) -> None:
    state = get_session_state(session_id)
    for key in ("pending_booking_service", "pending_booking_date",
                "awaiting_booking_date", "awaiting_booking_time",
                "last_presented_slots"):
        state.pop(key, None)


# ---------------------------------------------------------------------------
# Pre-fill booking fields from logged-in user session
# ---------------------------------------------------------------------------

def _prefill_user_booking_fields(session_id: str) -> None:
    state = get_session_state(session_id)
    if state.get("user_name") and not state.get("booking_name"):
        state["booking_name"] = state["user_name"]
        log.debug("Pre-filled booking_name: %s", state["user_name"])
    if state.get("user_email") and not state.get("booking_email"):
        state["booking_email"] = state["user_email"]
        log.debug("Pre-filled booking_email: %s", state["user_email"])


# ---------------------------------------------------------------------------
# Slot presentation
# ---------------------------------------------------------------------------

def _present_slots(
    service_name: str,
    date: str,
    time_of_day: Optional[str],
    session_id: str,
) -> Dict[str, Any]:
    slots = _fetch_slots(service_name, date, time_of_day)

    if not slots:
        return _response(
            f"I couldn't find any availability for {service_name} on {_pretty_date(date)}. "
            "Would you like to try a different date?",
            session_id,
        )

    try:
        save_presented_slots(session_id, slots, service_name)
    except Exception as exc:
        log.warning("save_presented_slots error: %s", exc)

    state = get_session_state(session_id)
    state["pending_booking_service"] = service_name
    state["pending_booking_date"]    = date
    state["awaiting_booking_time"]   = True
    state.pop("awaiting_booking_date", None)

    return _response(
        f"Here are the available times for {service_name} on {_pretty_date(date)}:\n\n"
        f"{format_slots_for_response(slots)}\n\n"
        "Which time would you like to book?",
        session_id,
    )


# ---------------------------------------------------------------------------
# Time slot selection — handles all reasonable input formats
# ---------------------------------------------------------------------------

def _try_start_intake(
    message: str,
    msg: str,
    session_id: str,
) -> Optional[Dict[str, Any]]:
    state = get_session_state(session_id)
    if not state.get("awaiting_booking_time"):
        return None

    slots = state.get("last_presented_slots", [])
    if not slots:
        return None

    selected_slot = None

    # 1. Ordinal words: "first", "second", "1st", "2nd", etc.
    ordinal_map = {
        "first": 0, "1st": 0, "one": 0,
        "second": 1, "2nd": 1, "two": 1,
        "third": 2, "3rd": 2, "three": 2,
        "fourth": 3, "4th": 3, "four": 3,
        "fifth": 4, "5th": 4, "five": 4,
        "sixth": 5, "6th": 5, "six": 5,
        "seventh": 6, "7th": 6, "seven": 6,
        "eighth": 7, "8th": 7, "eight": 7,
    }
    for word, idx in ordinal_map.items():
        if re.search(rf"\b{word}\b", msg):
            if idx < len(slots):
                selected_slot = slots[idx]
            break

    # 2. Bare digit: "1" through "8"
    if not selected_slot and re.fullmatch(r"[1-8]", message.strip()):
        idx = int(message.strip()) - 1
        if idx < len(slots):
            selected_slot = slots[idx]

    # 3. Time string with AM/PM: "9 AM", "9:00 AM", "9am", "10:30pm"
    if not selected_slot:
        time_m = re.search(
            r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm))\b",
            message, re.IGNORECASE
        )
        if time_m:
            req_min = _time_to_minutes(time_m.group(1))
            if req_min is not None:
                for slot in slots:
                    slot_min = _time_to_minutes(str(slot.get("start_time", "")))
                    if slot_min is not None and req_min == slot_min:
                        selected_slot = slot
                        break
            # Fallback substring match
            if not selected_slot:
                req_norm = re.sub(r"\s+", "", time_m.group(1)).upper()
                for slot in slots:
                    slot_norm = re.sub(r"\s+", "", str(slot.get("start_time", ""))).upper()
                    if req_norm in slot_norm or slot_norm.startswith(req_norm):
                        selected_slot = slot
                        break

    # 4. Bare hour number: "9", "10" (no AM/PM) — try AM first for spa hours
    if not selected_slot:
        bare_m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?", message.strip())
        if bare_m:
            hour   = int(bare_m.group(1))
            minute = int(bare_m.group(2)) if bare_m.group(2) else 0
            for ampm_offset in (0, 12):
                h      = (hour + ampm_offset) if hour != 12 else hour
                target = h * 60 + minute
                for slot in slots:
                    slot_min = _time_to_minutes(str(slot.get("start_time", "")))
                    if slot_min is not None and slot_min == target:
                        selected_slot = slot
                        break
                if selected_slot:
                    break

    # 5. Single slot + affirmative response
    if not selected_slot and len(slots) == 1:
        affirmatives = (
            "that", "yes", "ok", "sure", "sounds good", "perfect",
            "good", "great", "works", "fine", "please", "that one",
            "that works", "let's do", "i'll take", "book it", "confirm",
        )
        if any(w in msg for w in affirmatives):
            selected_slot = slots[0]

    if not selected_slot:
        return None

    state.pop("awaiting_booking_time", None)
    service_name = state.get("pending_booking_service") or selected_slot.get("service_name")

    _prefill_user_booking_fields(session_id)

    return _safe_call(
        "I'm sorry, I couldn't start the booking.",
        session_id,
        lambda: begin_booking_intake(session_id, selected_slot, service_name),
        lambda: begin_booking_intake(session_id=session_id, slot=selected_slot,
                                     service_name=service_name),
    )


# ---------------------------------------------------------------------------
# Intent handlers
# ---------------------------------------------------------------------------

def _handle_service_question(msg: str, session_id: str, service_name: Optional[str] = None) -> Dict[str, Any]:
    # If a specific service was mentioned, describe just that one
    if service_name:
        svcs = _all_services()
        for svc in svcs:
            name = _service_name(svc)
            if name.lower() == service_name.lower() or service_name.lower() in name.lower():
                duration = svc.get("duration_minutes")
                price    = svc.get("price")
                desc     = svc.get("description", "")
                details  = []
                if duration: details.append(f"{duration} minutes")
                if price is not None:
                    try: details.append(f"${int(price)}")
                    except: pass
                detail_str = " · ".join(details)
                lines = [f"{name}"]
                if detail_str: lines.append(detail_str)
                if desc: lines.append(desc)
                lines.append(f"\nWould you like to book a {name}?")
                return _response("\n".join(lines), session_id)
    return _response(_format_service_list(msg), session_id)


def _handle_availability(intent: Dict[str, Any], msg: str, session_id: str) -> Dict[str, Any]:
    service_name = _resolve_service(intent, msg)
    if not service_name:
        return _response(
            "Which service would you like to check availability for?", session_id
        )
    date = intent.get("date")
    if not date:
        _set_pending_service(session_id, service_name)
        return _response(
            f"What date would you like to check availability for {service_name}?",
            session_id,
        )
    return _present_slots(service_name, date, _extract_time_of_day(msg), session_id)


def _handle_booking(intent: Dict[str, Any], msg: str, session_id: str) -> Dict[str, Any]:
    service_name = _resolve_service(intent, msg)
    if not service_name:
        return _response("What service would you like to book?", session_id)
    date = intent.get("date")
    if not date:
        _set_pending_service(session_id, service_name)
        return _response(
            f"What date would you like to book your {service_name}?",
            session_id,
        )
    return _present_slots(service_name, date, _extract_time_of_day(msg), session_id)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def handle_chat(
    message: str,
    session_id: str = "default",
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    message = (message or "").strip()
    if not message:
        return _response("How can I help you today?", session_id)

    context      = context or {}
    user_name    = context.get("user_name")
    user_email   = context.get("user_email")
    user_token   = context.get("user_token")
    is_logged_in = bool(user_token and user_name)

    msg   = _normalize(message)
    state = get_session_state(session_id) or {}

    # Persist user context so booking intake can pre-fill fields
    if is_logged_in:
        state["user_name"]  = user_name
        state["user_email"] = user_email
        state["user_token"] = user_token

    flow = _active_flow(state)

    # ── 1. Active booking intake ──────────────────────────────────────────
    if _booking_active(state):
        if not state.get("pending_booking_slot") and state.get("awaiting_field"):
            get_session_state(session_id).clear()
            return _response(
                "It looks like your session timed out. No worries — what would you like to book?",
                session_id,
            )
        return _continue_booking(session_id, message)

    # ── 2. Cancel flow ────────────────────────────────────────────────────
    if flow in {"cancel", "cancel_request"} or state.get("awaiting_cancel_booking_id"):
        return _continue_cancel(session_id, message)

    # ── 3. Reschedule flow ────────────────────────────────────────────────
    if flow in {"reschedule", "reschedule_request"} or state.get("awaiting_reschedule_booking_id"):
        return _continue_reschedule(session_id, message)

    # ── 3b. Reschedule date follow-up ─────────────────────────────────────
    if state.get("pending_reschedule_booking_id"):
        date = _extract_date(message)
        if date:
            booking      = state.get("pending_reschedule_booking")
            service_name = booking.get("service_name", "") if booking else ""
            slots        = _fetch_slots(service_name, date, _extract_time_of_day(msg))
            if not slots:
                return _response(
                    f"I couldn't find any availability on {_pretty_date(date)}. "
                    "Would you like to try a different date?",
                    session_id,
                )
            try:
                from app.bookings import set_reschedule_options
                set_reschedule_options(session_id, slots)
            except Exception as exc:
                log.warning("set_reschedule_options error: %s", exc)
            try:
                save_presented_slots(session_id, slots)
            except Exception as exc:
                log.warning("save_presented_slots (reschedule) error: %s", exc)
            state["awaiting_reschedule_slot"] = True
            return _response(
                "Here are the available times on {}:\n\n{}\n\nWhich time would you like?".format(
                    _pretty_date(date), format_slots_for_response(slots)
                ),
                session_id,
            )
        return _safe_call(
            "Please let me know which date you'd like to reschedule to.",
            session_id,
            lambda: finalize_reschedule_from_message(session_id, message),
            lambda: finalize_reschedule_from_message(session_id=session_id, message=message),
        )

    # ── 4a. Reschedule slot selection ─────────────────────────────────────
    if state.get("awaiting_reschedule_slot"):
        result = _safe_call(
            "Please choose one of the time options I listed.",
            session_id,
            lambda: finalize_reschedule_from_message(session_id, message),
            lambda: finalize_reschedule_from_message(session_id=session_id, message=message),
        )
        if result.get("response", "").startswith(("Your appointment", "Done,")):
            state.pop("awaiting_reschedule_slot", None)
        return result

    # ── 4b. Booking time slot selection ───────────────────────────────────
    intake = _try_start_intake(message, msg, session_id)
    if intake:
        return intake

    # Still awaiting a time but nothing matched — re-prompt clearly
    if state.get("awaiting_booking_time"):
        slots = state.get("last_presented_slots", [])
        if slots:
            return _response(
                "I didn't catch that. Please choose a time from the list above — "
                "you can type the time (like '9 AM' or '10:30 AM') or just the number.",
                session_id,
            )
        # Slots missing — reset and start over
        _clear_pending_booking(session_id)
        return _response(
            "I lost track of the available slots. What service and date would you like to book?",
            session_id,
        )

    # ── 5. Date follow-up (service known, waiting for date) ───────────────
    if state.get("awaiting_booking_date"):
        date         = _extract_date(message)
        service_name = state.get("pending_booking_service")

        if date and service_name:
            state.pop("awaiting_booking_date", None)
            return _present_slots(service_name, date, _extract_time_of_day(msg), session_id)

        return _response(
            "I didn't catch a date. You can say something like 'tomorrow', 'April 15th', "
            "or 'next Friday'.",
            session_id,
        )

    # ── 6. Booking history ────────────────────────────────────────────────
    history_triggers = (
        "my bookings", "my appointments", "my history", "show my",
        "what have i booked", "upcoming appointments", "past appointments",
        "my upcoming", "my past", "appointment history",
    )
    if any(t in msg for t in history_triggers):
        if is_logged_in and get_bookings_by_email and format_history_for_concierge:
            bookings = get_bookings_by_email(user_email)
            return _response(format_history_for_concierge(bookings), session_id)
        return _response(
            "Please sign in to your account to view your appointment history.",
            session_id,
        )

    # ── 7. LLM intent detection ───────────────────────────────────────────
    intent   = detect_intent(message)
    detected = intent.get("intent", "unknown")
    log.debug(
        "detect_intent(%r) -> %s | service=%s date=%s",
        message, detected, intent.get("service_name"), intent.get("date"),
    )

    if detected == "service_question":
        return _handle_service_question(msg, session_id, intent.get("service_name"))

    if detected == "availability_check":
        return _handle_availability(intent, msg, session_id)

    if detected == "booking_request":
        return _handle_booking(intent, msg, session_id)

    if detected == "cancel_request":
        return _begin_cancel(session_id, message)

    if detected == "reschedule_request":
        return _begin_reschedule(session_id, message)

    # ── 8. Graceful fallback ──────────────────────────────────────────────
    name_part = f", {user_name.split()[0]}" if is_logged_in and user_name else ""
    return _response(
        f"I'm here to help{name_part}. I can explore services, check availability, "
        "book an appointment, reschedule, or cancel a booking. What can I do for you?",
        session_id,
    )
