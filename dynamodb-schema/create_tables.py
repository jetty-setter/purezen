import json
import boto3
import os

REGION = "us-east-1"
SCHEMA_DIR = "dynamodb-schema"

dynamodb = boto3.client("dynamodb", region_name=REGION)

def table_exists(table_name):
    try:
        dynamodb.describe_table(TableName=table_name)
        return True
    except dynamodb.exceptions.ResourceNotFoundException:
        return False

for filename in os.listdir(SCHEMA_DIR):
    if not filename.endswith(".json"):
        continue

    path = os.path.join(SCHEMA_DIR, filename)

    with open(path, "r") as f:
        data = json.load(f)

    table = data["Table"]
    table_name = table["TableName"]

    if table_exists(table_name):
        print(f"Skipping {table_name} (already exists)")
        continue

    params = {
        "TableName": table_name,
        "AttributeDefinitions": table["AttributeDefinitions"],
        "KeySchema": table["KeySchema"],
        "BillingMode": "PAY_PER_REQUEST"
    }

    if "GlobalSecondaryIndexes" in table:
        params["GlobalSecondaryIndexes"] = [
            {
                "IndexName": gsi["IndexName"],
                "KeySchema": gsi["KeySchema"],
                "Projection": gsi["Projection"]
            }
            for gsi in table["GlobalSecondaryIndexes"]
        ]

    print(f"Creating {table_name}...")
    dynamodb.create_table(**params)

print("Done.")
