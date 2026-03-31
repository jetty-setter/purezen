from __future__ import annotations

import logging
import uuid
from collections import Counter, defaultdict
from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

import bcrypt
import boto3
from boto3.dynamodb.conditions import Attr
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import AWS_REGION
from app.dynamodb_client import get_availability_table
from app.bookings import cancel_booking, reschedule_booking
from app.llm import call_ollama

log = logging.getLogger(__name__)

ADMINS_TABLE = "purezen_admins"

router = APIRouter(prefix="/admin")

dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
admins_table = dynamodb.Table(ADMINS_TABLE)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class AdminLoginRequest(BaseModel):
    email: str
    password: str


class AdminCancelRequest(BaseModel):
    booking_id: str
    reason: Optional[str] = None


class AdminRescheduleRequest(BaseModel):
    booking_id: str
    new_slot_id: str


class AdminQueryRequest(BaseModel):
    query: str
    token: str


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


def _scan_all(table, filter_expression=None) -> List[Dict[str, Any]]:
    kwargs = {}
    if filter_expression is not None:
        kwargs["FilterExpression"] = filter_expression
    items: List[Dict[str, Any]] = []
    response = table.scan(**kwargs)
    items.extend(response.get("Items", []))
    while "LastEvaluatedKey" in response:
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
        response = table.scan(**kwargs)
        items.extend(response.get("Items", []))
    return [_convert_decimal(item) for item in items]


