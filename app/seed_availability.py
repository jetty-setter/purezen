"""
seed_availability.py

Deletes and recreates purezen_availability with a clean structure:
  - One row per staff member per time slot
  - services_offered is a list of services that staff member can perform
  - service_name is set at booking time, not at slot creation

Run from the app directory:
  python3 seed_availability.py

Requires AWS credentials with DynamoDB access.
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import date, datetime, timedelta

import boto3
from boto3.dynamodb.conditions import Key

AWS_REGION          = os.getenv("AWS_REGION", "us-east-1")
TABLE_NAME          = os.getenv("AVAILABILITY_TABLE_NAME", "purezen_availability")
BILLING_MODE        = "PAY_PER_REQUEST"

# ---------------------------------------------------------------------------
# Staff
# ---------------------------------------------------------------------------

STAFF = [
    {
        "staff_id":  "stf_001",
        "staff_name": "Ava",
        "services": [
            "Swedish Massage",
            "Deep Tissue Massage",
            "Prenatal Massage",
            "Hydrating Deluxe Facial",
        ],
    },
    {
        "staff_id":  "stf_002",
        "staff_name": "Kai",
        "services": [
            "Swedish Massage",
            "Deep Tissue Massage",
            "Hot Stone Massage",
            "Classic Facial",
        ],
    },
    {
        "staff_id":  "stf_003",
        "staff_name": "Mia",
        "services": [
            "Swedish Massage",
            "Hydrating Deluxe Facial",
            "Classic Facial",
            "Sea Salt Body Scrub",
        ],
    },
    {
        "staff_id":  "stf_004",
        "staff_name": "Lena",
        "services": [
            "Deep Tissue Massage",
            "Hot Stone Massage",
            "Prenatal Massage",
            "Sea Salt Body Scrub",
        ],
    },
    {
        "staff_id":  "stf_005",
        "staff_name": "Noah",
        "services": [
            "Swedish Massage",
            "Deep Tissue Massage",
            "Hot Stone Massage",
            "Classic Facial",
            "Sea Salt Body Scrub",
        ],
    },
]

# ---------------------------------------------------------------------------
# Schedule config
# ---------------------------------------------------------------------------

# (start_hour, end_hour) per weekday (0=Mon ... 6=Sun)
HOURS = {
    0: (9, 19),  # Monday
    1: (9, 19),  # Tuesday
    2: (9, 19),  # Wednesday
    3: (9, 19),  # Thursday
    4: (9, 19),  # Friday
    5: (9, 17),  # Saturday
    6: (9, 17),  # Sunday
}

# Slot durations in minutes, cycling per time slot across staff
# Gives a natural mix of 30 and 60 minute blocks
DURATION_CYCLE = [60, 60, 30, 60, 30, 60, 60, 30, 60, 30]

LOCATION_ID = "loc_001"
ROOM_TYPE   = "Treatment Room"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def slot_id(d: date, hour: int, minute: int, staff_id: str) -> str:
    return "slot_{}_{}_{:02d}{:02d}_{}".format(
        d.strftime("%Y-%m-%d"),
        d.strftime("%A").lower(),
        hour, minute,
        staff_id,
    )


def fmt_time(hour: int, minute: int) -> str:
    suffix = "AM" if hour < 12 else "PM"
    h = hour if hour <= 12 else hour - 12
    if h == 0:
        h = 12
    return f"{h}:{minute:02d} {suffix}"


def date_start_key(d: date, hour: int, minute: int) -> str:
    return "{date}#{h:02d}{m:02d}".format(date=d.isoformat(), h=hour, m=minute)


def generate_slots(start_date: date, num_days: int) -> list[dict]:
    slots = []
    duration_idx = 0

    for day_offset in range(num_days):
        d = start_date + timedelta(days=day_offset)
        weekday = d.weekday()
        start_h, end_h = HOURS[weekday]

        # Build list of (hour, minute) slots for this day
        times = []
        hour, minute = start_h, 0
        while hour < end_h:
            times.append((hour, minute))
            duration = DURATION_CYCLE[duration_idx % len(DURATION_CYCLE)]
            duration_idx += 1
            minute += duration
            if minute >= 60:
                hour += minute // 60
                minute = minute % 60

        for staff in STAFF:
            for (hour, minute) in times:
                sid = slot_id(d, hour, minute, staff["staff_id"])
                end_minute = minute + 60  # display end time always +60 for simplicity
                end_hour   = hour + end_minute // 60
                end_minute = end_minute % 60

                slots.append({
                    "slot_id":          sid,
                    "date":             d.isoformat(),
                    "date_start":       date_start_key(d, hour, minute),
                    "start_time":       fmt_time(hour, minute),
                    "end_time":         fmt_time(end_hour, end_minute),
                    "duration_minutes": DURATION_CYCLE[(duration_idx - 1) % len(DURATION_CYCLE)],
                    "staff_id":         staff["staff_id"],
                    "staff_name":       staff["staff_name"],
                    "services_offered": staff["services"],
                    "service_name":     "",          # set at booking time
                    "location_id":      LOCATION_ID,
                    "room_type":        ROOM_TYPE,
                    "status":           "AVAILABLE",
                })

    return slots


# ---------------------------------------------------------------------------
# DynamoDB operations
# ---------------------------------------------------------------------------

def delete_table_if_exists(client, table_name: str) -> None:
    try:
        client.describe_table(TableName=table_name)
        print(f"Deleting existing table '{table_name}'...")
        client.delete_table(TableName=table_name)
        waiter = client.get_waiter("table_not_exists")
        waiter.wait(TableName=table_name)
        print("Table deleted.")
    except client.exceptions.ResourceNotFoundException:
        print(f"Table '{table_name}' does not exist — skipping delete.")


def create_table(client, table_name: str) -> None:
    print(f"Creating table '{table_name}'...")
    client.create_table(
        TableName=table_name,
        BillingMode=BILLING_MODE,
        AttributeDefinitions=[
            {"AttributeName": "slot_id",    "AttributeType": "S"},
            {"AttributeName": "date",       "AttributeType": "S"},
            {"AttributeName": "status",     "AttributeType": "S"},
            {"AttributeName": "booking_id", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "slot_id", "KeyType": "HASH"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "date-status-index",
                "KeySchema": [
                    {"AttributeName": "date",   "KeyType": "HASH"},
                    {"AttributeName": "status", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
            {
                "IndexName": "booking-id-index",
                "KeySchema": [
                    {"AttributeName": "booking_id", "KeyType": "HASH"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
    )
    waiter = client.get_waiter("table_exists")
    waiter.wait(TableName=table_name)
    print("Table created and active.")


def seed_table(resource, table_name: str, slots: list[dict]) -> None:
    table = resource.Table(table_name)
    print(f"Seeding {len(slots)} slots...")

    with table.batch_writer() as batch:
        for slot in slots:
            batch.put_item(Item=slot)

    print("Seeding complete.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    client   = boto3.client("dynamodb",  region_name=AWS_REGION)
    resource = boto3.resource("dynamodb", region_name=AWS_REGION)

    start_date = date.today() + timedelta(days=1)  # start from tomorrow
    slots = generate_slots(start_date, num_days=7)

    print(f"\nPureZen availability seed")
    print(f"  Table   : {TABLE_NAME}")
    print(f"  Region  : {AWS_REGION}")
    print(f"  From    : {start_date}")
    print(f"  To      : {start_date + timedelta(days=6)}")
    print(f"  Staff   : {len(STAFF)}")
    print(f"  Slots   : {len(slots)}")
    print()

    delete_table_if_exists(client, TABLE_NAME)
    create_table(client, TABLE_NAME)
    seed_table(resource, TABLE_NAME, slots)

    print(f"\nDone. {len(slots)} slots seeded across 7 days.")


if __name__ == "__main__":
    main()
