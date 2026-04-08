from __future__ import annotations

from datetime import datetime, timedelta


def build_intent_prompt(message: str) -> str:
    today = datetime.now().date()
    today_str = today.strftime("%Y-%m-%d")
    tomorrow_str = (today + timedelta(days=1)).strftime("%Y-%m-%d")

    # Compute nearest future 28th for the example
    if today.day < 28:
        example_28 = today.replace(day=28).strftime("%Y-%m-%d")
    else:
        m = today.month % 12 + 1
        y = today.year + (1 if m == 1 else 0)
        example_28 = today.replace(year=y, month=m, day=28).strftime("%Y-%m-%d")

    return f"""You are a JSON intent classifier for a spa booking system. Today is {today_str}.

Output ONLY a single JSON object. No markdown. No code fences. No explanation. No extra keys.

INTENT VALUES (pick exactly one):
- service_question      → asking what services, facials, or massages exist or are offered
- availability_check    → asking about open times, availability, "do you have", "when can I"
- booking_request       → wants to book, reserve, schedule, confirm, or mentions a specific time like "9am"
- cancel_request        → wants to cancel a booking or appointment
- reschedule_request    → wants to reschedule, move, or change an appointment
- recommendation_request → describing how they feel, a mood, a symptom, or asking what's best for them. Examples: "I feel stressed", "my back hurts", "I need to relax", "what would you recommend", "I've never been to a spa", "I want to treat myself", "I had a long week"
- general_question      → anything else

EXTRACTION RULES:
- service_name: normalize to title case. "deep tissue" → "Deep Tissue Massage", "swedish" → "Swedish Massage", "facial" → "Facial". null if not mentioned.
- date: convert to YYYY-MM-DD. "tomorrow" = {tomorrow_str}. "the 28th" = {example_28}. null if not mentioned.
- start_time: keep as written e.g. "2pm", "9:00 AM". null if not mentioned.
- booking_id: extract only if format is bk_abc123. null otherwise.
- customer_name, customer_email, customer_phone, notes: null unless clearly present.

EXAMPLES:

Message: "Book a Swedish Massage tomorrow"
{{"intent":"booking_request","service_name":"Swedish Massage","date":"{tomorrow_str}","start_time":null,"booking_id":null,"customer_name":null,"customer_email":null,"customer_phone":null,"notes":null}}

Message: "What facials do you offer?"
{{"intent":"service_question","service_name":null,"date":null,"start_time":null,"booking_id":null,"customer_name":null,"customer_email":null,"customer_phone":null,"notes":null}}

Message: "Do you have availability for a deep tissue on Saturday?"
{{"intent":"availability_check","service_name":"Deep Tissue Massage","date":null,"start_time":null,"booking_id":null,"customer_name":null,"customer_email":null,"customer_phone":null,"notes":null}}

Message: "Can I book a deep tissue for the 28th?"
{{"intent":"booking_request","service_name":"Deep Tissue Massage","date":"{example_28}","start_time":null,"booking_id":null,"customer_name":null,"customer_email":null,"customer_phone":null,"notes":null}}

Message: "Cancel my booking bk_abc123def456"
{{"intent":"cancel_request","service_name":null,"date":null,"start_time":null,"booking_id":"bk_abc123def456","customer_name":null,"customer_email":null,"customer_phone":null,"notes":null}}

Message: "I need to reschedule my appointment"
{{"intent":"reschedule_request","service_name":null,"date":null,"start_time":null,"booking_id":null,"customer_name":null,"customer_email":null,"customer_phone":null,"notes":null}}

Now classify this message:
"{message}"

JSON:""".strip()
