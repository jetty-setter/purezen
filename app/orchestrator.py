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
    return [svc for svc in (list_services() or []) if isinstance(svc, dict)]


def _service_name(service: Dict[str, Any]) -> str:
    return str(service.get("name", "")).strip()


_SERVICE_KEYWORDS = [
    ("facial",  "Here are our available facials:"),
    ("massage", "Here are our available massages:"),
    ("scrub",   "Here are our available body treatments:"),
]


def _format_service_list(msg: str) -> str:
    services = _all_services()
    intro = "Here are our available services:"

    for keyword, kw_intro in _SERVICE_KEYWORDS:
        if keyword in msg:
            services = [s for s in services if keyword in _service_name(s).lower()]
            intro = kw_intro
            break

    names = sorted(_service_name(s) for s in services if _service_name(s))
    if not names:
        return "I couldn't find any matching services right now."
    return intro + "\n\n" + "\n".join(f"• {name}" for name in names)


def _resolve_service(intent: Dict[str, Any], msg: str) -> Optional[str]:
    if intent.get("service_name"):
        return intent["service_name"]
    for svc in _all_services():
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
    # Active intake: bookings.py sets awaiting_field when collecting name/phone/email
    if state.get("awaiting_field"):
        return True
    return False


# ---------------------------------------------------------------------------
# Pending booking state — multi-turn "what date?" / "what time?" flows
# ---------------------------------------------------------------------------

def _set_pending_service(session_id: str, service_name: str) -> None:
    state = get_session_state(session_id)
    state["pending_booking_service"] = service_name
    state["awaiting_booking_date"] = True


def _clear_pending_service(session_id: str) -> None:
    state = get_session_state(session_id)
    for key in ("pending_booking_service", "pending_booking_date",
                "awaiting_booking_date", "awaiting_booking_time"):
        state.pop(key, None)


# ---------------------------------------------------------------------------
# Slot presentation + intake launch
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

    # Store state for the follow-up time selection
    state = get_session_state(session_id)
    state["pending_booking_service"] = service_name
    state["pending_booking_date"] = date
    state["awaiting_booking_time"] = True
    state.pop("awaiting_booking_date", None)

    intro = f"Here are the available times for {service_name} on {_pretty_date(date)}:"
    return _response(
        f"{intro}\n\n{format_slots_for_response(slots)}\n\nWhich time would you like to book?",
        session_id,
    )


def _try_start_intake(
    message: str,
    msg: str,
    session_id: str,
) -> Optional[Dict[str, Any]]:
    """
    If state says we're awaiting a time selection, try to match the user's
    message to a presented slot and start the booking intake.
    Returns None if this doesn't look like a time selection.
    """
    state = get_session_state(session_id)
    if not state.get("awaiting_booking_time"):
        return None

    slots = state.get("last_presented_slots", [])
    if not slots:
        return None

    selected_slot = None

    # Ordinal: "the first one", "second", "1st" etc.
    ordinal_map = {
        "first": 0, "1st": 0, "one": 0,
        "second": 1, "2nd": 1, "two": 1,
        "third": 2, "3rd": 2, "three": 2,
        "fourth": 3, "4th": 3, "four": 3,
    }
    for word, idx in ordinal_map.items():
        if re.search(rf"\b{word}\b", msg):
            if idx < len(slots):
                selected_slot = slots[idx]
            break

    # Time match: "9am", "9:00 AM", "9:00", or bare "9" / "10" / "11"
    if not selected_slot:
        # Full time with am/pm: "9am", "9:00 AM", "9:30pm"
        time_m = re.search(r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm))\b", message, re.IGNORECASE)
        if time_m:
            requested = time_m.group(1).strip().upper().replace(" ", "")
            for slot in slots:
                slot_time = str(slot.get("start_time", "")).upper().replace(" ", "")
                if requested in slot_time or slot_time in requested:
                    selected_slot = slot
                    break

    if not selected_slot:
        # Bare inputs: "9", "9:30", "930", "1030" — match against slot start_time
        raw = message.strip()

        # Normalise "930" -> "9:30", "1030" -> "10:30", "1200" -> "12:00"
        compact_m = re.fullmatch(r"(\d{1,2})(\d{2})", raw)
        if compact_m:
            raw = "{}:{}".format(compact_m.group(1), compact_m.group(2))

        bare_m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?", raw)
        if bare_m:
            hour = int(bare_m.group(1))
            minute = int(bare_m.group(2)) if bare_m.group(2) else 0
            for slot in slots:
                raw_time = str(slot.get("start_time", ""))
                try:
                    for fmt in ("%I:%M %p", "%I %p", "%I:%M%p", "%I%p"):
                        try:
                            parsed = datetime.strptime(raw_time.strip().upper(), fmt)
                            if parsed.hour == hour and parsed.minute == minute:
                                selected_slot = slot
                            break  # parsed successfully — stop trying formats
                        except ValueError:
                            continue
                except Exception:
                    pass
                if selected_slot:
                    break

    # Single slot + affirmative
    if not selected_slot and len(slots) == 1:
        if any(w in msg for w in ("that", "yes", "ok", "sure", "sounds good", "perfect", "good")):
            selected_slot = slots[0]

    if not selected_slot:
        return None

    state.pop("awaiting_booking_time", None)
    # Always use the service the user asked for, not what the slot row says in the DB.
    # Slots can be shared across service types; pending_booking_service is the source of truth.
    service_name = state.get("pending_booking_service") or selected_slot.get("service_name")

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

