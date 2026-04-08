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
from boto3.dynamodb.conditions import Attr
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.config import AWS_REGION, OLLAMA_URL
from app.dynamodb_client import get_availability_table
from app.bookings import cancel_booking, reschedule_booking
from app.admin_orchestrator import llm, orchestrate, configure as configure_llm

log = logging.getLogger(__name__)

ADMINS_TABLE = "purezen_admins"
USERS_TABLE  = "purezen_users"
STAFF_TABLE  = "purezen_staff"

router = APIRouter(prefix="/admin")

dynamodb     = boto3.resource("dynamodb", region_name=AWS_REGION)
admins_table = dynamodb.Table(ADMINS_TABLE)
users_table  = dynamodb.Table(USERS_TABLE)
staff_table  = dynamodb.Table(STAFF_TABLE)

configure_llm(model="llama3.2:3b", timeout=60, ollama_url=OLLAMA_URL)


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

class StaffLoginRequest(BaseModel):
    email: str
    password: str

class SetStaffPasswordRequest(BaseModel):
    token: str
    staff_id: str
    password: str

class CreateAdminRequest(BaseModel):
    token: str
    name: str
    email: str
    password: str

class AdminActionRequest(BaseModel):
    token: str
    admin_id: str

class ResetAdminPasswordRequest(BaseModel):
    token: str
    admin_id: str
    password: str

class UserActionRequest(BaseModel):
    token: str
    user_id: str

class CreateStaffRequest(BaseModel):
    token: str
    first_name: str
    last_name: str
    role: str
    email: str
    employment_type: str
    weekly_hours_limit: int
    skills: List[str]
    location_id: str = "omaha_main"

class StaffActionRequest(BaseModel):
    token: str
    staff_id: str
    is_active: bool

class WalkInRequest(BaseModel):
    token:            str
    slot_id:          str
    service_name:     str
    customer_name:    str
    customer_phone:   str
    customer_email:   str
    special_requests: Optional[str] = None


# ---------------------------------------------------------------------------
# Shared helpers
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
    booked    = [s for s in all_slots if str(s.get("status", "")).upper() in ("BOOKED", "CANCELLED") and s.get("booking_id")]
    formatted = [_format_booking(s) for s in booked]
    formatted.sort(key=lambda b: (b.get("date") or "", b.get("start_time") or ""))
    return formatted


def _get_data_fns() -> Dict[str, Any]:
    """Inject data access callables into the orchestrator."""
    return {
        "get_all_bookings": _get_all_bookings,
        "scan_staff":       lambda: _scan_all(staff_table),
    }


def _verify_admin_token(token: str) -> bool:
    try:
        return len(admins_table.scan(FilterExpression=Attr("token").eq(token)).get("Items", [])) > 0
    except Exception:
        return False


def _verify_staff_token(token: str) -> bool:
    try:
        if admins_table.scan(FilterExpression=Attr("token").eq(token)).get("Items"):
            return True
        items = staff_table.scan(FilterExpression=Attr("token").eq(token)).get("Items", [])
        return bool(items) and items[0].get("is_active", True)
    except Exception:
        return False


def _verify_any_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        items = admins_table.scan(FilterExpression=Attr("token").eq(token)).get("Items", [])
        if items and items[0].get("active", True):
            a = items[0]
            return {"role": "admin", "name": a.get("name", "Admin"), "id": a.get("admin_id")}
        items2 = staff_table.scan(FilterExpression=Attr("token").eq(token)).get("Items", [])
        if items2 and items2[0].get("is_active", True):
            s = items2[0]
            return {"role": "staff", "name": s.get("display_name") or f"{s.get('first_name','')} {s.get('last_name','')}".strip(), "id": s.get("staff_id")}
    except Exception:
        pass
    return None