def _format_display_date(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return "Unknown"
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%d")
        try:
            return parsed.strftime("%B %-d, %Y")
        except Exception:
            return parsed.strftime("%B %d, %Y").replace(" 0", " ")
    except Exception:
        return raw


def _booking_status_label(slot: Dict[str, Any]) -> str:
    status = str(slot.get("status", "")).upper()
    if status == "BOOKED":
        try:
            slot_date = datetime.strptime(slot.get("date", ""), "%Y-%m-%d").date()
            return "Upcoming" if slot_date >= datetime.utcnow().date() else "Completed"
        except Exception:
            return "Upcoming"
    if status == "CANCELLED":
        return "Cancelled"
    return status.title()


def _format_booking(slot: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "booking_id":    slot.get("booking_id"),
        "slot_id":       slot.get("slot_id"),
        "service_name":  slot.get("service_name", "Unknown"),
        "date":          slot.get("date"),
        "date_display":  _format_display_date(str(slot.get("date", ""))),
        "start_time":    slot.get("start_time"),
        "end_time":      slot.get("end_time"),
        "staff_name":    slot.get("staff_name"),
        "staff_id":      slot.get("staff_id"),
        "customer_name": slot.get("customer_name"),
        "customer_email":slot.get("customer_email"),
        "customer_phone":slot.get("customer_phone"),
        "special_requests": slot.get("special_requests"),
        "status":        _booking_status_label(slot),
        "booked_at":     slot.get("booked_at"),
    }


def _get_all_bookings() -> List[Dict[str, Any]]:
    table = get_availability_table()
    all_slots = _scan_all(table)
    booked = [
        s for s in all_slots
        if str(s.get("status", "")).upper() in ("BOOKED", "CANCELLED")
        and s.get("booking_id")
    ]
    formatted = [_format_booking(s) for s in booked]
    formatted.sort(key=lambda b: (b.get("date") or "", b.get("start_time") or ""))
    return formatted


def _verify_admin_token(token: str) -> bool:
    try:
        response = admins_table.scan(FilterExpression=Attr("token").eq(token))
        return len(response.get("Items", [])) > 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

def _llm_summarize(prompt: str) -> str:
    try:
        return call_ollama(prompt)
    except Exception as exc:
        log.warning("LLM call failed: %s", exc)
        return "Unable to generate AI summary at this time."


def _build_schedule_text(bookings: List[Dict[str, Any]]) -> str:
    if not bookings:
        return "No bookings."
    lines = []
    for b in bookings:
        staff = b.get("staff_name") or "Unassigned"
        customer = b.get("customer_name") or "Guest"
        requests = b.get("special_requests") or "None"
        lines.append(
            f"- {b.get('start_time', '?')} | {b.get('service_name')} | "
            f"Staff: {staff} | Customer: {customer} | Requests: {requests} | Status: {b.get('status')}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@router.post("/login")
def admin_login(request: AdminLoginRequest) -> Dict[str, Any]:
    email = request.email.lower().strip()

    response = admins_table.scan(FilterExpression=Attr("email").eq(email))
    items = response.get("Items", [])
    if not items:
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    admin = items[0]
    match = bcrypt.checkpw(
        request.password.encode("utf-8"),
        admin["password_hash"].encode("utf-8"),
    )
    if not match:
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    token = uuid.uuid4().hex
    admins_table.update_item(
        Key={"admin_id": admin["admin_id"]},
        UpdateExpression="SET #t = :t",
        ExpressionAttributeNames={"#t": "token"},
        ExpressionAttributeValues={":t": token},
    )

    return {
        "success": True,
        "token": token,
        "name": admin.get("name"),
        "email": email,
    }


# ---------------------------------------------------------------------------
# Booking data routes
# ---------------------------------------------------------------------------

@router.get("/bookings")
def get_all_bookings(token: str) -> List[Dict[str, Any]]:
    if not _verify_admin_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized.")
    return _get_all_bookings()


@router.get("/bookings/by-date")
def get_bookings_by_date(token: str, date: str) -> List[Dict[str, Any]]:
    if not _verify_admin_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized.")
    bookings = _get_all_bookings()
    return [b for b in bookings if b.get("date") == date]


@router.get("/bookings/by-service")
def get_bookings_by_service(token: str, service: str) -> List[Dict[str, Any]]:
    if not _verify_admin_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized.")
    bookings = _get_all_bookings()
    return [
        b for b in bookings
        if service.lower() in (b.get("service_name") or "").lower()
    ]


@router.get("/trends")
def get_trends(token: str) -> Dict[str, Any]:
    if not _verify_admin_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized.")

    bookings = _get_all_bookings()
    booked   = [b for b in bookings if b.get("status") in ("Upcoming", "Completed")]
    cancelled = [b for b in bookings if b.get("status") == "Cancelled"]

    service_counts = Counter(b.get("service_name", "Unknown") for b in booked)
    staff_counts   = Counter(b.get("staff_name", "Unassigned") for b in booked)

    # Bookings per day for the last 14 days
    today = datetime.utcnow().date()
    daily: Dict[str, int] = {}
    for i in range(13, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        daily[d] = 0
    for b in booked:
        d = b.get("date", "")
        if d in daily:
            daily[d] += 1

    # Peak hour
    hour_counts: Counter = Counter()
    for b in booked:
        t = b.get("start_time", "")
        if t:
            try:
                parsed = datetime.strptime(t.strip().upper(), "%I:%M %p")
                hour_counts[parsed.hour] += 1
            except Exception:
                pass

    peak_hour = None
    if hour_counts:
        h = hour_counts.most_common(1)[0][0]
        peak_hour = datetime.strptime(str(h), "%H").strftime("%-I %p")

    return {
        "total_bookings":   len(booked),
        "total_cancelled":  len(cancelled),
        "cancellation_rate": round(len(cancelled) / max(len(bookings), 1) * 100, 1),
        "by_service":       dict(service_counts.most_common()),
        "by_staff":         dict(staff_counts.most_common()),
        "daily_bookings":   daily,
        "peak_hour":        peak_hour,
    }


# ---------------------------------------------------------------------------
# Admin actions
# ---------------------------------------------------------------------------

@router.post("/bookings/cancel")
def admin_cancel_booking(request: AdminCancelRequest, token: str) -> Dict[str, Any]:
    if not _verify_admin_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized.")
    result = cancel_booking(request.booking_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message"))
    return result


@router.post("/bookings/reschedule")
def admin_reschedule_booking(request: AdminRescheduleRequest, token: str) -> Dict[str, Any]:
    if not _verify_admin_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized.")
    result = reschedule_booking(
        booking_id=request.booking_id,
        new_slot_id=request.new_slot_id,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message"))
    return result


# ---------------------------------------------------------------------------
# LLM routes
# ---------------------------------------------------------------------------

@router.get("/ai/schedule-summary")
def ai_schedule_summary(token: str, date: str) -> Dict[str, Any]:
    if not _verify_admin_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized.")

    bookings = [b for b in _get_all_bookings() if b.get("date") == date]
    schedule_text = _build_schedule_text(bookings)
    date_display = _format_display_date(date)

    prompt = f"""You are an assistant for PureZen Spa. Summarize the following schedule for {date_display} in 3-5 plain English sentences. Mention staff workloads, service mix, and any notable patterns. Be concise and professional.

Schedule:
{schedule_text}

Summary:"""

    return {"date": date, "summary": _llm_summarize(prompt)}


@router.get("/ai/conflicts")
def ai_conflict_check(token: str, date: str) -> Dict[str, Any]:
    if not _verify_admin_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized.")

    bookings = [b for b in _get_all_bookings() if b.get("date") == date]
    schedule_text = _build_schedule_text(bookings)
    date_display = _format_display_date(date)

    prompt = f"""You are a scheduling assistant for PureZen Spa. Review the following schedule for {date_display} and identify any conflicts, anomalies, or concerns. Look for: staff double-bookings, back-to-back appointments with no buffer, unusual cancellation patterns, or special requests that need attention. If no issues are found, say so clearly.

Schedule:
{schedule_text}

Conflicts and flags:"""

    return {"date": date, "conflicts": _llm_summarize(prompt)}


@router.get("/ai/trends-narrative")
def ai_trends_narrative(token: str) -> Dict[str, Any]:
    if not _verify_admin_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized.")

    trends = get_trends(token)

    prompt = f"""You are a business analyst for PureZen Spa. Based on the following booking data, write a 3-5 sentence narrative summary of trends and insights. Highlight top services, busiest staff, peak times, and cancellation rate. Suggest one actionable recommendation.

Data:
- Total bookings: {trends['total_bookings']}
- Total cancellations: {trends['total_cancelled']}
- Cancellation rate: {trends['cancellation_rate']}%
- Bookings by service: {trends['by_service']}
- Bookings by staff: {trends['by_staff']}
- Peak hour: {trends['peak_hour']}
- Daily bookings (last 14 days): {trends['daily_bookings']}

Narrative:"""

    return {"narrative": _llm_summarize(prompt)}


@router.get("/ai/customer-notes")
def ai_customer_notes(token: str, email: str) -> Dict[str, Any]:
    if not _verify_admin_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized.")

    bookings = _get_all_bookings()
    customer_bookings = [
        b for b in bookings
        if (b.get("customer_email") or "").lower() == email.lower().strip()
    ]

    if not customer_bookings:
        return {"email": email, "summary": "No booking history found for this guest."}

    lines = []
    for b in customer_bookings:
        requests = b.get("special_requests") or "None"
        lines.append(
            f"- {b.get('date_display')} | {b.get('service_name')} | "
            f"Status: {b.get('status')} | Special requests: {requests}"
        )
    history_text = "\n".join(lines)
    name = customer_bookings[0].get("customer_name", "This guest")

    prompt = f"""You are a spa concierge assistant. Based on the following booking history for {name}, write a 2-4 sentence summary of their preferences and patterns. Note any recurring services, special requests, or preferences worth flagging for staff.

History:
{history_text}

Guest summary:"""

    return {"email": email, "name": name, "summary": _llm_summarize(prompt)}


@router.post("/ai/query")
def ai_natural_language_query(request: AdminQueryRequest) -> Dict[str, Any]:
    if not _verify_admin_token(request.token):
        raise HTTPException(status_code=401, detail="Unauthorized.")

    bookings = _get_all_bookings()
    schedule_text = _build_schedule_text(bookings[:100])  # cap to avoid token overflow

    prompt = f"""You are an assistant for PureZen Spa with access to the current booking data. Answer the following question from a spa admin accurately and concisely based on the data provided.

Question: {request.query}

Current booking data (most recent 100 records):
{schedule_text}

Answer:"""

    return {"query": request.query, "answer": _llm_summarize(prompt)}
