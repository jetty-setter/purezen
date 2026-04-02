#!/usr/bin/env python3
"""
Emergency admin password reset — run directly on the backend EC2.

Usage:
  python3 reset_admin_password.py <email> <new_password>

Example:
  python3 reset_admin_password.py admin@purezen.com NewPassword123!
"""

import sys
import boto3
import bcrypt
from boto3.dynamodb.conditions import Attr

AWS_REGION   = "us-east-1"
ADMINS_TABLE = "purezen_admins"

def reset(email: str, new_password: str) -> None:
    if len(new_password) < 8:
        print("ERROR: Password must be at least 8 characters.")
        sys.exit(1)

    dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
    table    = dynamodb.Table(ADMINS_TABLE)

    resp  = table.scan(FilterExpression=Attr("email").eq(email.lower().strip()))
    items = resp.get("Items", [])

    if not items:
        print(f"ERROR: No admin found with email '{email}'.")
        sys.exit(1)

    admin    = items[0]
    admin_id = admin["admin_id"]
    name     = admin.get("name", "Unknown")

    pw_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()

    table.update_item(
        Key={"admin_id": admin_id},
        UpdateExpression="SET password_hash = :h, #t = :t, active = :a",
        ExpressionAttributeNames={"#t": "token"},
        ExpressionAttributeValues={":h": pw_hash, ":t": "", ":a": True},
    )

    print(f"✓ Password reset for {name} ({email})")
    print(f"  Admin ID : {admin_id}")
    print(f"  Account  : reactivated if it was deactivated")
    print(f"  Session  : invalidated (must log in again)")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python3 reset_admin_password.py <email> <new_password>")
        sys.exit(1)
    reset(sys.argv[1], sys.argv[2])
