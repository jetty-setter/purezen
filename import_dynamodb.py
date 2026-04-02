import json
import os
import boto3
from decimal import Decimal

REGION = os.getenv("AWS_REGION", "us-east-1")
DATA_DIR = "data/seed"

TABLE_MAP = {
    "purezen-chat-sessions.json": "purezen-chat-sessions",
    "purezen_admins.json": "purezen_admins",
    "purezen_availability.json": "purezen_availability",
    "purezen_bookings.json": "purezen_bookings",
    "purezen_customers.json": "purezen_customers",
    "purezen_policies.json": "purezen_policies",
    "purezen_services.json": "purezen_services",
    "purezen_staff.json": "purezen_staff",
    "purezen_users.json": "purezen_users",
}

dynamodb = boto3.resource("dynamodb", region_name=REGION)

def to_decimal(obj):
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, list):
        return [to_decimal(x) for x in obj]
    if isinstance(obj, dict):
        return {k: to_decimal(v) for k, v in obj.items()}
    return obj

for filename, table_name in TABLE_MAP.items():
    path = os.path.join(DATA_DIR, filename)

    if not os.path.exists(path):
        print(f"Skipping {filename}: file not found")
        continue

    with open(path, "r") as f:
        items = json.load(f)

    table = dynamodb.Table(table_name)

    with table.batch_writer() as batch:
        for item in items:
            batch.put_item(Item=to_decimal(item))

    print(f"Imported {table_name}: {len(items)} items")
