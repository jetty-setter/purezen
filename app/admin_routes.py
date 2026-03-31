from __future__ import annotations

import csv
import io
import logging
import uuid
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

import bcrypt
import boto3
import requests as http_requests
from boto3.dynamodb.conditions import Attr
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.config import AWS_REGION, OLLAMA_URL
from app.dynamodb_client import get_availability_table
from app.bookings import cancel_booking, reschedule_booking

log = logging.getLogger(__name__)

ADMINS_TABLE      = "purezen_admins"
ADMIN_LLM_MODEL   = "qwen2.5:3b"
ADMIN_LLM_TIMEOUT = 60

router = APIRouter(prefix="/admin")

dynamodb     = boto3.resource("dynamodb", region_name=AWS_REGION)
admins_table = dynamodb.Table(ADMINS_TABLE)


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
        "booking_id":       slot.get("booking_id"),
        "slot_id":          slot.get("slot_id"),
        "service_name":     slot.get("service_name", "Unknown"),
        "date":             slot.get("date"),
        "date_display":     _format_display_date(str(slot.get("date", ""))),
        "start_time":       slot.get("start_time"),
        "end_time":         slot.get("end_time"),
        "staff_name":       slot.get("staff_name"),
        "staff_id":         slot.get("staff_id"),
        "customer_name":    slot.get("customer_name"),
        "customer_email":   slot.get("customer_email"),
        "customer_phone":   slot.get("customer_phone"),
        "special_requests": slot.get("special_requests"),
        "status":           _booking_status_label(slot),
        "booked_at":        slot.get("booked_at"),
    }


def _get_all_bookings() -> List[Dict[str, Any]]:
    table     = get_availability_table()
    all_slots = _scan_all(table)
    booked    = [
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


def _build_schedule_text(bookings: List[Dict[str, Any]]) -> str:
    if not bookings:
        return "No bookings."
    return "\n".join(
        f"- {b.get('start_time','?')} | {b.get('service_name')} | "
        f"Staff: {b.get('staff_name') or 'Unassigned'} | "
        f"Customer: {b.get('customer_name') or 'Guest'} | "
        f"Requests: {b.get('special_requests') or 'None'} | "
        f"Status: {b.get('status')}"
        for b in bookings
    )


def _llm(prompt: str) -> str:
    """
    Calls qwen2.5:3b. Uses a strict system instruction to keep responses
    professional, brief, and statement-only (no questions, no sign-offs).
    """
    system = (
        "You are a concise administrative assistant for PureZen Spa. "
        "Respond only with factual, professional observations. "
        "Use 1-3 short sentences maximum. "
        "Never ask a question. Never sign off. Never say 'warm regards' or similar. "
        "Never mention yourself. Just state the facts clearly."
    )
    try:
        payload = {
            "model":   ADMIN_LLM_MODEL,
            "prompt":  f"{system}\n\n{prompt}",
            "stream":  False,
            "options": {"temperature": 0.1},
        }
        response = http_requests.post(
            f"{OLLAMA_URL}/api/generate",
            json=payload,
            timeout=ADMIN_LLM_TIMEOUT,
        )
        response.raise_for_status()
        raw = (response.json().get("response") or "").strip()

        # Strip any trailing questions or sign-offs the model adds anyway
        lines = raw.split("\n")
        cleaned = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # Drop lines that are questions or common sign-offs
            if line.endswith("?"):
                continue
            lower = line.lower()
            if any(lower.startswith(p) for p in (
                "warm regards", "regards", "sincerely", "best regards",
                "owen", "qwen", "how can i", "let me know", "feel free",
                "if you", "please let", "i hope",
            )):
                continue
            cleaned.append(line)

        return " ".join(cleaned).strip() or "No summary available."
    except Exception as exc:
        log.warning("Admin LLM call failed: %s", exc)
        return "Summary unavailable — please try again."


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@router.post("/login")
def admin_login(request: AdminLoginRequest) -> Dict[str, Any]:
    email    = request.email.lower().strip()
    response = admins_table.scan(FilterExpression=Attr("email").eq(email))
    items    = response.get("Items", [])
    if not items:
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    admin = items[0]
    if not bcrypt.checkpw(request.password.encode(), admin["password_hash"].encode()):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    token = uuid.uuid4().hex
    admins_table.update_item(
        Key={"admin_id": admin["admin_id"]},
        UpdateExpression="SET #t = :t",
        ExpressionAttributeNames={"#t": "token"},
        ExpressionAttributeValues={":t": token},
    )
    return {"success": True, "token": token, "name": admin.get("name"), "email": email}


# ---------------------------------------------------------------------------
# Booking data
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
    return [b for b in _get_all_bookings() if b.get("date") == date]


@router.get("/bookings/upcoming")
def get_upcoming_bookings(token: str, limit: int = 5) -> List[Dict[str, Any]]:
    if not _verify_admin_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized.")
    today = datetime.utcnow().date().isoformat()
    upcoming = [
        b for b in _get_all_bookings()
        if b.get("status") == "Upcoming" and (b.get("date") or "") >= today
    ]
    return upcoming[:limit]


@router.get("/bookings/export")
def export_bookings_csv(token: str) -> StreamingResponse:
    if not _verify_admin_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized.")
    bookings = _get_all_bookings()
    output   = io.StringIO()
    writer   = csv.DictWriter(output, fieldnames=[
        "booking_id", "customer_name", "customer_email", "customer_phone",
        "service_name", "date", "start_time", "staff_name",
        "special_requests", "status", "booked_at",
    ])
    writer.writeheader()
    for b in bookings:
        writer.writerow({k: b.get(k, "") for k in writer.fieldnames})
    output.seek(0)
    filename = f"purezen_bookings_{datetime.utcnow().strftime('%Y%m%d')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/trends")