def _build_schedule_text(bookings: List[Dict[str, Any]]) -> str:
    if not bookings:
        return "No bookings."
    return "\n".join(
        f"- {b.get('start_time','?')} | {b.get('service_name')} | Staff: {b.get('staff_name') or 'Unassigned'} | Customer: {b.get('customer_name') or 'Guest'} | Status: {b.get('status')}"
        for b in bookings
    )


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@router.post("/login")
def admin_login(request: AdminLoginRequest) -> Dict[str, Any]:
    email    = request.email.lower().strip()
    items    = admins_table.scan(FilterExpression=Attr("email").eq(email)).get("Items", [])
    if not items:
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    admin = items[0]
    if not bcrypt.checkpw(request.password.encode(), admin["password_hash"].encode()):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    token = uuid.uuid4().hex
    admins_table.update_item(Key={"admin_id": admin["admin_id"]}, UpdateExpression="SET #t = :t", ExpressionAttributeNames={"#t": "token"}, ExpressionAttributeValues={":t": token})
    return {"success": True, "token": token, "name": admin.get("name"), "email": email}


@router.post("/staff/login")
def staff_login(request: StaffLoginRequest) -> Dict[str, Any]:
    email = request.email.lower().strip()
    items = staff_table.scan(FilterExpression=Attr("email").eq(email)).get("Items", [])
    if not items:
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    staff = items[0]
    if not staff.get("is_active", True):
        raise HTTPException(status_code=403, detail="This account has been deactivated.")
    pw_hash = staff.get("password_hash", "")
    if not pw_hash:
        raise HTTPException(status_code=401, detail="No password set. Contact an administrator.")
    if not bcrypt.checkpw(request.password.encode(), pw_hash.encode()):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    token = uuid.uuid4().hex
    staff_table.update_item(Key={"staff_id": staff["staff_id"]}, UpdateExpression="SET #t = :t", ExpressionAttributeNames={"#t": "token"}, ExpressionAttributeValues={":t": token})
    display = staff.get("display_name") or f"{staff.get('first_name','')} {staff.get('last_name','')}".strip()
    return {"success": True, "token": token, "name": display, "role": "staff"}


@router.post("/logout")
def admin_logout(token: str) -> Dict[str, Any]:
    """Invalidate an admin or staff session token in DynamoDB."""
    try:
        # Check admins table
        items = admins_table.scan(FilterExpression=Attr("token").eq(token)).get("Items", [])
        if items:
            admins_table.update_item(
                Key={"admin_id": items[0]["admin_id"]},
                UpdateExpression="SET #t = :t",
                ExpressionAttributeNames={"#t": "token"},
                ExpressionAttributeValues={":t": ""},
            )
            return {"success": True, "message": "Logged out."}

        # Check staff table
        items2 = staff_table.scan(FilterExpression=Attr("token").eq(token)).get("Items", [])
        if items2:
            staff_table.update_item(
                Key={"staff_id": items2[0]["staff_id"]},
                UpdateExpression="SET #t = :t",
                ExpressionAttributeNames={"#t": "token"},
                ExpressionAttributeValues={":t": ""},
            )
            return {"success": True, "message": "Logged out."}

        return {"success": True, "message": "Token not found, already logged out."}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))



@router.get("/me")
def get_me(token: str) -> Dict[str, Any]:
    session = _verify_any_token(token)
    if not session:
        raise HTTPException(status_code=401, detail="Invalid or expired session.")
    return session


# ---------------------------------------------------------------------------
# Booking data
# ---------------------------------------------------------------------------

@router.get("/bookings")
def get_all_bookings(token: str) -> List[Dict[str, Any]]:
    if not _verify_staff_token(token): raise HTTPException(status_code=401, detail="Unauthorized.")
    return _get_all_bookings()


@router.get("/bookings/by-date")
def get_bookings_by_date(token: str, date: str) -> List[Dict[str, Any]]:
    if not _verify_staff_token(token): raise HTTPException(status_code=401, detail="Unauthorized.")
    return [b for b in _get_all_bookings() if b.get("date") == date]


@router.get("/bookings/upcoming")
def get_upcoming_bookings(token: str, limit: int = 5) -> List[Dict[str, Any]]:
    if not _verify_staff_token(token): raise HTTPException(status_code=401, detail="Unauthorized.")
    today = datetime.utcnow().date().isoformat()
    return [b for b in _get_all_bookings() if b.get("status") == "Upcoming" and (b.get("date") or "") >= today][:limit]


