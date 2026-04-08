from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import boto3
from botocore.exceptions import ClientError
from app.config import AWS_REGION

log = logging.getLogger(__name__)
TABLE_NAME   = "purezen-chat-sessions"
MAX_MESSAGES = 40
dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
table    = dynamodb.Table(TABLE_NAME)

def _now(): return datetime.now(timezone.utc).isoformat()
def _truncate(msgs): return msgs[-MAX_MESSAGES:] if len(msgs) > MAX_MESSAGES else msgs

def load_history(session_id: str) -> List[Dict[str, Any]]:
    try:
        result = table.get_item(Key={"session_id": session_id}, ConsistentRead=True)
        return list(result.get("Item", {}).get("messages", []))
    except Exception as exc:
        log.warning("load_history error: %s", exc)
        return []

def append_exchange(session_id: str, user_message: str, assistant_response: str, user_email: Optional[str]=None) -> None:
    try:
        ts = _now()
        new_msgs = [{"role":"user","content":user_message,"ts":ts},{"role":"assistant","content":assistant_response,"ts":ts}]
        existing = load_history(session_id)
        merged   = _truncate(existing + new_msgs)
        if existing:
            table.update_item(
                Key={"session_id": session_id},
                UpdateExpression="SET messages = :m, last_updated = :lu" + (", user_email = :ue" if user_email else ""),
                ExpressionAttributeValues={":m": merged, ":lu": ts, **({":ue": user_email} if user_email else {})},
            )
        else:
            item = {"session_id": session_id, "messages": merged, "created_at": ts, "last_updated": ts}
            if user_email: item["user_email"] = user_email
            table.put_item(Item=item)
    except Exception as exc:
        log.warning("append_exchange error: %s", exc)

def format_history_for_llm(messages: List[Dict[str, Any]], max_turns: int=6) -> str:
    if not messages: return ""
    recent = messages[-(max_turns*2):]
    return "\n".join(f"{m.get('role','user').capitalize()}: {m.get('content','')}" for m in recent if m.get('content'))

def clear_session(session_id: str) -> None:
    try: table.delete_item(Key={"session_id": session_id})
    except Exception as exc: log.warning("clear_session error: %s", exc)