def get_trends(token: str, date_from: Optional[str] = None, date_to: Optional[str] = None) -> Dict[str, Any]:
    if not _verify_admin_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized.")
    bookings = _get_all_bookings()
    if date_from:
        bookings = [b for b in bookings if (b.get("date") or "") >= date_from]
    if date_to:
        bookings = [b for b in bookings if (b.get("date") or "") <= date_to]
    booked    = [b for b in bookings if b.get("status") in ("Upcoming", "Completed")]
    cancelled = [b for b in bookings if b.get("status") == "Cancelled"]
    service_counts = Counter(b.get("service_name", "Unknown") for b in booked)
    staff_counts   = Counter(b.get("staff_name", "Unassigned") for b in booked)
    today = datetime.utcnow().date()
    daily: Dict[str, int] = {}
    for i in range(13, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        daily[d] = 0
    for b in booked:
        d = b.get("date", "")
        if d in daily:
            daily[d] += 1
    weekly: Dict[str, int] = {}
    for i in range(7):
        d = (today + timedelta(days=i)).isoformat()
        weekly[d] = 0
    for b in booked:
        d = b.get("date", "")
        if d in weekly:
            weekly[d] += 1
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
        "total_bookings":    len(booked),
        "total_cancelled":   len(cancelled),
        "cancellation_rate": round(len(cancelled) / max(len(bookings), 1) * 100, 1),
        "by_service":        dict(service_counts.most_common()),
        "by_staff":          dict(staff_counts.most_common()),
        "daily_bookings":    daily,
        "weekly_forward":    weekly,
        "peak_hour":         peak_hour,
    }