@router.get("/bookings/export")
def export_bookings_csv(token: str) -> StreamingResponse:
    if not _verify_staff_token(token): raise HTTPException(status_code=401, detail="Unauthorized.")
    bookings = _get_all_bookings()
    output   = io.StringIO()
    fields   = ["booking_id","customer_name","customer_email","customer_phone","service_name","date","start_time","staff_name","special_requests","status","booked_at"]
    writer   = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for b in bookings:
        writer.writerow({k: b.get(k, "") for k in fields})
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename=purezen_bookings_{datetime.utcnow().strftime('%Y%m%d')}.csv"})


@router.get("/trends")
def get_trends(token: str, date_from: Optional[str] = None, date_to: Optional[str] = None) -> Dict[str, Any]:
    if not _verify_staff_token(token): raise HTTPException(status_code=401, detail="Unauthorized.")
    bookings = _get_all_bookings()
    if date_from: bookings = [b for b in bookings if (b.get("date") or "") >= date_from]
    if date_to:   bookings = [b for b in bookings if (b.get("date") or "") <= date_to]
    booked    = [b for b in bookings if b.get("status") in ("Upcoming", "Completed")]
    cancelled = [b for b in bookings if b.get("status") == "Cancelled"]
    today = datetime.utcnow().date()
    daily: Dict[str, int] = {(today - timedelta(days=i)).isoformat(): 0 for i in range(13, -1, -1)}
    for b in booked:
        if b.get("date") in daily: daily[b["date"]] += 1
    weekly: Dict[str, int] = {(today + timedelta(days=i)).isoformat(): 0 for i in range(7)}
    for b in booked:
        if b.get("date") in weekly: weekly[b["date"]] += 1
    today_booked = [b for b in _get_all_bookings() if b.get("date") == today.isoformat() and b.get("status") in ("Upcoming", "Completed")]
    hour_counts: Counter = Counter()
    for b in today_booked:
        t = b.get("start_time", "")
        if t:
            try: hour_counts[datetime.strptime(t.strip().upper(), "%I:%M %p").hour] += 1
            except Exception: pass
    peak_hour = None
    if hour_counts:
        h = hour_counts.most_common(1)[0][0]
        peak_hour = datetime.strptime(str(h), "%H").strftime("%-I %p")
    return {
        "total_bookings": len(booked), "total_cancelled": len(cancelled),
        "cancellation_rate": round(len(cancelled) / max(len(bookings), 1) * 100, 1),
        "by_service": dict(Counter(b.get("service_name", "Unknown") for b in booked).most_common()),
        "by_staff":   dict(Counter(b.get("staff_name", "Unassigned") for b in booked).most_common()),
        "daily_bookings": daily, "weekly_forward": weekly, "peak_hour": peak_hour,
    }


@router.get("/staff/roster")
def get_staff_roster(token: str) -> List[Dict[str, Any]]:
    if not _verify_staff_token(token): raise HTTPException(status_code=401, detail="Unauthorized.")
    bookings   = _get_all_bookings()
    today      = datetime.utcnow().date()
    week_start = today - timedelta(days=today.weekday())
    week_end   = week_start + timedelta(days=6)
    staff_map: Dict[str, Dict[str, Any]] = {}
    for b in bookings:
        name = b.get("staff_name") or "Unassigned"
        if name not in staff_map:
            staff_map[name] = {"staff_name": name, "total_bookings": 0, "this_week": 0, "upcoming": 0, "services": Counter()}
        staff_map[name]["total_bookings"] += 1
        bdate = b.get("date", "")
        if bdate:
            try:
                bd = datetime.strptime(bdate, "%Y-%m-%d").date()
                if week_start <= bd <= week_end: staff_map[name]["this_week"] += 1
                if b.get("status") == "Upcoming": staff_map[name]["upcoming"] += 1
            except Exception: pass
        if b.get("service_name"): staff_map[name]["services"][b["service_name"]] += 1
    roster = [{"staff_name": s["staff_name"], "total_bookings": s["total_bookings"], "this_week": s["this_week"], "upcoming": s["upcoming"], "top_service": s["services"].most_common(1)[0][0] if s["services"] else "—"} for s in staff_map.values()]
    roster.sort(key=lambda x: x["total_bookings"], reverse=True)
    return roster


# ---------------------------------------------------------------------------
# Admin actions
# ---------------------------------------------------------------------------

