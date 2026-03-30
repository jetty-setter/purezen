from typing import Any, Dict, Optional
from pydantic import BaseModel, EmailStr


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    context: Optional[Dict[str, Any]] = None


class BookingRequest(BaseModel):
    service_name: str
    date: str
    start_time: str
    customer_name: str
    customer_email: EmailStr
    customer_phone: str
    staff_id: Optional[str] = None
    notes: Optional[str] = None


class CancelBookingRequest(BaseModel):
    booking_id: str
    reason: Optional[str] = None


class RescheduleBookingRequest(BaseModel):
    booking_id: str
    new_date: str
    new_start_time: str
