"""Shared DynamoDB and formatting utilities."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional


def convert_decimal(obj: Any) -> Any:
    """Recursively convert DynamoDB Decimal types to int/float."""
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    if isinstance(obj, list):
        return [convert_decimal(v) for v in obj]
    if isinstance(obj, dict):
        return {k: convert_decimal(v) for k, v in obj.items()}
    return obj


def scan_all(table, filter_expression=None) -> List[Dict[str, Any]]:
    """Paginated DynamoDB full table scan. Returns Decimal-converted items."""
    kwargs: Dict[str, Any] = {}
    if filter_expression is not None:
        kwargs["FilterExpression"] = filter_expression
    items: List[Dict[str, Any]] = []
    response = table.scan(**kwargs)
    items.extend(response.get("Items", []))
    while "LastEvaluatedKey" in response:
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
        response = table.scan(**kwargs)
        items.extend(response.get("Items", []))
    return [convert_decimal(item) for item in items]


def format_display_date(value: str) -> str:
    """Format a YYYY-MM-DD date string for human display (e.g. 'June 5, 2025')."""
    raw = (value or "").strip()
    if not raw:
        return "Unknown"
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%d")
        try:
            return parsed.strftime("%B %-d, %Y")
        except Exception:
            return parsed.strftime("%B %d, %Y").replace(" 0", " ")
    except Exception:
        return raw