@router.post("/bookings/cancel")
def admin_cancel_booking(request: AdminCancelRequest, token: str) -> Dict[str, Any]:
    if not _verify_staff_token(token): raise HTTPException(status_code=401, detail="Unauthorized.")
    table     = get_availability_table()
    all_slots = _scan_all(table)
    slot      = next((s for s in all_slots if s.get("booking_id") == request.booking_id), None)
    result    = cancel_booking(request.booking_id)
    if not result.get("success"): raise HTTPException(status_code=400, detail=result.get("message"))
    if slot and slot.get("customer_email"):
        try: _send_cancel_email(slot)
        except Exception as e: log.warning("Cancel email failed: %s", e)
    return {**result, "email_sent": bool(slot and slot.get("customer_email"))}


def _send_cancel_email(slot: Dict[str, Any]) -> None:
    ses        = boto3.client("ses", region_name=AWS_REGION)
    name       = slot.get("customer_name", "Valued Guest")
    email      = slot.get("customer_email")
    service    = slot.get("service_name", "your appointment")
    date_str   = _format_display_date(str(slot.get("date", "")))
    time_str   = slot.get("start_time", "")
    staff      = slot.get("staff_name", "our team")
    booking_id = slot.get("booking_id", "")
    subject    = "Your PureZen Appointment Has Been Cancelled"
    body_html  = f"""<html><body style="font-family:Georgia,serif;color:#2b2624;max-width:600px;margin:0 auto;padding:40px 24px;background:#fbf7f3;">
      <h1 style="font-size:1.5rem;">PureZen Spa &amp; Wellness</h1>
      <h2>Appointment Cancelled</h2>
      <p>Hi {name},</p><p>Your appointment has been cancelled:</p>
      <div style="background:#eae4de;border-radius:16px;padding:24px;margin:24px 0;">
        <table style="width:100%;border-collapse:collapse;">
          <tr><td style="color:#9e9189;font-size:0.85rem;width:140px;">Service</td><td style="font-weight:600;">{service}</td></tr>
          <tr><td style="color:#9e9189;font-size:0.85rem;">Date</td><td style="font-weight:600;">{date_str}</td></tr>
          <tr><td style="color:#9e9189;font-size:0.85rem;">Time</td><td style="font-weight:600;">{time_str}</td></tr>
          <tr><td style="color:#9e9189;font-size:0.85rem;">Therapist</td><td style="font-weight:600;">{staff}</td></tr>
          <tr><td style="color:#9e9189;font-size:0.85rem;">Booking ID</td><td style="color:#9e9189;font-size:0.85rem;">{booking_id}</td></tr>
        </table>
      </div>
      <p>With warmth,<br><strong>The PureZen Team</strong></p>
    </body></html>"""
    ses.send_email(Source="noreply@purezen.com", Destination={"ToAddresses": [email]}, Message={"Subject": {"Data": subject, "Charset": "UTF-8"}, "Body": {"Html": {"Data": body_html, "Charset": "UTF-8"}}})


@router.post("/bookings/reschedule")
def admin_reschedule_booking(request: AdminRescheduleRequest, token: str) -> Dict[str, Any]:
    if not _verify_staff_token(token): raise HTTPException(status_code=401, detail="Unauthorized.")
    result = reschedule_booking(booking_id=request.booking_id, new_slot_id=request.new_slot_id)
    if not result.get("success"): raise HTTPException(status_code=400, detail=result.get("message"))
    return result


# ---------------------------------------------------------------------------
# AI routes — delegate to ai_orchestrator.py
# ---------------------------------------------------------------------------

@router.get("/ai/schedule-summary")
def ai_schedule_summary(token: str, date: str) -> Dict[str, Any]:
    if not _verify_staff_token(token): raise HTTPException(status_code=401, detail="Unauthorized.")
    all_day  = [b for b in _get_all_bookings() if b.get("date") == date]
    upcoming = [b for b in all_day if b.get("status") == "Upcoming"]
    is_today = date == datetime.utcnow().date().isoformat()
    focus    = upcoming if is_today and upcoming else all_day
    if not focus:
        return {"date": date, "summary": f"No upcoming appointments for {_format_display_date(date)}."}
    count    = len(focus)
    staff    = list({b.get("staff_name") for b in focus if b.get("staff_name")})
    services = list({b.get("service_name") for b in focus if b.get("service_name")})
    if is_today:
        prompt = (f"Right now at PureZen, there are {count} appointments still to come today. Staff on duty: {', '.join(staff) if staff else 'None'}. Services: {', '.join(services) if services else 'None'}.\n\nWrite exactly 2 sentences in present tense. No questions. No sign-off.")
    else:
        prompt = (f"Date: {_format_display_date(date)}. Appointments: {count}. Staff: {', '.join(staff) if staff else 'None'}. Services: {', '.join(services) if services else 'None'}.\n\nWrite exactly 2 sentences. No questions. No sign-off.")
    return {"date": date, "summary": llm(prompt)}


