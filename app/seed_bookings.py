#!/usr/bin/env python3
"""
seed_bookings.py
Books ~50 realistic appointments against existing AVAILABLE slots in DynamoDB.
Spreads bookings across past and future dates with real service names.

Run from your backend EC2:
    python3 seed_bookings.py

Dry run (preview only, no writes):
    python3 seed_bookings.py --dry-run
"""

import sys
import uuid
import random
from datetime import datetime, timedelta

import boto3
from boto3.dynamodb.conditions import Attr

AWS_REGION         = "us-east-1"
AVAILABILITY_TABLE = "purezen_availability"
TARGET_BOOKINGS    = 50
DRY_RUN            = "--dry-run" in sys.argv

# ---------------------------------------------------------------------------
# Services — weighted to reflect realistic booking distribution
# ---------------------------------------------------------------------------

SERVICES = [
    "Swedish Massage",
    "Swedish Massage",
    "Swedish Massage",
    "Deep Tissue Massage",
    "Deep Tissue Massage",
    "Deep Tissue Massage",
    "Hot Stone Massage",
    "Hot Stone Massage",
    "Prenatal Massage",
    "Prenatal Massage",
    "Hydrating Deluxe Facial",
    "Hydrating Deluxe Facial",
    "Classic Facial",
    "Classic Facial",
    "Sea Salt Body Scrub",
    "Aromatherapy Add-On",
]

# ---------------------------------------------------------------------------
# Guests
# ---------------------------------------------------------------------------

GUESTS = [
    {"name": "Claire Beaumont",  "email": "claire.beaumont@gmail.com",  "phone": "(402) 555-0101"},
    {"name": "Marcus Webb",      "email": "marcus.webb@outlook.com",     "phone": "(402) 555-0102"},
    {"name": "Sofia Navarro",    "email": "sofia.navarro@yahoo.com",     "phone": "(402) 555-0103"},
    {"name": "James Thornton",   "email": "james.thornton@gmail.com",    "phone": "(402) 555-0104"},
    {"name": "Priya Sharma",     "email": "priya.sharma@gmail.com",      "phone": "(402) 555-0105"},
    {"name": "Ethan Cole",       "email": "ethan.cole@hotmail.com",      "phone": "(402) 555-0106"},
    {"name": "Natalie Brooks",   "email": "natalie.brooks@gmail.com",    "phone": "(402) 555-0107"},
    {"name": "Daniel Park",      "email": "daniel.park@outlook.com",     "phone": "(402) 555-0108"},
    {"name": "Rachel Torres",    "email": "rachel.torres@yahoo.com",     "phone": "(402) 555-0109"},
    {"name": "Owen Mitchell",    "email": "owen.mitchell@gmail.com",     "phone": "(402) 555-0110"},
    {"name": "Isabella Chen",    "email": "isabella.chen@gmail.com",     "phone": "(402) 555-0111"},
    {"name": "Lucas Freeman",    "email": "lucas.freeman@outlook.com",   "phone": "(402) 555-0112"},
    {"name": "Amara Okafor",     "email": "amara.okafor@gmail.com",      "phone": "(402) 555-0113"},
    {"name": "Tyler Hansen",     "email": "tyler.hansen@yahoo.com",      "phone": "(402) 555-0114"},
    {"name": "Grace Kim",        "email": "grace.kim@gmail.com",         "phone": "(402) 555-0115"},
    {"name": "Noah Sullivan",    "email": "noah.sullivan@hotmail.com",   "phone": "(402) 555-0116"},
    {"name": "Zoe Patel",        "email": "zoe.patel@gmail.com",         "phone": "(402) 555-0117"},
    {"name": "Carter Davis",     "email": "carter.davis@outlook.com",    "phone": "(402) 555-0118"},
    {"name": "Maya Johnson",     "email": "maya.johnson@gmail.com",      "phone": "(402) 555-0119"},
    {"name": "Liam Anderson",    "email": "liam.anderson@yahoo.com",     "phone": "(402) 555-0120"},
]

