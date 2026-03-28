from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
import boto3

from app.config import AWS_REGION, SERVICES_TABLE

log = logging.getLogger(__name__)

router = APIRouter()

dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
table = dynamodb.Table(SERVICES_TABLE)


def _convert_decimal(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, list):
        return [_convert_decimal(v) for v in value]
    if isinstance(value, dict):
        return {k: _convert_decimal(v) for k, v in value.items()}
    return value


def list_services(active_only: bool = True) -> List[Dict[str, Any]]:
    # Paginate fully — services table is small now but won't always be
    items: List[Dict[str, Any]] = []
    response = table.scan()
    items.extend(response.get("Items", []))

    while "LastEvaluatedKey" in response:
        response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        items.extend(response.get("Items", []))

    items = [_convert_decimal(item) for item in items]

    if active_only:
        items = [item for item in items if item.get("active", True) is not False]

    items.sort(key=lambda x: x.get("name", ""))
    return items


def get_service_by_name(service_name: str) -> Optional[Dict[str, Any]]:
    service_name_lower = (service_name or "").strip().lower()
    services = list_services(active_only=True)

    for service in services:
        if service.get("name", "").strip().lower() == service_name_lower:
            return service

    for service in services:
        if service_name_lower in service.get("name", "").strip().lower():
            return service

    return None


@router.get("/services")
def get_services() -> List[Dict[str, Any]]:
    return list_services(active_only=True)