def _handle_service_question(msg: str, session_id: str) -> Dict[str, Any]:
    return _response(_format_service_list(msg), session_id)


def _handle_availability(intent: Dict[str, Any], msg: str, session_id: str) -> Dict[str, Any]:
    service_name = _resolve_service(intent, msg)
    if not service_name:
        return _response("Which service would you like to check availability for?", session_id)

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
            f"What date would you like to book for {service_name}?",
            session_id,
        )

    return _present_slots(service_name, date, _extract_time_of_day(msg), session_id)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def handle_chat(message: str, session_id: str = "default") -> Dict[str, Any]:
    message = (message or "").strip()
    if not message:
        return _response("How can I help you today?", session_id)

    msg = _normalize(message)
    state = get_session_state(session_id) or {}
    flow = _active_flow(state)

    # 1. Active booking intake (collecting name / phone / email / special requests)
    if _booking_active(state):
        # Guard against server restart wiping slot data mid-flow — give user a clean recovery
        if not state.get("pending_booking_slot") and state.get("awaiting_field"):
            get_session_state(session_id).clear()
            return _response(
                "It looks like your session expired. No worries — what would you like to book?",
                session_id,
            )
        return _continue_booking(session_id, message)

    # 2. Cancel flow — bookings.py sets awaiting_cancel_booking_id, not a flow key
    if flow in {"cancel", "cancel_request"} or state.get("awaiting_cancel_booking_id"):
        return _continue_cancel(session_id, message)

    # 3. Reschedule flow — two distinct stages need different handlers
    if flow in {"reschedule", "reschedule_request"} or state.get("awaiting_reschedule_booking_id"):
        # Still waiting for the booking ID — route through the ID collection flow
        return _continue_reschedule(session_id, message)

    if state.get("pending_reschedule_booking_id"):
        # Booking already found — user is now providing a new date or picking a slot.
        # Go straight to finalize rather than back through the ID flow.
        date = _extract_date(message)
        if date:
            # User gave a date — fetch slots for that date and present them
            booking = state.get("pending_reschedule_booking")
            service_name = booking.get("service_name", "") if booking else ""
            slots = _fetch_slots(service_name, date, _extract_time_of_day(msg))
            if not slots:
                return _response(
                    f"I couldn't find any availability on {_pretty_date(date)}. Try a different date?",
                    session_id,
                )
            try:
                from app.bookings import set_reschedule_options
                set_reschedule_options(session_id, slots)
            except Exception as exc:
                log.warning("set_reschedule_options error: %s", exc)
            from app.scheduling import format_slots_for_response
            return _response(
                "Here are the available times on {}:\n\n{}\n\nWhich time would you like?".format(
                    _pretty_date(date), format_slots_for_response(slots)
                ),
                session_id,
            )
        # No date — try to match against previously presented reschedule slots
        result = _safe_call(
            "Please choose one of the new time options I shared.",
            session_id,
            lambda: finalize_reschedule_from_message(session_id, message),
            lambda: finalize_reschedule_from_message(session_id=session_id, message=message),
        )
        return result

    # 4. Time selection follow-up
    intake = _try_start_intake(message, msg, session_id)
    if intake:
        return intake

    # 5. Date follow-up ("what date?" was asked, user is answering)
    if state.get("awaiting_booking_date"):
        date = _extract_date(message)
        service_name = state.get("pending_booking_service")

        if date and service_name:
            state.pop("awaiting_booking_date", None)
            return _present_slots(service_name, date, _extract_time_of_day(msg), session_id)

        return _response(
            "I didn't catch a date. Could you say something like 'tomorrow' or 'March 28th'?",
            session_id,
        )

    # 6. LLM intent detection
    intent = detect_intent(message)
    detected = intent.get("intent", "unknown")
    log.debug("detect_intent(%r) → %s | service=%s date=%s",
              message, detected, intent.get("service_name"), intent.get("date"))

    if detected == "service_question":
        return _handle_service_question(msg, session_id)

    if detected == "availability_check":
        return _handle_availability(intent, msg, session_id)

    if detected == "booking_request":
        return _handle_booking(intent, msg, session_id)

    if detected == "cancel_request":
        return _begin_cancel(session_id, message)

    if detected == "reschedule_request":
        return _begin_reschedule(session_id, message)

    return _response(
        "I can help you explore services, check availability, book an appointment, "
        "reschedule, or cancel a booking.",
        session_id,
    )
