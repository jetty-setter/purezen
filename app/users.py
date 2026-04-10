from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

import bcrypt
import boto3
from boto3.dynamodb.conditions import Attr, Key
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr

from app.config import AWS_REGION

log = logging.getLogger(__name__)

USERS_TABLE     = "purezen_users"
EMAIL_GSI_NAME  = "email-index"
TOKEN_GSI_NAME  = "token-index"
TOKEN_TTL_SECS  = 86400  # 24 hours

router = APIRouter()

dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
table    = dynamodb.Table(USERS_TABLE)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    name: str
    email: EmailStr
    phone: str
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class AuthResponse(BaseModel):
    success: bool
    message: str
    token: Optional[str] = None
    user: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    """
    Look up a user by email using the email-index GSI.
    Falls back to a full scan if the GSI does not exist yet
    (e.g. first run before the index is created).
    """
    email = email.lower().strip()
    try:
        response = table.query(
            IndexName=EMAIL_GSI_NAME,
            KeyConditionExpression=Key("email").eq(email),
            Limit=1,
        )
        items = response.get("Items", [])
        return items[0] if items else None
    except Exception as exc:
        log.warning("GSI query failed, falling back to scan: %s", exc)
        response = table.scan(FilterExpression=Attr("email").eq(email))
        items = response.get("Items", [])
        return items[0] if items else None


def _get_user_by_token(token: str) -> Optional[Dict[str, Any]]:
    """Look up a user by session token using token-index GSI. Falls back to scan."""
    if not token:
        return None
    try:
        response = table.query(
            IndexName="token-index",
            KeyConditionExpression=Key("token").eq(token),
            Limit=1,
        )
        items = response.get("Items", [])
        return items[0] if items else None
    except Exception as exc:
        log.warning("token-index GSI query failed, falling back to scan: %s", exc)
        response = table.scan(FilterExpression=Attr("token").eq(token))
        items = response.get("Items", [])
        return items[0] if items else None


def _safe_user(user: Dict[str, Any]) -> Dict[str, Any]:
    """Return user dict without the password hash."""
    return {
        "user_id":    user.get("user_id"),
        "name":       user.get("name"),
        "email":      user.get("email"),
        "phone":      user.get("phone"),
        "created_at": user.get("created_at"),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/auth/register", response_model=AuthResponse)
def register(request: RegisterRequest) -> AuthResponse:
    email = request.email.lower().strip()

    existing = _get_user_by_email(email)
    if existing:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    hashed  = bcrypt.hashpw(request.password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    user_id = f"usr_{uuid.uuid4().hex[:12]}"
    token   = uuid.uuid4().hex

    user = {
        "user_id":       user_id,
        "name":          request.name.strip(),
        "email":         email,
        "phone":         request.phone.strip(),
        "password_hash": hashed,
        "token":         token,
        "token_expires_at": int(time.time()) + TOKEN_TTL_SECS,
        "created_at":    datetime.utcnow().isoformat(),
    }

    table.put_item(Item=user)
    log.info("Registered new user: %s", email)

    return AuthResponse(
        success=True,
        message="Account created successfully.",
        token=token,
        user=_safe_user(user),
    )


@router.post("/auth/login", response_model=AuthResponse)
def login(request: LoginRequest) -> AuthResponse:
    email = request.email.lower().strip()
    user  = _get_user_by_email(email)

    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    password_match = bcrypt.checkpw(
        request.password.encode("utf-8"),
        user["password_hash"].encode("utf-8"),
    )

    if not password_match:
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    token   = uuid.uuid4().hex
    expires = int(time.time()) + TOKEN_TTL_SECS
    table.update_item(
        Key={"user_id": user["user_id"]},
        UpdateExpression="SET #t = :t, token_expires_at = :e",
        ExpressionAttributeNames={"#t": "token"},
        ExpressionAttributeValues={":t": token, ":e": expires},
    )

    log.info("User logged in: %s", email)

    return AuthResponse(
        success=True,
        message="Login successful.",
        token=token,
        user=_safe_user(user),
    )


@router.post("/auth/logout")
def logout(token: str) -> Dict[str, Any]:
    """Invalidate a customer session token in DynamoDB."""
    user = _get_user_by_token(token)
    if user:
        table.update_item(
            Key={"user_id": user["user_id"]},
            UpdateExpression="SET #t = :t, token_expires_at = :e",
            ExpressionAttributeNames={"#t": "token"},
            ExpressionAttributeValues={":t": "", ":e": 0},
        )
        log.info("User logged out: %s", user.get("email"))
    return {"success": True, "message": "Logged out."}


@router.get("/auth/me")
def get_me(token: str) -> Dict[str, Any]:
    """Validate a session token and return the user."""
    user = _get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired session.")
    return _safe_user(user)
