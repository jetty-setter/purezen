from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.orchestrator import handle_chat

try:
    from app.services import router as services_router
except Exception:
    services_router = None

try:
    from app.users import router as users_router
except Exception:
    users_router = None

try:
    from app.booking_history import router as history_router
except Exception:
    history_router = None

try:
    from app.admin_routes import router as admin_router
except Exception:
    admin_router = None

app = FastAPI(title="PureZen API")

ALLOWED_ORIGINS = [
    "http://purezen-350578858687-us-east-1-an.s3-website-us-east-1.amazonaws.com",
    "http://pzalb-1969659663.us-east-1.elb.amazonaws.com",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    context: Optional[Dict[str, Any]] = None


class ChatResponse(BaseModel):
    session_id: str
    response: str


@app.get("/")
def root() -> dict:
    return {"status": "ok", "service": "PureZen API"}


@app.get("/health")
def health() -> dict:
    return {"status": "healthy"}


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "healthy"}


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    session_id = request.session_id or str(__import__("uuid").uuid4())

    result = handle_chat(
        request.message,
        session_id=session_id,
        context=request.context or {},
    )

    resolved_session_id = result.get("session_id") or session_id
    response_text = result.get("response") or "I'm sorry, something went wrong."

    return ChatResponse(session_id=resolved_session_id, response=response_text)


if services_router:
    app.include_router(services_router)

if users_router:
    app.include_router(users_router)

if history_router:
    app.include_router(history_router)

if admin_router:
    app.include_router(admin_router)
