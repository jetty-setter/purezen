#!/usr/bin/env python3
"""
week1_setup.py
Creates the purezen_users DynamoDB table and installs bcrypt.

Run this ONCE from your backend EC2:
    python3 week1_setup.py
"""

import boto3
import subprocess
import sys

AWS_REGION = "us-east-1"
USERS_TABLE = "purezen_users"


def install_bcrypt():
    print("Installing bcrypt...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "bcrypt"])
    print("bcrypt installed.")


def create_users_table():
    dynamodb = boto3.client("dynamodb", region_name=AWS_REGION)

    existing = dynamodb.list_tables().get("TableNames", [])
    if USERS_TABLE in existing:
        print(f"Table '{USERS_TABLE}' already exists. Skipping.")
        return

    print(f"Creating table '{USERS_TABLE}'...")
    dynamodb.create_table(
        TableName=USERS_TABLE,
        KeySchema=[
            {"AttributeName": "user_id", "KeyType": "HASH"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "user_id", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )

    waiter = dynamodb.get_waiter("table_exists")
    waiter.wait(TableName=USERS_TABLE)
    print(f"Table '{USERS_TABLE}' created and active.")


if __name__ == "__main__":
    install_bcrypt()
    create_users_table()
    print("\nWeek 1 setup complete.")
    print("Next steps:")
    print("  1. Copy users.py to your app/ directory on EC2")
    print("  2. Register the router in main.py")
    print("  3. Upload auth.html to your S3 bucket")
    print("  4. Restart your backend: sudo systemctl restart purezen")