@router.get("/ai/conflicts")
def ai_conflict_check(token: str, date: str) -> Dict[str, Any]:
    if not _verify_staff_token(token): raise HTTPException(status_code=401, detail="Unauthorized.")
    bookings = [b for b in _get_all_bookings() if b.get("date") == date and b.get("status") != "Cancelled"]
    if not bookings:
        return {"date": date, "conflicts": f"No appointments on {_format_display_date(date)}."}
    from collections import defaultdict
    def normalize_time(t):
        if not t: return ""
        try: return datetime.strptime(t.strip().upper(), "%I:%M %p").strftime("%H:%M")
        except Exception: return t.strip().upper()
    staff_times: Dict[str, list] = defaultdict(list)
    for b in bookings:
        s = b.get("staff_name", "").strip(); time = normalize_time(b.get("start_time", ""))
        if s and time: staff_times[f"{s}|{time}"].append(b.get("customer_name", "Guest"))
    real_conflicts   = [f"{k.split('|')[0]} has overlapping bookings at {k.split('|')[1]}: {', '.join(v)}" for k, v in staff_times.items() if len(v) > 1]
    special_requests = [f"{b.get('customer_name','Guest')} ({b.get('start_time','')}, {b.get('service_name','')}): {b.get('special_requests')}" for b in bookings if b.get("special_requests") and b.get("special_requests").strip().lower() not in ("none","n/a","","-")]
    if not real_conflicts and not special_requests:
        return {"date": date, "conflicts": "No conflicts or special requests identified."}
    return {"date": date, "conflicts": "\n".join(real_conflicts + special_requests)}


@router.get("/ai/trends-narrative")
def ai_trends_narrative(token: str, date_from: Optional[str] = None, date_to: Optional[str] = None) -> Dict[str, Any]:
    if not _verify_staff_token(token): raise HTTPException(status_code=401, detail="Unauthorized.")
    trends      = get_trends(token, date_from=date_from, date_to=date_to)
    top_service = next(iter(trends["by_service"]), "N/A")
    top_staff   = next(iter(trends["by_staff"]), "N/A")
    date_range  = f" ({_format_display_date(date_from)} – {_format_display_date(date_to)})" if date_from and date_to else (f" (from {_format_display_date(date_from)})" if date_from else (f" (up to {_format_display_date(date_to)})" if date_to else ""))
    prompt = (f"PureZen Spa data{date_range}: {trends['total_bookings']} bookings, {trends['cancellation_rate']}% cancellation rate, top service: {top_service}, top staff: {top_staff}, peak hour: {trends['peak_hour']}.\n\nRespond in EXACTLY this format:\nObservation 1: [one factual sentence about booking volume or trends]\nObservation 2: [one factual sentence about service or staff performance]\nActionable Recommendation: [one specific, practical suggestion]\n\nNo intro. No sign-off. No extra sentences.")
    return {"narrative": llm(prompt)}


@router.get("/guest/lookup")
def guest_lookup(token: str, query: str) -> Dict[str, Any]:
    if not _verify_staff_token(token): raise HTTPException(status_code=401, detail="Unauthorized.")
    q = query.strip().lower(); q_digits = "".join(c for c in q if c.isdigit())
    matches = [b for b in _get_all_bookings() if (b.get("customer_email") or "").lower() == q or (q_digits and len(q_digits) >= 7 and q_digits in "".join(c for c in (b.get("customer_phone") or "") if c.isdigit()))]
    if not matches:
        return {"found": False, "bookings": [], "name": None, "summary": "No booking history found for this guest."}
    name = matches[0].get("customer_name", "Guest"); email = matches[0].get("customer_email", "")
    top  = Counter(b.get("service_name") for b in matches if b.get("service_name")).most_common(1)
    reqs = [b.get("special_requests") for b in matches if b.get("special_requests")]
    prompt = (f"Guest: {name}. Total bookings: {len(matches)}. Most booked service: {top[0][0] if top else 'N/A'}. Special requests: {'; '.join(reqs) if reqs else 'None'}.\n\nWrite 2 sentences summarizing this guest for staff reference. No questions. No sign-off.")
    return {"found": True, "name": name, "email": email, "summary": llm(prompt), "bookings": matches}


