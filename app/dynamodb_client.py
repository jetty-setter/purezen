from functools import lru_cache

import boto3

from app.config import AWS_REGION, SERVICES_TABLE as SERVICES_TABLE_NAME, AVAILABILITY_TABLE as AVAILABILITY_TABLE_NAME


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
