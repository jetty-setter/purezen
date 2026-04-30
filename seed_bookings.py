#!/usr/bin/env python3
"""
seed_bookings.py — Seed realistic bookings across the next 2 months.

Run on EC2:
    python3 seed_bookings.py

Requires boto3 and AWS credentials (already configured on the instance).
Finds AVAILABLE slots in purezen_availability and books them with
realistic fake customer data, spread across services and staff.
"""

import random
import uuid
from datetime import datetime, timedelta

import boto3
from boto3.dynamodb.conditions import Attr

AWS_REGION         = "us-east-1"
AVAILABILITY_TABLE = "purezen_availability"

dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
table    = dynamodb.Table(AVAILABILITY_TABLE)

# ── Fake customers ─────────────────────────────────────────────────────────
CUSTOMERS = [
    {"name": "Rachel Moore",    "email": "rachel.moore@gmail.com",    "phone": "(402) 555-0181"},
    {"name": "James Holloway",  "email": "james.holloway@gmail.com",  "phone": "(402) 555-0234"},
    {"name": "Priya Sharma",    "email": "priya.sharma@outlook.com",  "phone": "(402) 555-0317"},
    {"name": "Derek Nguyen",    "email": "derek.nguyen@yahoo.com",    "phone": "(402) 555-0422"},
    {"name": "Amber Collins",   "email": "amber.collins@gmail.com",   "phone": "(402) 555-0539"},
    {"name": "Marcus Bell",     "email": "marcus.bell@icloud.com",    "phone": "(402) 555-0648"},
    {"name": "Sofia Reyes",     "email": "sofia.reyes@gmail.com",     "phone": "(402) 555-0751"},
    {"name": "Tyler Grant",     "email": "tyler.grant@outlook.com",   "phone": "(402) 555-0864"},
    {"name": "Hannah Brooks",   "email": "hannah.brooks@gmail.com",   "phone": "(402) 555-0972"},
    {"name": "Lena Fischer",    "email": "lena.fischer@gmail.com",    "phone": "(402) 555-0183"},
    {"name": "Omar Hassan",     "email": "omar.hassan@yahoo.com",     "phone": "(402) 555-0295"},
    {"name": "Cassidy Walsh",   "email": "cassidy.walsh@gmail.com",   "phone": "(402) 555-0348"},
    {"name": "Nathan Park",     "email": "nathan.park@outlook.com",   "phone": "(402) 555-0457"},
    {"name": "Isabelle Turner", "email": "isabelle.turner@gmail.com", "phone": "(402) 555-0561"},
    {"name": "Victor Chen",     "email": "victor.chen@icloud.com",    "phone": "(402) 555-0674"},
    {"name": "Stacy Young",     "email": "stacy.young@gmail.com",     "phone": "(960) 541-1234"},
    {"name": "Christina Koch",  "email": "christina.koch@gmail.com",  "phone": "(402) 555-0789"},
]

# Realistic special requests (most bookings have none)
SPECIAL_REQUESTS = [
    None, None, None, None, None, None,  # most have none
    "Allergic to lavender",
    "Prefer light pressure",
    "Please use unscented products",
    "First time — please explain the process",
    "Recovering from shoulder surgery, avoid right shoulder",
    "Prefer female therapist",
    "Running 10 minutes late",
    None, None, None,
]


def scan_available_slots(date_from: str, date_to: str):
    """Scan all AVAILABLE slots within the date range."""
    items = []
    kwargs = {
        "FilterExpression": (
            Attr("status").eq("AVAILABLE") &
            Attr("date").between(date_from, date_to)
        )
    }
    resp = table.scan(**kwargs)
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
    return items


def book_slot(slot, customer, special_request, service_name=None):
    """Write a BOOKED record to the availability table."""
    booking_id = f"bk_{uuid.uuid4().hex[:12]}"
    svc = service_name or slot.get("service_name") or "Swedish Massage"
    try:
        table.update_item(
            Key={"slot_id": slot["slot_id"]},
            UpdateExpression=(
                "SET #status = :booked, booking_id = :bid, booked_at = :at, "
                "service_name = :svc, "
                "customer_name = :cn, customer_email = :ce, customer_phone = :cp"
                + (", special_requests = :sr" if special_request else "")
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":booked":    "BOOKED",
                ":bid":       booking_id,
                ":at":        datetime.utcnow().isoformat(),
                ":svc":       svc,
                ":cn":        customer["name"],
                ":ce":        customer["email"],
                ":cp":        customer["phone"],
                ":available": "AVAILABLE",
                **({":sr": special_request} if special_request else {}),
            },
            ConditionExpression="#status = :available",
        )
        return booking_id
    except Exception as e:
        if "ConditionalCheckFailedException" in str(e):
            return None
        raise