@router.get("/ai/customer-notes")
def ai_customer_notes(token: str, email: str) -> Dict[str, Any]:
    if not _verify_staff_token(token): raise HTTPException(status_code=401, detail="Unauthorized.")
    bks = [b for b in _get_all_bookings() if (b.get("customer_email") or "").lower() == email.lower().strip()]
    if not bks: return {"email": email, "summary": "No booking history on file for this guest."}
    name = bks[0].get("customer_name", "Guest")
    top  = Counter(b.get("service_name") for b in bks if b.get("service_name")).most_common(1)
    reqs = [b.get("special_requests") for b in bks if b.get("special_requests")]
    prompt = (f"Guest: {name}. Total bookings: {len(bks)}. Most booked service: {top[0][0] if top else 'N/A'}. Special requests: {'; '.join(reqs) if reqs else 'None'}.\n\nWrite 2 sentences summarizing this guest for staff reference. No questions. No sign-off.")
    return {"email": email, "name": name, "summary": llm(prompt)}


@router.post("/ai/query")
def ai_natural_language_query(request: AdminQueryRequest) -> Dict[str, Any]:
    if not _verify_staff_token(request.token): raise HTTPException(status_code=401, detail="Unauthorized.")
    answer = orchestrate(request.query, _get_data_fns())
    return {"query": request.query, "answer": answer}


# ---------------------------------------------------------------------------
# Walk-in booking
# ---------------------------------------------------------------------------

@router.get("/walkin/slots")
def get_walkin_slots(token: str, date: str) -> List[Dict[str, Any]]:
    if not _verify_staff_token(token): raise HTTPException(status_code=401, detail="Unauthorized.")
    from collections import defaultdict
    table    = get_availability_table()
    response = table.scan(FilterExpression=Attr("date").eq(date) & Attr("status").eq("AVAILABLE"))
    items    = [_convert_decimal(i) for i in response.get("Items", [])]
    while "LastEvaluatedKey" in response:
        response = table.scan(FilterExpression=Attr("date").eq(date) & Attr("status").eq("AVAILABLE"), ExclusiveStartKey=response["LastEvaluatedKey"])
        items += [_convert_decimal(i) for i in response.get("Items", [])]
    grouped: Dict[str, List[Dict]] = defaultdict(list)
    for item in items:
        t = item.get("start_time") or ""
        if t: grouped[t].append({"slot_id": item.get("slot_id"), "start_time": t, "end_time": item.get("end_time"), "staff_name": item.get("staff_name"), "staff_id": item.get("staff_id")})
    return [{"start_time": tv, "available_staff": grouped[tv]} for tv in sorted(grouped.keys())]


@router.post("/walkin/book")
def walkin_book(request: WalkInRequest) -> Dict[str, Any]:
    if not _verify_staff_token(request.token): raise HTTPException(status_code=401, detail="Unauthorized.")
    table    = get_availability_table()
    slot     = table.get_item(Key={"slot_id": request.slot_id}).get("Item")
    if not slot: raise HTTPException(status_code=404, detail="Slot not found.")
    if str(slot.get("status", "")).upper() != "AVAILABLE": raise HTTPException(status_code=409, detail="That slot is no longer available.")
    booking_id  = f"bk_{uuid.uuid4().hex[:12]}"
    update_expr = "SET #status = :booked, booking_id = :bid, booked_at = :bat, service_name = :svc, customer_name = :cn, customer_phone = :cp, customer_email = :ce"
    expr_values = {":booked": "BOOKED", ":available": "AVAILABLE", ":bid": booking_id, ":bat": datetime.utcnow().isoformat(), ":svc": request.service_name, ":cn": request.customer_name, ":cp": request.customer_phone, ":ce": request.customer_email}
    if request.special_requests: update_expr += ", special_requests = :req"; expr_values[":req"] = request.special_requests
    try:
        table.update_item(Key={"slot_id": request.slot_id}, UpdateExpression=update_expr, ExpressionAttributeNames={"#status": "status"}, ExpressionAttributeValues=expr_values, ConditionExpression="#status = :available")
    except Exception:
        raise HTTPException(status_code=409, detail="That slot was just taken. Please choose another.")
    return {"success": True, "booking_id": booking_id, "message": f"Appointment booked for {request.customer_name}. Confirmation: {booking_id}"}