SPECIAL_REQUESTS = [
    None, None, None, None, None, None,
    "Prefers light pressure",
    "Allergic to lavender oil",
    "First time guest — please provide a brief orientation",
    "Prefers female therapist",
    "Requesting extra focus on shoulders and neck",
    "Please keep room temperature warm",
    "Has lower back sensitivity",
    "Prefers no music during session",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def scan_all(table, filter_expression=None):
    kwargs = {}
    if filter_expression is not None:
        kwargs["FilterExpression"] = filter_expression
    items = []
    resp = table.scan(**kwargs)
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
    return items


def spread_across_dates(slots, target=50):
    """70% past bookings, 30% future for realistic history."""
    today = datetime.utcnow().date().isoformat()
    past   = [s for s in slots if (s.get("date") or "") <  today]
    future = [s for s in slots if (s.get("date") or "") >= today]

    random.shuffle(past)
    random.shuffle(future)

    past_target   = int(target * 0.70)
    future_target = target - past_target

    selected = past[:past_target] + future[:future_target]

    # Top up if either pool was short
    if len(selected) < target:
        remaining = [s for s in slots if s not in selected]
        random.shuffle(remaining)
        selected += remaining[:target - len(selected)]

    return selected


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
    table    = dynamodb.Table(AVAILABILITY_TABLE)

    print(f"Scanning {AVAILABILITY_TABLE} for AVAILABLE slots…")
    available = scan_all(table, Attr("status").eq("AVAILABLE"))

    if not available:
        print("No AVAILABLE slots found. Seed your availability table first.")
        sys.exit(1)

    print(f"Found {len(available)} available slots.")

    selected = spread_across_dates(available, TARGET_BOOKINGS)
    print(f"Selected {len(selected)} slots to book.")

    if DRY_RUN:
        print("\n--- DRY RUN — no writes ---")
        for slot in selected:
            guest   = random.choice(GUESTS)
            service = random.choice(SERVICES)
            label   = "PAST" if (slot.get("date") or "") < datetime.utcnow().date().isoformat() else "UPCOMING"
            print(f"  [{label}] {slot.get('date')} {slot.get('start_time')} | {service} | {slot.get('staff_name','?')} → {guest['name']}")
        print(f"\nTotal: {len(selected)} bookings would be created.")
        return

    booked  = 0
    skipped = 0
    today   = datetime.utcnow().date().isoformat()

    for slot in selected:
        slot_id = str(slot["slot_id"])

        # Re-verify still available
        current = table.get_item(Key={"slot_id": slot_id}).get("Item")
        if not current or str(current.get("status", "")).upper() != "AVAILABLE":
            skipped += 1
            continue

        guest      = random.choice(GUESTS)
        service    = random.choice(SERVICES)
        req        = random.choice(SPECIAL_REQUESTS)
        booking_id = f"bk_{uuid.uuid4().hex[:12]}"
        booked_at  = datetime.utcnow().isoformat()
        label      = "PAST" if (slot.get("date") or "") < today else "UPCOMING"

        update_expr = (
            "SET #status = :booked, booking_id = :bid, booked_at = :bat, "
            "service_name = :svc, customer_name = :cname, "
            "customer_phone = :cphone, customer_email = :cemail"
        )
        expr_names  = {"#status": "status"}
        expr_values = {
            ":booked":  "BOOKED",
            ":available": "AVAILABLE",
            ":bid":     booking_id,
            ":bat":     booked_at,
            ":svc":     service,
            ":cname":   guest["name"],
            ":cphone":  guest["phone"],
            ":cemail":  guest["email"],
        }

        if req:
            update_expr += ", special_requests = :req"
            expr_values[":req"] = req

        try:
            table.update_item(
                Key={"slot_id": slot_id},
                UpdateExpression=update_expr,
                ExpressionAttributeNames=expr_names,
                ExpressionAttributeValues=expr_values,
                ConditionExpression="#status = :available",
            )
            booked += 1
            print(f"  ✓ [{label}] {slot.get('date')} {slot.get('start_time')} | {service} | {slot.get('staff_name','?')} → {guest['name']} ({booking_id})")
        except Exception as exc:
            skipped += 1
            print(f"  ✗ Skipped {slot_id}: {exc}")

    print(f"\nDone. Booked: {booked} | Skipped: {skipped}")
    if booked < TARGET_BOOKINGS:
        print(f"Note: Only {booked} slots were available. Add more availability to reach {TARGET_BOOKINGS}.")


if __name__ == "__main__":
    main()