def clear_seeded_bookings(date_from: str, date_to: str, seed_emails: set):
    """Release bookings made by seed customers so we can re-seed cleanly."""
    print("Clearing previously seeded bookings...")
    items = []
    kwargs = {
        "FilterExpression": (
            Attr("status").eq("BOOKED") &
            Attr("date").between(date_from, date_to)
        )
    }
    resp = table.scan(**kwargs)
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))

    cleared = 0
    for item in items:
        if (item.get("customer_email") or "").lower() in seed_emails:
            try:
                table.update_item(
                    Key={"slot_id": item["slot_id"]},
                    UpdateExpression=(
                        "SET #status = :available "
                        "REMOVE booking_id, booked_at, customer_name, customer_email, "
                        "customer_phone, special_requests, service_name"
                    ),
                    ExpressionAttributeNames={"#status": "status"},
                    ExpressionAttributeValues={":available": "AVAILABLE"},
                )
                cleared += 1
            except Exception as e:
                print(f"  Warning: could not clear slot {item.get('slot_id')}: {e}")
    print(f"  Cleared {cleared} previously seeded bookings.\n")


def main():
    today     = datetime.utcnow().date()
    date_from = (today + timedelta(days=1)).strftime("%Y-%m-%d")
    date_to   = (today + timedelta(days=60)).strftime("%Y-%m-%d")

    seed_emails = {c["email"].lower() for c in CUSTOMERS}
    clear_seeded_bookings(date_from, date_to, seed_emails)

    print(f"Scanning AVAILABLE slots from {date_from} to {date_to}...")
    slots = scan_available_slots(date_from, date_to)
    print(f"Found {len(slots)} available slots.")

    if not slots:
        print("No available slots found. Make sure availability is seeded first.")
        return

    # Sort by date then time for predictable distribution
    slots.sort(key=lambda s: (s.get("date", ""), s.get("start_time", "")))

    # Target: book roughly 60% of available slots for a realistic look
    # Bias toward certain services being more popular (massage > facial > body)
    target_count = max(1, int(len(slots) * 0.60))

    # Shuffle slots but keep some date-based grouping for realistic patterns
    # Book more on weekdays, fewer on weekends
    weekday_slots = [s for s in slots if datetime.strptime(s["date"], "%Y-%m-%d").weekday() < 5]
    weekend_slots = [s for s in slots if datetime.strptime(s["date"], "%Y-%m-%d").weekday() >= 5]

    # Sample from weekdays more heavily
    sampled = (
        random.sample(weekday_slots, min(int(target_count * 0.75), len(weekday_slots))) +
        random.sample(weekend_slots, min(int(target_count * 0.25), len(weekend_slots)))
    )
    random.shuffle(sampled)

    # Service popularity weights for realistic distribution
    SERVICE_WEIGHTS = {
        "Swedish Massage":        10,
        "Deep Tissue Massage":     8,
        "Hot Stone Massage":       7,
        "Classic Facial":          8,
        "Hydrating Deluxe Facial": 6,
        "Prenatal Massage":        4,
        "Sea Salt Body Scrub":     5,
        "Aromatherapy Add-On":     3,
    }

    booked_count   = 0
    skipped_count  = 0
    used_customers = {}
    booked_per_day: dict = {}  # date → set of emails already booked that day

    for slot in sampled:
        slot_date = slot.get("date", "")

        # Pick service
        offered = slot.get("services_offered")
        if isinstance(offered, list) and offered:
            weights = [SERVICE_WEIGHTS.get(s, 1) for s in offered]
            service = random.choices(offered, weights=weights, k=1)[0]
        else:
            service = slot.get("service_name") or "Swedish Massage"
        if service == "Aromatherapy Add-On":
            service = "Swedish Massage"
        slot["_booked_service"] = service

        # Pick a customer not already booked on this date
        already_today    = booked_per_day.get(slot_date, set())
        available        = [c for c in CUSTOMERS if c["email"] not in already_today]

        if not available:
            skipped_count += 1
            continue  # every customer already has a booking today — skip slot

        # 25% chance to reuse a repeat customer from a previous day
        if used_customers and random.random() < 0.25:
            repeat_pool = [c for c in used_customers.values() if c["email"] not in already_today]
            customer = random.choice(repeat_pool) if repeat_pool else random.choice(available)
        else:
            customer = random.choice(available)

        used_customers[customer["email"]] = customer

        special = random.choice(SPECIAL_REQUESTS)
        bid = book_slot(slot, customer, special, service_name=slot.get("_booked_service"))

        if bid:
            booked_count += 1
            booked_per_day.setdefault(slot_date, set()).add(customer["email"])
            svc   = slot.get("_booked_service", slot.get("service_name", "?"))
            staff = slot.get("staff_name", "?")
            print(f"  ✓ {slot_date} {slot.get('start_time','?'):10s} | {svc:30s} | {staff:15s} | {customer['name']} → {bid}")
        else:
            skipped_count += 1

    print(f"\nDone. Booked {booked_count} appointments, skipped {skipped_count}.")
    print(f"Date range: {date_from} → {date_to}")


if __name__ == "__main__":
    main()