# ---------------------------------------------------------------------------
# Staff password management
# ---------------------------------------------------------------------------

@router.post("/staff/set-password")
def set_staff_password(request: SetStaffPasswordRequest) -> Dict[str, Any]:
    if not _verify_admin_token(request.token): raise HTTPException(status_code=401, detail="Unauthorized.")
    if len(request.password) < 8: raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
    pw_hash = bcrypt.hashpw(request.password.encode(), bcrypt.gensalt()).decode()
    staff_table.update_item(Key={"staff_id": request.staff_id}, UpdateExpression="SET password_hash = :h", ExpressionAttributeValues={":h": pw_hash})
    return {"success": True, "message": "Password set successfully."}


# ---------------------------------------------------------------------------
# User Management — Admins
# ---------------------------------------------------------------------------

@router.get("/users/admins")
def list_admins(token: str) -> List[Dict[str, Any]]:
    if not _verify_admin_token(token): raise HTTPException(status_code=401, detail="Unauthorized.")
    return [{"admin_id": a.get("admin_id"), "name": a.get("name"), "email": a.get("email"), "active": a.get("active", True)} for a in _scan_all(admins_table)]


@router.post("/users/admins/create")
def create_admin(request: CreateAdminRequest) -> Dict[str, Any]:
    if not _verify_admin_token(request.token): raise HTTPException(status_code=401, detail="Unauthorized.")
    email = request.email.lower().strip()
    if admins_table.scan(FilterExpression=Attr("email").eq(email)).get("Items"):
        raise HTTPException(status_code=409, detail="An admin with this email already exists.")
    pw_hash = bcrypt.hashpw(request.password.encode(), bcrypt.gensalt()).decode()
    admin_id = f"adm_{uuid.uuid4().hex[:12]}"
    admins_table.put_item(Item={"admin_id": admin_id, "name": request.name.strip(), "email": email, "password_hash": pw_hash, "active": True, "created_at": datetime.utcnow().isoformat(), "token": ""})
    return {"success": True, "admin_id": admin_id, "message": f"Admin {request.name} created."}


@router.post("/users/admins/deactivate")
def deactivate_admin(request: AdminActionRequest) -> Dict[str, Any]:
    if not _verify_admin_token(request.token): raise HTTPException(status_code=401, detail="Unauthorized.")
    caller = admins_table.scan(FilterExpression=Attr("token").eq(request.token)).get("Items", [])
    if caller and caller[0].get("admin_id") == request.admin_id:
        raise HTTPException(status_code=400, detail="You cannot deactivate your own account.")
    admins_table.update_item(Key={"admin_id": request.admin_id}, UpdateExpression="SET active = :v", ExpressionAttributeValues={":v": False})
    return {"success": True, "message": "Admin deactivated."}


@router.post("/users/admins/reactivate")
def reactivate_admin(request: AdminActionRequest) -> Dict[str, Any]:
    if not _verify_admin_token(request.token): raise HTTPException(status_code=401, detail="Unauthorized.")
    admins_table.update_item(Key={"admin_id": request.admin_id}, UpdateExpression="SET active = :v", ExpressionAttributeValues={":v": True})
    return {"success": True, "message": "Admin reactivated."}


@router.post("/users/admins/reset-password")
def reset_admin_password(request: ResetAdminPasswordRequest) -> Dict[str, Any]:
    if not _verify_admin_token(request.token): raise HTTPException(status_code=401, detail="Unauthorized.")
    if len(request.password) < 8: raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
    pw_hash = bcrypt.hashpw(request.password.encode(), bcrypt.gensalt()).decode()
    admins_table.update_item(Key={"admin_id": request.admin_id}, UpdateExpression="SET password_hash = :h, #t = :t", ExpressionAttributeNames={"#t": "token"}, ExpressionAttributeValues={":h": pw_hash, ":t": ""})
    return {"success": True, "message": "Admin password reset successfully."}


