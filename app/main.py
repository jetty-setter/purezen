from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum
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

import os

# The deployed frontend origin (CloudFront URL) is injected by CDK at deploy
# time so CORS has a single source of truth without hardcoding it here.
ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
_frontend_origin = os.getenv("FRONTEND_ORIGIN")
if _frontend_origin:
    ALLOWED_ORIGINS.append(_frontend_origin)

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


@app.get("/health/llm")
def health_llm(x_diag_token: Optional[str] = Header(default=None)) -> dict:
    """Protected diagnostic: actually calls the LLM and surfaces the real error.

    Disabled by default. It only responds when the DIAG_TOKEN env var is set
    AND the request sends a matching `X-Diag-Token` header; otherwise it 404s
    (same as a nonexistent route) so it can't be used to probe key presence or
    trigger LLM calls publicly."""
    expected = os.getenv("DIAG_TOKEN")
    if not expected or x_diag_token != expected:
        raise HTTPException(status_code=404, detail="Not Found")

    from app.llm import call_ollama, LLM_MODEL
    try:
        reply = call_ollama("Reply with the single word: pong")
        return {
            "ok": True,
            "model": LLM_MODEL,
            "api_key_present": bool(os.getenv("ANTHROPIC_API_KEY")),
            "sample": reply,
        }
    except Exception as exc:
        return {
            "ok": False,
            "model": LLM_MODEL,
            "api_key_present": bool(os.getenv("ANTHROPIC_API_KEY")),
            "error": f"{type(exc).__name__}: {exc}",
        }


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


# Lambda handler (API Gateway proxy via Mangum). Ignored when run under uvicorn.
handler = Mangum(app, lifespan="off")
