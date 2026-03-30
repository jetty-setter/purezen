from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

import bcrypt
import boto3
from boto3.dynamodb.conditions import Attr
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr

from app.config import AWS_REGION

log = logging.getLogger(__name__)

USERS_TABLE = "purezen_users"

router = APIRouter()

dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
table = dynamodb.Table(USERS_TABLE)


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
    """Scan for a user by email. Users table is small so scan is fine."""
    response = table.scan(
        FilterExpression=Attr("email").eq(email.lower().strip())
    )
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

    # Check for existing account
    existing = _get_user_by_email(email)
    if existing:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    # Hash password
    hashed = bcrypt.hashpw(request.password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    user_id = f"usr_{uuid.uuid4().hex[:12]}"
    token   = uuid.uuid4().hex  # simple session token

    user = {
        "user_id":       user_id,
        "name":          request.name.strip(),
        "email":         email,
        "phone":         request.phone.strip(),
        "password_hash": hashed,
        "token":         token,
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

    # Rotate token on each login
    token = uuid.uuid4().hex
    table.update_item(
        Key={"user_id": user["user_id"]},
        UpdateExpression="SET #t = :t",
        ExpressionAttributeNames={"#t": "token"},
        ExpressionAttributeValues={":t": token},
    )

    log.info("User logged in: %s", email)

    return AuthResponse(
        success=True,
        message="Login successful.",
        token=token,
        user=_safe_user(user),
    )


@router.get("/auth/me")
def get_me(token: str) -> Dict[str, Any]:
    """Validate a session token and return the user."""
    response = table.scan(FilterExpression=Attr("token").eq(token))
    items = response.get("Items", [])

    if not items:
        raise HTTPException(status_code=401, detail="Invalid or expired session.")

    return _safe_user(items[0])
