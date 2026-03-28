import os
from functools import lru_cache

import boto3


AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

SERVICES_TABLE_NAME = os.getenv("SERVICES_TABLE", "purezen_services")
AVAILABILITY_TABLE_NAME = os.getenv("AVAILABILITY_TABLE", "purezen_availability")


@lru_cache
def get_dynamodb_resource():
    return boto3.resource("dynamodb", region_name=AWS_REGION)


@lru_cache
def get_services_table():
    dynamodb = get_dynamodb_resource()
    return dynamodb.Table(SERVICES_TABLE_NAME)


@lru_cache
def get_availability_table():
    dynamodb = get_dynamodb_resource()
    return dynamodb.Table(AVAILABILITY_TABLE_NAME)
