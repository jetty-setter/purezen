from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Dict, List, Any

from app.dynamodb_client import get_availability_table, get_services_table


STAFF = [
    {"staff_id": "stf_001", "name": "Ava"},
    {"staff_id": "stf_002", "name": "Mia"},
    {"staff_id": "stf_003", "name": "Noah"},
    {"staff_id": "stf_004", "name": "Lena"},
    {"staff_id": "stf_005", "name": "Kai"},
]

# Saturday/Sunday: 9 AM–5 PM
WEEKEND_START_HOUR = 9
WEEKEND_END_HOUR = 17

# Monday–Friday: 9 AM–7 PM
WEEKDAY_START_HOUR = 9
WEEKDAY_END_HOUR = 19

DAYS_TO_SEED = 14


def _to_int(value: Any, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, Decimal):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(str(value))
    except Exception:
        return default


def _format_time_12h(hour: int, minute: int) -> str:
    dt = datetime.combine(date.today(), time(hour=hour, minute=minute))
    return dt.strftime("%I:%M %p").lstrip("0")


def _safe_scan_all(table) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    response = table.scan()
    items.extend(response.get("Items", []))

    while "LastEvaluatedKey" in response:
        response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        items.extend(response.get("Items", []))

    return items


def _load_services() -> List[Dict[str, Any]]:
    services_table = get_services_table()
    raw_services = _safe_scan_all(services_table)

    services: List[Dict[str, Any]] = []
    for svc in raw_services:
        service_id = svc.get("service_id") or svc.get("id")
        name = svc.get("name")
        duration = _to_int(svc.get("duration_minutes"), 60)

        if service_id and name:
            services.append(
                {
                    "service_id": str(service_id),
                    "name": str(name),
                    "duration_minutes": duration,
                }
            )

    return services


def _hours_for_day(day_value: date) -> tuple[int, int]:
    # Monday=0 ... Sunday=6
    if day_value.weekday() >= 5:
        return WEEKEND_START_HOUR, WEEKEND_END_HOUR
    return WEEKDAY_START_HOUR, WEEKDAY_END_HOUR


def _generate_start_times_for_duration(start_hour: int, end_hour: int, duration_minutes: int) -> List[str]:
    times: List[str] = []

    current = datetime.combine(date.today(), time(hour=start_hour, minute=0))
    end_boundary = datetime.combine(date.today(), time(hour=end_hour, minute=0))

    while current + timedelta(minutes=duration_minutes) <= end_boundary:
        times.append(current.strftime("%I:%M %p").lstrip("0"))
        current += timedelta(minutes=30)

    return times


def seed_demo_availability() -> None:
    availability_table = get_availability_table()
    services = _load_services()

    if not services:
        raise RuntimeError("No services found in the services table. Seed services first.")

    today = date.today()
    created = 0

    with availability_table.batch_writer(overwrite_by_pkeys=["slot_id"]) as batch:
        for day_offset in range(DAYS_TO_SEED):
            current_day = today + timedelta(days=day_offset)
            date_str = current_day.isoformat()
            start_hour, end_hour = _hours_for_day(current_day)

            for service in services:
                service_id = service["service_id"]
                service_name = service["name"]
                duration = service["duration_minutes"]

                start_times = _generate_start_times_for_duration(start_hour, end_hour, duration)

                for staff in STAFF:
                    staff_id = staff["staff_id"]
                    staff_name = staff["name"]

                    for start_time in start_times:
                        slot_id = (
                            f"slot_{date_str}_{start_time.replace(':', '').replace(' ', '').lower()}_"
                            f"{staff_id}_{service_id}"
                        )

                        item = {
                            "slot_id": slot_id,
                            "service_id": service_id,
                            "service_name": service_name,
                            "staff_id": staff_id,
                            "staff_name": staff_name,
                            "date": date_str,
                            "start_time": start_time,
                            "date_start": f"{date_str}#{start_time}",
                            "status": "AVAILABLE",
                            "duration_minutes": duration,
                        }

                        batch.put_item(Item=item)
                        created += 1

    print(f"Seeded {created} availability slots across {len(STAFF)} staff members for {DAYS_TO_SEED} days.")


if __name__ == "__main__":
    seed_demo_availability()