# ---------------------------------------------------------------------------
# User Management — Customers
# ---------------------------------------------------------------------------

@router.get("/users/customers")
def list_customers(token: str) -> List[Dict[str, Any]]:
    if not _verify_admin_token(token): raise HTTPException(status_code=401, detail="Unauthorized.")
    items          = _scan_all(users_table)
    booking_counts = Counter((b.get("customer_email") or "").lower() for b in _get_all_bookings())
    result = [{"user_id": u.get("user_id"), "name": u.get("name"), "email": (u.get("email") or "").lower(), "phone": u.get("phone"), "created_at": u.get("created_at"), "active": u.get("active", True), "bookings": booking_counts.get((u.get("email") or "").lower(), 0)} for u in items]
    result.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return result


@router.post("/users/customers/deactivate")
def deactivate_customer(request: UserActionRequest) -> Dict[str, Any]:
    if not _verify_admin_token(request.token): raise HTTPException(status_code=401, detail="Unauthorized.")
    users_table.update_item(Key={"user_id": request.user_id}, UpdateExpression="SET active = :v", ExpressionAttributeValues={":v": False})
    return {"success": True, "message": "Customer account deactivated."}


@router.post("/users/customers/reactivate")
def reactivate_customer(request: UserActionRequest) -> Dict[str, Any]:
    if not _verify_admin_token(request.token): raise HTTPException(status_code=401, detail="Unauthorized.")
    users_table.update_item(Key={"user_id": request.user_id}, UpdateExpression="SET active = :v", ExpressionAttributeValues={":v": True})
    return {"success": True, "message": "Customer account reactivated."}


# ---------------------------------------------------------------------------
# User Management — Staff
# ---------------------------------------------------------------------------

@router.get("/users/staff")
def list_staff(token: str) -> List[Dict[str, Any]]:
    if not _verify_admin_token(token): raise HTTPException(status_code=401, detail="Unauthorized.")
    result = []
    for s in _scan_all(staff_table):
        skills = [sk if isinstance(sk, str) else str(sk) for sk in s.get("skills", [])]
        result.append({"staff_id": s.get("staff_id"), "first_name": s.get("first_name"), "last_name": s.get("last_name"), "display_name": s.get("display_name"), "role": s.get("role"), "email": s.get("email"), "employment_type": s.get("employment_type"), "weekly_hours_limit": s.get("weekly_hours_limit"), "skills": skills, "is_active": s.get("is_active", True), "location_id": s.get("location_id", "omaha_main")})
    result.sort(key=lambda x: (x.get("last_name") or "", x.get("first_name") or ""))
    return result


@router.post("/users/staff/create")
def create_staff(request: CreateStaffRequest) -> Dict[str, Any]:
    if not _verify_admin_token(request.token): raise HTTPException(status_code=401, detail="Unauthorized.")
    staff_id = f"stf_{uuid.uuid4().hex[:6]}"; display_name = f"{request.first_name} {request.last_name[0]}."
    staff_table.put_item(Item={"staff_id": staff_id, "first_name": request.first_name.strip(), "last_name": request.last_name.strip(), "display_name": display_name, "role": request.role.strip(), "email": request.email.lower().strip(), "employment_type": request.employment_type, "weekly_hours_limit": request.weekly_hours_limit, "skills": request.skills, "is_active": True, "location_id": request.location_id, "created_at": datetime.utcnow().isoformat()})
    return {"success": True, "staff_id": staff_id, "display_name": display_name, "message": f"Staff member {display_name} created."}


@router.post("/users/staff/toggle")
def toggle_staff(request: StaffActionRequest) -> Dict[str, Any]:
    if not _verify_admin_token(request.token): raise HTTPException(status_code=401, detail="Unauthorized.")
    staff_table.update_item(Key={"staff_id": request.staff_id}, UpdateExpression="SET is_active = :v", ExpressionAttributeValues={":v": request.is_active})
    return {"success": True, "message": f"Staff member {'activated' if request.is_active else 'deactivated'}."}
