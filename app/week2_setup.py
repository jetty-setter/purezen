#!/usr/bin/env python3
"""
week2_setup.py
Creates the purezen_admins DynamoDB table and a default admin account.

Run ONCE from your backend EC2:
    python3 week2_setup.py
"""

import boto3
import bcrypt
import uuid
import sys

AWS_REGION   = "us-east-1"
ADMINS_TABLE = "purezen_admins"

# Change these before running
DEFAULT_ADMIN_EMAIL    = "admin@purezen.com"
DEFAULT_ADMIN_PASSWORD = "PureZen2026!"
DEFAULT_ADMIN_NAME     = "PureZen Admin"


def create_admins_table():
    dynamodb = boto3.client("dynamodb", region_name=AWS_REGION)
    existing = dynamodb.list_tables().get("TableNames", [])

    if ADMINS_TABLE in existing:
        print(f"Table '{ADMINS_TABLE}' already exists. Skipping.")
        return

    print(f"Creating table '{ADMINS_TABLE}'...")
    dynamodb.create_table(
        TableName=ADMINS_TABLE,
        KeySchema=[{"AttributeName": "admin_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "admin_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    waiter = dynamodb.get_waiter("table_exists")
    waiter.wait(TableName=ADMINS_TABLE)
    print(f"Table '{ADMINS_TABLE}' created.")


def create_default_admin():
    dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
    table    = dynamodb.Table(ADMINS_TABLE)

    from boto3.dynamodb.conditions import Attr
    existing = table.scan(
        FilterExpression=Attr("email").eq(DEFAULT_ADMIN_EMAIL)
    ).get("Items", [])

    if existing:
        print(f"Admin account '{DEFAULT_ADMIN_EMAIL}' already exists. Skipping.")
        return

    hashed = bcrypt.hashpw(
        DEFAULT_ADMIN_PASSWORD.encode("utf-8"), bcrypt.gensalt()
    ).decode("utf-8")

    table.put_item(Item={
        "admin_id":      f"adm_{uuid.uuid4().hex[:12]}",
        "name":          DEFAULT_ADMIN_NAME,
        "email":         DEFAULT_ADMIN_EMAIL,
        "password_hash": hashed,
        "token":         "",
        "created_at":    __import__("datetime").datetime.utcnow().isoformat(),
    })

    print(f"Admin account created:")
    print(f"  Email:    {DEFAULT_ADMIN_EMAIL}")
    print(f"  Password: {DEFAULT_ADMIN_PASSWORD}")
    print("  ** Change this password after first login **")


if __name__ == "__main__":
    create_admins_table()
    create_default_admin()
    print("\nWeek 2 setup complete.")
    print("Next steps:")
    print("  1. Copy admin_routes.py to purezen-backend/app/")
    print("  2. Update main.py to include the admin router")
    print("  3. Upload admin.html to S3")
    print("  4. Restart backend: sudo systemctl restart purezen")