@router.get("/staff/roster")
def get_staff_roster(token: str) -> List[Dict[str, Any]]:
    """Staff roster with booking counts for current week."""
    if not _verify_admin_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized.")
    bookings = _get_all_bookings()
    today    = datetime.utcnow().date()
    week_start = today - timedelta(days=today.weekday())
    week_end   = week_start + timedelta(days=6)

    staff_map: Dict[str, Dict[str, Any]] = {}
    for b in bookings:
        name = b.get("staff_name") or "Unassigned"
        if name not in staff_map:
            staff_map[name] = {
                "staff_name":     name,
                "total_bookings": 0,
                "this_week":      0,
                "upcoming":       0,
                "services":       Counter(),
            }
        staff_map[name]["total_bookings"] += 1
        bdate = b.get("date", "")
        if bdate:
            try:
                bd = datetime.strptime(bdate, "%Y-%m-%d").date()
                if week_start <= bd <= week_end:
                    staff_map[name]["this_week"] += 1
                if b.get("status") == "Upcoming":
                    staff_map[name]["upcoming"] += 1
            except Exception:
                pass
        if b.get("service_name"):
            staff_map[name]["services"][b["service_name"]] += 1

    roster = []
    for s in staff_map.values():
        top_service = s["services"].most_common(1)[0][0] if s["services"] else "—"
        roster.append({
            "staff_name":     s["staff_name"],
            "total_bookings": s["total_bookings"],
            "this_week":      s["this_week"],
            "upcoming":       s["upcoming"],
            "top_service":    top_service,
        })

    roster.sort(key=lambda x: x["total_bookings"], reverse=True)
    return roster


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
    result = reschedule_booking(booking_id=request.booking_id, new_slot_id=request.new_slot_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message"))
    return result


# ---------------------------------------------------------------------------
# AI routes — strict, concise prompts
# ---------------------------------------------------------------------------

@router.get("/ai/schedule-summary")
def ai_schedule_summary(token: str, date: str) -> Dict[str, Any]:
    if not _verify_admin_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized.")
    all_day    = [b for b in _get_all_bookings() if b.get("date") == date]
    upcoming   = [b for b in all_day if b.get("status") == "Upcoming"]
    is_today   = date == datetime.utcnow().date().isoformat()

    # For today's overview, only summarise upcoming slots so tense is present/future
    focus = upcoming if is_today and upcoming else all_day

    if not focus:
        return {"date": date, "summary": f"No upcoming appointments for {_format_display_date(date)}."}

    count    = len(focus)
    staff    = list({b.get("staff_name") for b in focus if b.get("staff_name")})
    services = list({b.get("service_name") for b in focus if b.get("service_name")})

    if is_today:
        prompt = (
            f"Right now at PureZen, there are {count} appointments still to come today. "
            f"Staff currently on duty: {', '.join(staff) if staff else 'None'}. "
            f"Services being offered: {', '.join(services) if services else 'None'}.\n\n"
            "Write exactly 2 sentences describing what is happening at the spa right now. "
            "Use ONLY present tense: 'has', 'is', 'are', 'will be'. "
            "NEVER use 'was', 'had', 'were', 'scheduled' in past tense, or any past tense verb. "
            "Professional tone. No questions. No sign-off."
        )
    else:
        prompt = (
            f"Date: {_format_display_date(date)}. "
            f"Appointments: {count}. "
            f"Staff: {', '.join(staff) if staff else 'None'}. "
            f"Services: {', '.join(services) if services else 'None'}.\n\n"
            "Write exactly 2 sentences summarizing this day. "
            "Professional tone. No questions. No sign-off."
        )
    return {"date": date, "summary": _llm(prompt)}


@router.get("/ai/conflicts")
def ai_conflict_check(token: str, date: str) -> Dict[str, Any]:
    if not _verify_admin_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized.")
    bookings = [b for b in _get_all_bookings() if b.get("date") == date]
    if not bookings:
        return {"date": date, "conflicts": f"No appointments on {_format_display_date(date)}."}
    prompt = (
        f"Schedule for {_format_display_date(date)}:\n{_build_schedule_text(bookings)}\n\n"
        "Identify conflicts, double-bookings, or special requests requiring attention. "
        "If none, state: 'No conflicts or special requests identified.' "
        "Maximum 3 bullet points. No questions. No sign-off."
    )
    return {"date": date, "conflicts": _llm(prompt)}


@router.get("/ai/trends-narrative")
def ai_trends_narrative(token: str, date_from: Optional[str] = None, date_to: Optional[str] = None) -> Dict[str, Any]:
    if not _verify_admin_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized.")
    trends = get_trends(token, date_from=date_from, date_to=date_to)
    top_service = next(iter(trends["by_service"]), "N/A")
    top_staff   = next(iter(trends["by_staff"]),   "N/A")
    date_range  = ""
    if date_from and date_to:
        date_range = f" ({_format_display_date(date_from)} – {_format_display_date(date_to)})"
    elif date_from:
        date_range = f" (from {_format_display_date(date_from)})"
    elif date_to:
        date_range = f" (up to {_format_display_date(date_to)})"
    prompt = (
        f"PureZen Spa data{date_range}: "
        f"{trends['total_bookings']} bookings, "
        f"{trends['cancellation_rate']}% cancellation rate, "
        f"top service: {top_service}, top staff: {top_staff}, peak hour: {trends['peak_hour']}.\n\n"
        "Respond in EXACTLY this format, nothing else:\n"
        "Observation 1: [one factual sentence about booking volume or trends]\n"
        "Observation 2: [one factual sentence about service or staff performance]\n"
        "Actionable Recommendation: [one specific, practical suggestion]\n\n"
        "No intro. No sign-off. No extra sentences."
    )
    return {"narrative": _llm(prompt)}


@router.get("/guest/lookup")
def guest_lookup(token: str, query: str) -> Dict[str, Any]:
    """Look up a guest by email or phone number. Returns bookings + AI profile."""
    if not _verify_admin_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized.")

    q = query.strip().lower()
    # Normalise phone — strip everything except digits for comparison
    q_digits = "".join(c for c in q if c.isdigit())

    all_bks = _get_all_bookings()

    matches = []
    for b in all_bks:
        email_match = q and (b.get("customer_email") or "").lower() == q
        phone_raw   = "".join(c for c in (b.get("customer_phone") or "") if c.isdigit())
        phone_match = q_digits and len(q_digits) >= 7 and q_digits in phone_raw
        if email_match or phone_match:
            matches.append(b)

    if not matches:
        return {"found": False, "bookings": [], "name": None, "summary": "No booking history found for this guest."}

    name  = matches[0].get("customer_name", "Guest")
    email = matches[0].get("customer_email", "")
    count = len(matches)
    services      = [b.get("service_name") for b in matches if b.get("service_name")]
    service_counts = Counter(services)
    top  = service_counts.most_common(1)[0][0] if service_counts else "N/A"
    reqs = [b.get("special_requests") for b in matches if b.get("special_requests")]

    prompt = (
        f"Guest: {name}. Total bookings: {count}. "
        f"Most booked service: {top}. "
        f"Special requests on file: {'; '.join(reqs) if reqs else 'None'}.\n\n"
        "Write 2 sentences summarizing this guest's profile for staff reference. "
        "Include any special requests or notes. Professional tone. No questions. No sign-off."
    )
    summary = _llm(prompt)

    return {
        "found":    True,
        "name":     name,
        "email":    email,
        "summary":  summary,
        "bookings": matches,
    }


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
        return {"email": email, "summary": "No booking history on file for this guest."}
    name  = customer_bookings[0].get("customer_name", "Guest")
    count = len(customer_bookings)
    services = [b.get("service_name") for b in customer_bookings if b.get("service_name")]
    service_counts = Counter(services)
    top = service_counts.most_common(1)[0][0] if service_counts else "N/A"
    requests = [b.get("special_requests") for b in customer_bookings if b.get("special_requests")]
    prompt = (
        f"Guest: {name}. Total bookings: {count}. "
        f"Most booked service: {top}. "
        f"Special requests on file: {'; '.join(requests) if requests else 'None'}.\n\n"
        "Write 2 sentences summarizing this guest's profile for staff reference. "
        "Include any special requests or notes. Professional tone. No questions. No sign-off."
    )
    return {"email": email, "name": name, "summary": _llm(prompt)}


@router.post("/ai/query")
def ai_natural_language_query(request: AdminQueryRequest) -> Dict[str, Any]:
    if not _verify_admin_token(request.token):
        raise HTTPException(status_code=401, detail="Unauthorized.")
    bookings      = _get_all_bookings()
    schedule_text = _build_schedule_text(bookings[:50])
    prompt = (
        f"Booking data:\n{schedule_text}\n\n"
        f"Question: {request.query}\n\n"
        "Answer in 1-2 sentences. Facts only. No questions. No sign-off."
    )
    return {"query": request.query, "answer": _llm(prompt)}


# ---------------------------------------------------------------------------
# Walk-in booking
# ---------------------------------------------------------------------------

class WalkInRequest(BaseModel):
    token:            str
    slot_id:          str
    service_name:     str
    customer_name:    str
    customer_phone:   str
    customer_email:   str
    special_requests: Optional[str] = None


@router.get("/walkin/slots")
def get_walkin_slots(token: str, date: str) -> List[Dict[str, Any]]:
    """Return available slots for a date, grouped by time with available staff."""
    if not _verify_admin_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized.")

    table    = get_availability_table()
    response = table.scan(
        FilterExpression=Attr("date").eq(date) & Attr("status").eq("AVAILABLE")
    )
    items = [_convert_decimal(i) for i in response.get("Items", [])]
    while "LastEvaluatedKey" in response:
        response = table.scan(
            FilterExpression=Attr("date").eq(date) & Attr("status").eq("AVAILABLE"),
            ExclusiveStartKey=response["LastEvaluatedKey"],
        )
        items += [_convert_decimal(i) for i in response.get("Items", [])]

    # Group by start_time so frontend can show time → available staff
    from collections import defaultdict
    grouped: Dict[str, List[Dict]] = defaultdict(list)
    for item in items:
        t = item.get("start_time") or ""
        if t:
            grouped[t].append({
                "slot_id":    item.get("slot_id"),
                "start_time": t,
                "end_time":   item.get("end_time"),
                "staff_name": item.get("staff_name"),
                "staff_id":   item.get("staff_id"),
            })

    result = []
    for time_val in sorted(grouped.keys()):
        result.append({
            "start_time":      time_val,
            "available_staff": grouped[time_val],
        })
    return result


@router.post("/walkin/book")
def walkin_book(request: WalkInRequest) -> Dict[str, Any]:
    """Book a walk-in appointment from the admin schedule page."""
    if not _verify_admin_token(request.token):
        raise HTTPException(status_code=401, detail="Unauthorized.")

    table    = get_availability_table()
    response = table.get_item(Key={"slot_id": request.slot_id})
    slot     = response.get("Item")

    if not slot:
        raise HTTPException(status_code=404, detail="Slot not found.")
    if str(slot.get("status", "")).upper() != "AVAILABLE":
        raise HTTPException(status_code=409, detail="That slot is no longer available.")

    booking_id  = f"bk_{uuid.uuid4().hex[:12]}"
    update_expr = (
        "SET #status = :booked, booking_id = :bid, booked_at = :bat, "
        "service_name = :svc, customer_name = :cn, customer_phone = :cp, customer_email = :ce"
    )
    expr_values = {
        ":booked":    "BOOKED",
        ":available": "AVAILABLE",
        ":bid":       booking_id,
        ":bat":       datetime.utcnow().isoformat(),
        ":svc":       request.service_name,
        ":cn":        request.customer_name,
        ":cp":        request.customer_phone,
        ":ce":        request.customer_email,
    }

    if request.special_requests:
        update_expr += ", special_requests = :req"
        expr_values[":req"] = request.special_requests

    try:
        table.update_item(
            Key={"slot_id": request.slot_id},
            UpdateExpression=update_expr,
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues=expr_values,
            ConditionExpression="#status = :available",
        )
    except Exception:
        raise HTTPException(status_code=409, detail="That slot was just taken. Please choose another.")

    return {
        "success":    True,
        "booking_id": booking_id,
        "message":    f"Appointment booked for {request.customer_name}. Confirmation: {booking_id}",
    }
