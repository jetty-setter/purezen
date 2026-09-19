#!/usr/bin/env python3
"""
Seed a rolling portfolio dataset into PureZen's availability table.

The admin console reads appointments directly from purezen_availability, so
demo appointments live in that table too. This script creates synthetic
BOOKED/CANCELLED rows only. It does not modify real availability or real
bookings.

Default window:
  * 30 days of history for current analytics and guest history
  * future appointments through March 31, 2027 for the active job-search window

Safety:
  * dry-run is the default
  * generated rows are tagged demo_seed=true
  * --apply refreshes only prior demo_seed rows
  * --reset deletes only demo_seed rows
  * non-demo rows are never updated or deleted
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import os
import random
import re
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

import boto3
from boto3.dynamodb.conditions import Attr


DEFAULT_TABLE = os.getenv("AVAILABILITY_TABLE", "purezen_availability")
DEFAULT_REGION = os.getenv("AWS_REGION", "us-east-1")
SEED_VERSION = "portfolio-rolling-v2"
DEFAULT_THROUGH_DATE = "2027-03-31"

COPY_FIELDS = (
    "staff_id",
    "staff_name",
    "location_id",
    "location_name",
    "room_type",
    "start_time",
    "end_time",
    "duration_minutes",
    "services_offered",
    "service_id",
)

GUESTS: Sequence[Tuple[str, str, str]] = (
    ("Maya Thompson", "maya.thompson@example.com", "(402) 555-0101"),
    ("Jordan Lee", "jordan.lee@example.com", "(402) 555-0102"),
    ("Sofia Ramirez", "sofia.ramirez@example.com", "(402) 555-0103"),
    ("Avery Morgan", "avery.morgan@example.com", "(402) 555-0104"),
    ("Nina Patel", "nina.patel@example.com", "(402) 555-0105"),
    ("Elliot Brooks", "elliot.brooks@example.com", "(402) 555-0106"),
    ("Camille Reed", "camille.reed@example.com", "(402) 555-0107"),
    ("Noah Bennett", "noah.bennett@example.com", "(402) 555-0108"),
    ("Tessa Nguyen", "tessa.nguyen@example.com", "(402) 555-0109"),
    ("Marcus Hill", "marcus.hill@example.com", "(402) 555-0110"),
    ("Leah Foster", "leah.foster@example.com", "(402) 555-0111"),
    ("Riley Adams", "riley.adams@example.com", "(402) 555-0112"),
)

SPECIAL_REQUESTS: Sequence[str | None] = (
    None,
    None,
    None,
    None,
    None,
    "Fragrance-free products if available",
    "Prefers a quiet room",
    "Focus on shoulders and upper back",
    "First visit, please allow a few minutes for questions",
)

# A small number of appointments per day keeps scans fast while still making
# the admin console, trends, guest history, and AI tools feel populated.
BOOKINGS_PER_DAY: Mapping[int, Tuple[int, int]] = {
    0: (2, 4),
    1: (2, 4),
    2: (3, 5),
    3: (3, 5),
    4: (4, 6),
    5: (4, 7),
    6: (2, 4),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed rolling PureZen portfolio appointments."
    )
    parser.add_argument(
        "--table",
        default=DEFAULT_TABLE,
        help=f"DynamoDB table (default: {DEFAULT_TABLE})",
    )
    parser.add_argument(
        "--region",
        default=DEFAULT_REGION,
        help=f"AWS region (default: {DEFAULT_REGION})",
    )
    parser.add_argument(
        "--past-days",
        type=int,
        default=30,
        help="Days of history to create (default: 30)",
    )
    parser.add_argument(
        "--through",
        default=DEFAULT_THROUGH_DATE,
        help=f"Seed future appointments through this date (default: {DEFAULT_THROUGH_DATE})",
    )
    parser.add_argument(
        "--future-days",
        type=int,
        default=None,
        help="Optional rolling-day override. When set, overrides --through.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=20260918,
        help="Deterministic seed for repeatable demo data",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the dataset. Without this flag, nothing is changed.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete only rows created by this demo seeder.",
    )
    parser.add_argument(
        "--allow-other-table",
        action="store_true",
        help="Permit a DynamoDB table that does not start with purezen_.",
    )
    return parser.parse_args()


def scan_all(table, filter_expression=None) -> List[Dict[str, Any]]:
    kwargs: Dict[str, Any] = {}
    if filter_expression is not None:
        kwargs["FilterExpression"] = filter_expression

    items: List[Dict[str, Any]] = []
    while True:
        response = table.scan(**kwargs)
        items.extend(response.get("Items", []))
        if "LastEvaluatedKey" not in response:
            return items
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def parse_iso_date(value: Any) -> date | None:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def normalize_time(value: Any) -> str:
    raw = str(value or "").strip().upper()
    if not raw:
        return ""

    for fmt in ("%I:%M %p", "%I %p", "%H:%M"):
        try:
            return datetime.strptime(raw, fmt).strftime("%H:%M")
        except ValueError:
            continue

    return raw


def slug(value: Any) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", str(value or "").lower()).strip("-")
    return text or "unknown"


def deterministic_rng(seed: int, *parts: Any) -> random.Random:
    material = "|".join([str(seed), *(str(part) for part in parts)])
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return random.Random(int(digest[:16], 16))


def template_group_key(item: Mapping[str, Any]) -> Tuple[str, str]:
    staff = str(item.get("staff_id") or item.get("staff_name") or "")
    start = normalize_time(item.get("start_time"))
    return staff, start


def offered_services(items: Iterable[Mapping[str, Any]]) -> List[str]:
    services: set[str] = set()

    for item in items:
        raw = item.get("services_offered")
        if isinstance(raw, (list, set, tuple)):
            services.update(str(value).strip() for value in raw if value)
        elif raw:
            services.add(str(raw).strip())

        if item.get("service_name"):
            services.add(str(item["service_name"]).strip())

    return sorted(service for service in services if service)


def build_templates(
    items: Iterable[Mapping[str, Any]],
) -> Dict[int, List[List[Dict[str, Any]]]]:
    """
    Build one template group per staff/time combination for each weekday.

    Multiple rows at the same staff/time can represent different service
    choices. Keeping them together prevents synthetic double-booking.
    """
    grouped: Dict[
        int,
        Dict[Tuple[str, str], List[Dict[str, Any]]],
    ] = defaultdict(lambda: defaultdict(list))

    for item in items:
        if item.get("demo_seed"):
            continue

        item_date = parse_iso_date(item.get("date"))
        if item_date is None:
            continue
        if not (item.get("staff_id") or item.get("staff_name")):
            continue
        if not item.get("start_time"):
            continue

        key = template_group_key(item)
        grouped[item_date.weekday()][key].append(copy.deepcopy(dict(item)))

    return {
        weekday: list(groups.values())
        for weekday, groups in grouped.items()
        if groups
    }


def booking_count_for_day(
    weekday: int,
    available_groups: int,
    rng: random.Random,
) -> int:
    low, high = BOOKINGS_PER_DAY.get(weekday, (2, 4))
    return min(available_groups, rng.randint(low, high))


def booked_at_for(
    appointment_date: date,
    today: date,
    rng: random.Random,
) -> str:
    lead_days = rng.randint(2, 35)
    booked_date = appointment_date - timedelta(days=lead_days)

    # A future appointment should never have a booked_at timestamp in the
    # future relative to the day the seed is run.
    if booked_date > today:
        booked_date = today - timedelta(days=rng.randint(0, 2))

    booked_time = time(
        hour=rng.randint(8, 18),
        minute=rng.choice((0, 15, 30, 45)),
    )
    return datetime.combine(booked_date, booked_time).isoformat()


def make_demo_booking(
    template_group: List[Dict[str, Any]],
    appointment_date: date,
    today: date,
    seed: int,
    ordinal: int,
) -> Dict[str, Any]:
    representative = template_group[0]
    staff_key, start_key = template_group_key(representative)
    rng = deterministic_rng(
        seed,
        appointment_date.isoformat(),
        staff_key,
        start_key,
        ordinal,
    )

    item: Dict[str, Any] = {}
    for field in COPY_FIELDS:
        if field in representative:
            item[field] = copy.deepcopy(representative[field])

    services = offered_services(template_group)
    service_name = rng.choice(services) if services else "Spa Service"
    guest_name, guest_email, guest_phone = GUESTS[rng.randrange(len(GUESTS))]

    id_material = "|".join(
        (
            str(seed),
            appointment_date.isoformat(),
            staff_key,
            start_key,
            str(ordinal),
            guest_email,
        )
    )
    digest = hashlib.sha1(id_material.encode("utf-8")).hexdigest()[:14]

    item.update(
        {
            "slot_id": (
                f"demo_{appointment_date.strftime('%Y%m%d')}_"
                f"{slug(staff_key)[:16]}_{digest}"
            ),
            "date": appointment_date.isoformat(),
            "status": "BOOKED",
            "service_name": service_name,
            "booking_id": f"demo_bk_{digest}",
            "booked_at": booked_at_for(appointment_date, today, rng),
            "customer_name": guest_name,
            "customer_email": guest_email,
            "customer_phone": guest_phone,
            "demo_seed": True,
            "demo_seed_created": True,
            "demo_seed_version": SEED_VERSION,
            "demo_seeded_at": datetime.utcnow().isoformat(),
        }
    )

    if item.get("start_time"):
        item["date_start"] = (
            f"{appointment_date.isoformat()}#{item['start_time']}"
        )

    special_request = rng.choice(SPECIAL_REQUESTS)
    if special_request:
        item["special_requests"] = special_request

    # A modest cancellation rate gives the Trends view realistic data without
    # overwhelming the actual schedule.
    if rng.random() < 0.065:
        item["status"] = "CANCELLED"
        cancel_day = min(today, appointment_date) - timedelta(
            days=rng.randint(0, 3)
        )
        item["cancelled_at"] = datetime.combine(
            cancel_day,
            time(
                hour=rng.randint(9, 17),
                minute=rng.choice((0, 15, 30, 45)),
            ),
        ).isoformat()

    return item


def build_demo_rows(
    templates: Dict[int, List[List[Dict[str, Any]]]],
    start_date: date,
    end_date: date,
    today: date,
    seed: int,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    cursor = start_date

    while cursor <= end_date:
        day_groups = templates.get(cursor.weekday(), [])
        if day_groups:
            day_rng = deterministic_rng(seed, cursor.isoformat(), "day")
            count = booking_count_for_day(
                cursor.weekday(),
                len(day_groups),
                day_rng,
            )
            selected = day_rng.sample(day_groups, count)

            for ordinal, template_group in enumerate(selected):
                rows.append(
                    make_demo_booking(
                        template_group,
                        appointment_date=cursor,
                        today=today,
                        seed=seed,
                        ordinal=ordinal,
                    )
                )

        cursor += timedelta(days=1)

    return rows


def delete_demo_rows(table) -> int:
    demo_rows = scan_all(table, Attr("demo_seed").eq(True))

    for item in demo_rows:
        table.delete_item(Key={"slot_id": item["slot_id"]})

    return len(demo_rows)


def summarize(rows: Sequence[Mapping[str, Any]], today: date) -> Dict[str, int]:
    summary = {
        "total": len(rows),
        "completed": 0,
        "upcoming": 0,
        "cancelled": 0,
        "today": 0,
    }

    for row in rows:
        status = str(row.get("status") or "").upper()
        row_date = parse_iso_date(row.get("date"))

        if status == "CANCELLED":
            summary["cancelled"] += 1
        elif row_date and row_date < today:
            summary["completed"] += 1
        else:
            summary["upcoming"] += 1

        if row_date == today:
            summary["today"] += 1

    return summary


def main() -> int:
    args = parse_args()

    if not args.table.startswith("purezen_") and not args.allow_other_table:
        raise SystemExit(
            f"Refusing to touch table {args.table!r}. "
            "Use --allow-other-table only if that table is intentional."
        )

    if args.past_days < 0:
        raise SystemExit("--past-days must be 0 or greater")
    if args.future_days is not None and args.future_days < 1:
        raise SystemExit("--future-days must be at least 1")

    session = boto3.Session(region_name=args.region)
    identity = session.client("sts").get_caller_identity()
    table = session.resource("dynamodb").Table(args.table)

    print(f"AWS account: {identity.get('Account')}")
    print(f"Region: {args.region}")
    print(f"Table: {args.table}")

    if args.reset:
        deleted = delete_demo_rows(table)
        print(f"Reset complete. Deleted {deleted} demo rows.")
        return 0

    # Match the admin API, which currently classifies appointments by UTC date.
    today = datetime.utcnow().date()
    start_date = today - timedelta(days=args.past_days)

    if args.future_days is not None:
        end_date = today + timedelta(days=args.future_days)
        window_detail = f"{args.past_days} days history + {args.future_days} days future"
    else:
        end_date = parse_iso_date(args.through)
        if end_date is None:
            raise SystemExit("--through must be a valid YYYY-MM-DD date")
        if end_date < today:
            raise SystemExit(
                f"--through ({end_date.isoformat()}) is before today ({today.isoformat()})"
            )
        window_detail = (
            f"{args.past_days} days history + future appointments through "
            f"{end_date.isoformat()}"
        )

    all_rows = scan_all(table)
    real_rows = [row for row in all_rows if not row.get("demo_seed")]
    existing_demo_rows = [row for row in all_rows if row.get("demo_seed")]

    templates = build_templates(real_rows)
    if not templates:
        raise SystemExit(
            "No usable schedule templates were found. "
            "PureZen needs dated slots with staff and start_time fields first."
        )

    demo_rows = build_demo_rows(
        templates,
        start_date=start_date,
        end_date=end_date,
        today=today,
        seed=args.seed,
    )
    summary = summarize(demo_rows, today)

    print(
        f"Window: {start_date.isoformat()} through {end_date.isoformat()} "
        f"({window_detail})"
    )
    print(f"Existing non-demo rows preserved: {len(real_rows)}")
    print(f"Existing demo rows to refresh: {len(existing_demo_rows)}")
    print(
        "Planned demo appointments: "
        f"{summary['total']} total, "
        f"{summary['completed']} completed, "
        f"{summary['upcoming']} upcoming, "
        f"{summary['cancelled']} cancelled, "
        f"{summary['today']} today."
    )

    if not args.apply:
        print()
        print("DRY RUN ONLY. Nothing was written.")
        print("Run again with --apply when the preview looks right.")
        return 0

    deleted = delete_demo_rows(table)

    created = 0
    with table.batch_writer() as batch:
        for row in demo_rows:
            batch.put_item(Item=row)
            created += 1

    print()
    print(
        f"Seed complete. Refreshed {deleted} old demo rows "
        f"and created {created} rolling demo appointments."
    )
    print(
        "Real availability and real bookings were not modified. "
        "Use --reset to remove the synthetic portfolio dataset."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
