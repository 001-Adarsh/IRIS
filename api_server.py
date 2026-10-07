"""Public FastAPI gateway for IRIS.

Run with ``uvicorn api_server:app`` after installing ``requirements.txt``.
Set IRIS_ADMIN_KEY and configure IRIS_CORS_ORIGINS before public deployment.
Use IRIS_RATE_LIMIT_STORAGE_URI for shared limits across multiple workers, and
trust forwarded client IP headers only from the known reverse proxy.
The public execution endpoint requires a local Docker daemon and a preloaded
IRIS_SANDBOX_IMAGE; it fails closed when that sandbox is unavailable.
"""

import base64
import binascii
import json
import hashlib
import hmac
import logging
import os
import re
import secrets
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterator, Literal

import requests
from dotenv import load_dotenv
from fastapi import (
    BackgroundTasks,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

load_dotenv(Path(__file__).resolve().parent / ".env")

from core.chat_persistence import (
    ChatLimitReached,
    ChatPersistence,
    OTPResendTooSoon,
)
from core.executor import IRISExecutor
from core.live_connections import DuplicateConnectionError, LiveConnectionStore
from core.router import provider_response_failed
from core.synthesizer import AISynthesizer
from tools.manager import ToolManager
from tools.web_tool import search_web

logger = logging.getLogger("iris.api")

MAX_CODE_LENGTH = 20_000
MAX_CAPTURE_BYTES = 256_000
MAX_IMAGE_BYTES = 5 * 1024 * 1024
CHAT_RETENTION_SECONDS = 10 * 24 * 60 * 60
CHAT_TOKEN_TTL_SECONDS = CHAT_RETENTION_SECONDS
SANDBOX_TIMEOUT_SECONDS = 10
SANDBOX_IMAGE = os.getenv("IRIS_SANDBOX_IMAGE", "python:3.12-alpine")

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=os.getenv("IRIS_RATE_LIMIT_STORAGE_URI", "memory://"),
)
public_rate_limit = limiter.shared_limit("20/minute", scope="public-api")
app = FastAPI(title="IRIS API", version="1.0.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.middleware("http")
async def disable_private_message_caching(request: Request, call_next: Any) -> Response:
    response = await call_next(request)
    if request.url.path.startswith(("/v1/admin", "/v1/connect", "/auth", "/api")):
        response.headers["Cache-Control"] = "no-store"
    return response


app.mount(
    "/assets",
    StaticFiles(directory=Path(__file__).resolve().parent / "assets"),
    name="assets",
)

cors_origins = [
    origin.strip()
    for origin in os.getenv(
        "IRIS_CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000",
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-IRIS-ADMIN-KEY"],
)

synthesizer = AISynthesizer()
chat_store = ChatPersistence(
    Path(os.getenv("IRIS_DATA_DIR", Path(__file__).resolve().parent / "data"))
)


def _owner_profile() -> dict[str, Any]:
    profile_path = Path(__file__).resolve().parent / "data" / "owner_profile.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    if not isinstance(profile, dict) or not isinstance(profile.get("name"), str):
        raise ValueError("The configured IRIS owner profile must include a name.")
    return profile


def _identity_reply(prompt: str) -> str | None:
    normalized_prompt = re.sub(r"\s+", " ", prompt.casefold()).strip()
    owner_name = _owner_profile()["name"]
    asks_about_iris = re.search(
        r"\bwho are you\b|\bwhat are you\b|\bwho is iris\b|"
        r"\bintroduce yourself\b|\btell me about (?:yourself|you)\b|"
        r"\btell me more about you\b",
        normalized_prompt,
    )
    if asks_about_iris:
        return (
            f"I'm IRIS, an AI assistant created by {owner_name}. "
            "I can help with questions, writing, research, planning, and "
            "software-development tasks. I don't have personal experiences, "
            "and I'll be clear when I can't verify something."
        )

    creator_question = re.search(
        r"\bwho\s+(?:created|made|built|developed)\s+(?:you|iris)\b|"
        r"\bwho\s+(?:is|was)\s+(?:your|iris'?s)\s+"
        r"(?:creator|founder|developer)\b|"
        r"\b(?:your|iris'?s)\s+(?:creator|founder|developer)\b",
        normalized_prompt,
    )
    if creator_question:
        return f"IRIS was created by {owner_name}."

    asks_about_owner = re.search(
        r"\b(?:do you know|who is|tell me about)\b", normalized_prompt
    ) and re.search(r"\badarsh(?:\s+dwivedi)?\b", normalized_prompt)
    if asks_about_owner:
        return (
            f"Yes. {owner_name} is the creator of IRIS. "
            "I know about him from the profile configured for this assistant, "
            "not from personal experience."
        )

    return None


class ChatTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2_000)


class PromptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=8_000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=2_000)

    @field_validator("prompt")
    @classmethod
    def prompt_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("prompt must not be blank")
        return value


def _validate_image_data(value: str | None) -> str | None:
    if value is None:
        return None
    match = re.fullmatch(
        r"data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/]*={0,2})",
        value,
    )
    if match is None:
        raise ValueError("image_data must be a PNG, JPEG, or WebP data URL")
    try:
        decoded = base64.b64decode(match.group(2), validate=True)
    except binascii.Error as exc:
        raise ValueError("image_data must contain valid base64 data") from exc
    if not decoded or len(decoded) > MAX_IMAGE_BYTES:
        raise ValueError("image_data must be smaller than 5 MB")
    return value


class EmailOTPRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().casefold()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("Enter a valid email address.")
        return value


class VerifyOTPRequest(EmailOTPRequest):
    code: str = Field(pattern=r"^\d{6}$")


class ChatPromptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=8_000)
    image_data: str | None = Field(default=None, max_length=7_000_000)

    @field_validator("prompt")
    @classmethod
    def prompt_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("prompt must not be blank")
        return value

    @field_validator("image_data")
    @classmethod
    def validate_image(cls, value: str | None) -> str | None:
        return _validate_image_data(value)


class CodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=MAX_CODE_LENGTH)

    @field_validator("code")
    @classmethod
    def code_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("code must not be blank")
        return value


class AdminExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: dict[str, Any]


class ConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(default="", max_length=80)
    message: str = Field(min_length=1, max_length=2_000)

    @field_validator("display_name", "message")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        if not value:
            raise ValueError("message must not be blank")
        return value


class ConnectionMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=2_000)

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message must not be blank")
        return value


class AdminLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, max_length=256)


class ConnectionDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["approve", "decline", "close"]


connection_store = LiveConnectionStore(
    Path(os.getenv("IRIS_DATA_DIR", Path(__file__).resolve().parent / "data"))
)


def _sse_event(name: str, payload: dict[str, Any]) -> str:
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=True)}\n\n"


def _capture_pipe(pipe: Any, output: list[bytes], truncated: list[bool]) -> None:
    captured = 0
    while chunk := pipe.read(8192):
        remaining = MAX_CAPTURE_BYTES - captured
        if remaining > 0:
            output.append(chunk[:remaining])
            captured += min(len(chunk), remaining)
        if len(chunk) > remaining:
            truncated[0] = True


def _sandbox_command(sandbox_dir: Path, container_name: str) -> list[str]:
    return [
        "docker",
        "run",
        "--rm",
        "--pull=never",
        "--name",
        container_name,
        "--network=none",
        "--memory=128m",
        "--cpus=0.5",
        "--pids-limit=32",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--user=65534:65534",
        "--mount",
        f"type=bind,source={sandbox_dir},target=/workspace,readonly",
        "--workdir=/workspace",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=16m,mode=1777",
        "--entrypoint",
        "python",
        SANDBOX_IMAGE,
        "-I",
        "-B",
        "/workspace/main.py",
    ]


def _stop_sandbox(container_name: str) -> None:
    for command in (
        ["docker", "stop", "--time", "1", container_name],
        ["docker", "rm", "--force", container_name],
    ):
        try:
            subprocess.run(
                command,
                capture_output=True,
                check=False,
                timeout=3,
            )
        except (OSError, subprocess.TimeoutExpired):
            logger.warning("Could not clean up sandbox container %s", container_name)


def _run_in_sandbox(code: str) -> dict[str, Any]:
    container_name = f"iris-sandbox-{uuid.uuid4().hex}"
    process: subprocess.Popen[bytes] | None = None

    with tempfile.TemporaryDirectory(prefix="iris-sandbox-") as temp_dir:
        code_path = Path(temp_dir) / "main.py"
        code_path.write_text(code, encoding="utf-8")

        try:
            process = subprocess.Popen(
                _sandbox_command(Path(temp_dir), container_name),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=503,
                detail="The isolated code-execution sandbox is unavailable.",
            ) from exc
        except OSError as exc:
            logger.exception("Could not start isolated code sandbox")
            raise HTTPException(
                status_code=503,
                detail="The isolated code-execution sandbox could not be started.",
            ) from exc

        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        stdout_truncated = [False]
        stderr_truncated = [False]
        readers = [
            threading.Thread(
                target=_capture_pipe,
                args=(process.stdout, stdout_chunks, stdout_truncated),
                daemon=True,
            ),
            threading.Thread(
                target=_capture_pipe,
                args=(process.stderr, stderr_chunks, stderr_truncated),
                daemon=True,
            ),
        ]
        for reader in readers:
            reader.start()

        timed_out = False
        try:
            process.wait(timeout=SANDBOX_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            process.wait()
        finally:
            _stop_sandbox(container_name)
            for reader in readers:
                reader.join(timeout=2)

        if timed_out:
            raise HTTPException(
                status_code=504,
                detail="Code execution exceeded the time limit.",
            )

        stdout = b"".join(stdout_chunks).decode("utf-8", errors="replace")
        stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace")
        if process.returncode == 125:
            logger.error("Docker could not start sandbox: %s", stderr[:500])
            raise HTTPException(
                status_code=503,
                detail=(
                    "The isolated code-execution sandbox could not start. "
                    "Check that Docker is running and the configured image is available."
                ),
            )
        return {
            "exit_code": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "output_truncated": stdout_truncated[0] or stderr_truncated[0],
        }


def _admin_key_or_raise(supplied_key: str | None) -> str:
    if not os.getenv("IRIS_ADMIN_KEY"):
        raise HTTPException(
            status_code=503,
            detail="Admin tool access is not configured on this server.",
        )
    if not IRISExecutor.is_valid_admin_key(supplied_key):
        raise HTTPException(status_code=403, detail="Admin authorization required.")
    return supplied_key


def _admin_password_or_raise() -> str:
    password = os.getenv("IRIS_ADMIN_PASSWORD", "")
    if len(password) < 16:
        raise HTTPException(
            status_code=503,
            detail=(
                "The owner messaging panel is not configured. "
                "Set a strong IRIS_ADMIN_PASSWORD on the server."
            ),
        )
    return password


def _session_signature(value: str, purpose: str) -> str:
    password = _admin_password_or_raise().encode("utf-8")
    key = hmac.new(
        password,
        f"iris-session:{purpose}:v1".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()


def _auth_secret_or_raise() -> bytes:
    secret = os.getenv("IRIS_AUTH_SECRET", "").strip()
    if len(secret) < 32:
        raise HTTPException(
            status_code=503,
            detail="Email sign-in is not configured on this server.",
        )
    return secret.encode("utf-8")


def _otp_digest(email: str, code: str) -> str:
    return hmac.new(
        _auth_secret_or_raise(),
        f"iris-email-otp:v1:{email}:{code}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _issue_chat_token(email: str) -> str:
    payload = json.dumps(
        {
            "sub": email,
            "exp": int(time.time()) + CHAT_TOKEN_TTL_SECONDS,
            "jti": secrets.token_urlsafe(16),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
    signature = hmac.new(
        _auth_secret_or_raise(),
        f"iris-chat-token:v1:{encoded}".encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return f"{encoded}.{signature}"


def _authenticated_email(authorization: str | None) -> str:
    scheme, separator, token = (authorization or "").partition(" ")
    if not separator or scheme.casefold() != "bearer":
        raise HTTPException(status_code=401, detail="Sign in to access saved chats.")
    try:
        encoded, signature = token.split(".", 1)
        expected = hmac.new(
            _auth_secret_or_raise(),
            f"iris-chat-token:v1:{encoded}".encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("invalid token signature")
        payload = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        claims = json.loads(payload)
        email = claims["sub"]
        if (
            not isinstance(email, str)
            or claims["exp"] <= int(time.time())
            or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)
        ):
            raise ValueError("invalid token claims")
        return email
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=401, detail="Your sign-in has expired.") from exc


def _secure_cookie(request: Request) -> bool:
    return request.url.scheme == "https" or os.getenv("RENDER", "").lower() == "true"


def _send_owner_email_notification(display_name: str, message: str) -> None:
    api_key = os.getenv("SENDGRID_API_KEY", "").strip()
    recipient = os.getenv("IRIS_NOTIFICATION_EMAIL", "").strip()
    sender = os.getenv("IRIS_FROM_EMAIL", "").strip()
    if not api_key or not recipient or not sender:
        logger.warning(
            "Owner email notification not sent: configure "
            "SENDGRID_API_KEY, IRIS_NOTIFICATION_EMAIL, and IRIS_FROM_EMAIL."
        )
        return

    try:
        result = requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "personalizations": [
                    {
                        "to": [{"email": recipient}],
                        "subject": "New message for you on IRIS",
                    }
                ],
                "from": {"email": sender, "name": "IRIS"},
                "content": [
                    {
                        "type": "text/plain",
                        "value": (
                            f"{display_name} sent you a private message on IRIS:\n\n"
                            f"{message}\n\n"
                            "Open your owner inbox: "
                            "https://iris-nub2.onrender.com/admin"
                        ),
                    }
                ],
            },
            timeout=8,
        )
    except requests.RequestException:
        logger.exception("Could not send the IRIS owner email notification.")
        return

    if not 200 <= result.status_code < 300:
        logger.error(
            "IRIS owner email notification failed with SendGrid status %s: %s",
            result.status_code,
            result.text[:500],
        )


def _visitor_id(request: Request, *, allow_missing: bool = False) -> str | None:
    token = request.cookies.get("iris_visitor") or request.cookies.get(
        "iris_visitor_history"
    )
    if token is None and allow_missing:
        return None
    if token is None:
        raise HTTPException(status_code=401, detail="Visitor session is required.")
    visitor_id, separator, signature = token.partition(".")
    if (
        not separator
        or len(visitor_id) < 24
        or len(signature) != 64
        or not hmac.compare_digest(
            signature.encode("utf-8"),
            _session_signature(visitor_id, "visitor").encode("ascii"),
        )
    ):
        raise HTTPException(status_code=401, detail="Visitor session is invalid.")
    return visitor_id


def _require_visitor_id(request: Request) -> str:
    visitor_id = _visitor_id(request)
    if visitor_id is None:
        raise HTTPException(status_code=401, detail="Visitor session is required.")
    return visitor_id


def _set_visitor_history_cookie(
    request: Request,
    response: Response,
    visitor_id: str,
) -> None:
    response.set_cookie(
        "iris_visitor_history",
        f"{visitor_id}.{_session_signature(visitor_id, 'visitor')}",
        max_age=365 * 24 * 60 * 60,
        httponly=True,
        secure=_secure_cookie(request),
        samesite="strict",
        path="/api/adarsh-chat",
    )


def _require_admin(request: Request) -> None:
    token = request.cookies.get("iris_admin")
    if token is None:
        raise HTTPException(status_code=401, detail="Admin sign-in is required.")
    expiry_text, separator, signature = token.partition(".")
    try:
        expiry = int(expiry_text)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Admin session is invalid.") from exc
    if (
        not separator
        or expiry <= int(time.time())
        or len(signature) != 64
        or not hmac.compare_digest(
            signature.encode("utf-8"),
            _session_signature(expiry_text, "admin").encode("ascii"),
        )
    ):
        raise HTTPException(status_code=401, detail="Admin session is invalid.")


@app.get("/admin", response_class=HTMLResponse)
def admin_panel() -> FileResponse:
    return FileResponse(
        Path(__file__).resolve().parent / "assets" / "admin.html",
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


@app.post("/v1/admin/session")
@limiter.limit("5/minute")
def create_admin_session(
    request: Request,
    body: AdminLoginRequest,
    response: Response,
) -> dict[str, str]:
    password = _admin_password_or_raise()
    if not hmac.compare_digest(
        body.password.encode("utf-8"),
        password.encode("utf-8"),
    ):
        raise HTTPException(status_code=403, detail="Admin password is incorrect.")
    expires_at = str(int(time.time()) + 12 * 60 * 60)
    response.set_cookie(
        "iris_admin",
        f"{expires_at}.{_session_signature(expires_at, 'admin')}",
        max_age=12 * 60 * 60,
        httponly=True,
        secure=_secure_cookie(request),
        samesite="strict",
        path="/v1/admin",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"status": "ok"}


@app.delete("/v1/admin/session")
def delete_admin_session(request: Request) -> Response:
    _require_admin(request)
    connection_store.update_owner_presence(0)
    response = Response(status_code=204, headers={"Cache-Control": "no-store"})
    response.delete_cookie(
        "iris_admin",
        httponly=True,
        secure=_secure_cookie(request),
        samesite="strict",
        path="/v1/admin",
    )
    return response


@app.post("/v1/admin/presence")
@limiter.limit("60/minute")
def update_admin_presence(request: Request) -> dict[str, str]:
    _require_admin(request)
    connection_store.update_owner_presence(int(time.time()))
    return {"status": "online"}


@app.get("/v1/presence")
@limiter.limit("60/minute")
def public_owner_presence(request: Request) -> dict[str, bool]:
    return {"online": connection_store.owner_is_online(int(time.time()))}


@app.get("/v1/admin/connections")
@limiter.limit("120/minute")
def admin_list_connections(
    request: Request,
    status: Literal["pending", "approved", "declined", "closed"] | None = None,
) -> list[dict[str, Any]]:
    _require_admin(request)
    return connection_store.list_connections(status)


@app.post("/v1/admin/connections/{connection_id}/decision")
@limiter.limit("60/minute")
def admin_decide_connection(
    request: Request,
    connection_id: int,
    body: ConnectionDecisionRequest,
) -> dict[str, Any]:
    _require_admin(request)
    existing = connection_store.get_connection(connection_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Connection request not found.")
    if body.action == "approve":
        if existing["status"] != "pending":
            raise HTTPException(
                status_code=409,
                detail="Only pending requests can be approved.",
            )
        status = "approved"
    elif body.action == "decline":
        if existing["status"] != "pending":
            raise HTTPException(
                status_code=409,
                detail="Only pending requests can be declined.",
            )
        status = "declined"
    else:
        if existing["status"] != "approved":
            raise HTTPException(
                status_code=409,
                detail="Only approved conversations can be closed.",
            )
        status = "closed"
    updated = connection_store.set_status(
        connection_id,
        status,
        expected_status=existing["status"],
    )
    if updated is None:
        raise HTTPException(
            status_code=409,
            detail="This connection changed before the action could be applied.",
        )
    return updated


@app.get("/v1/admin/connections/{connection_id}/messages")
@limiter.limit("120/minute")
def admin_list_messages(
    request: Request,
    connection_id: int,
    after_id: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    _require_admin(request)
    if connection_store.get_connection(connection_id) is None:
        raise HTTPException(status_code=404, detail="Connection request not found.")
    return connection_store.list_messages(connection_id, after_id, limit=None)


@app.post("/v1/admin/connections/{connection_id}/messages")
@limiter.limit("60/minute")
def admin_send_message(
    request: Request,
    connection_id: int,
    body: ConnectionMessageRequest,
) -> dict[str, Any]:
    _require_admin(request)
    connection = connection_store.get_connection(connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="Connection request not found.")
    if connection["status"] != "approved":
        raise HTTPException(
            status_code=409,
            detail="Messages can only be sent to approved conversations.",
        )
    message = connection_store.add_message(connection_id, "admin", body.message)
    if message is None:
        raise HTTPException(
            status_code=409,
            detail="Messages can only be sent to approved conversations.",
        )
    return message


@app.delete("/v1/admin/connections/{connection_id}")
@limiter.limit("30/minute")
def admin_delete_connection(request: Request, connection_id: int) -> Response:
    _require_admin(request)
    if not connection_store.delete_connection(connection_id):
        raise HTTPException(status_code=404, detail="Connection request not found.")
    return Response(status_code=204)


@app.get("/v1/connect/status")
@limiter.limit("30/minute")
def visitor_connection_status(
    request: Request,
    response: Response,
) -> dict[str, Any]:
    visitor_id = _visitor_id(request, allow_missing=True)
    if visitor_id is None:
        return {"status": "not_requested", "messages": []}
    _set_visitor_history_cookie(request, response, visitor_id)
    connection = connection_store.visitor_connection(visitor_id)
    if connection is None:
        return {
            "status": "not_requested",
            "messages": [],
            "session_id": visitor_id,
        }
    messages = connection_store.list_messages(connection["id"])
    return {
        key: value
        for key, value in {
            **connection,
            "messages": messages,
            "session_id": visitor_id,
        }.items()
        if key != "visitor_id"
    }


@app.post("/v1/connect/requests")
@limiter.limit("5/hour")
def create_connection_request(
    request: Request,
    body: ConnectionRequest,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    visitor_id = _visitor_id(request, allow_missing=True) or secrets.token_urlsafe(32)
    signature = _session_signature(visitor_id, "visitor")
    display_name = body.display_name or "Visitor"
    try:
        connection = connection_store.create_request(
            visitor_id,
            display_name,
            body.message,
        )
    except DuplicateConnectionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    background_tasks.add_task(
        _send_owner_email_notification,
        display_name,
        body.message,
    )
    response = JSONResponse(
        {
            **{key: value for key, value in connection.items() if key != "visitor_id"},
            "session_id": visitor_id,
        },
        headers={"Cache-Control": "no-store"},
    )
    response.set_cookie(
        "iris_visitor",
        f"{visitor_id}.{signature}",
        max_age=365 * 24 * 60 * 60,
        httponly=True,
        secure=_secure_cookie(request),
        samesite="strict",
        path="/v1/connect",
    )
    _set_visitor_history_cookie(request, response, visitor_id)
    return response


@app.get("/v1/connect/messages")
@limiter.limit("30/minute")
def visitor_list_messages(
    request: Request,
    after_id: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    visitor_id = _require_visitor_id(request)
    connection = connection_store.visitor_connection(visitor_id)
    if connection is None:
        return []
    return connection_store.list_messages(connection["id"], after_id)


@app.get("/api/adarsh-chat/history")
@limiter.limit("30/minute")
def adarsh_chat_history(
    request: Request,
    session_id: str | None = Query(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
    ),
    after_id: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    visitor_id = _require_visitor_id(request)
    if session_id is not None and not hmac.compare_digest(session_id, visitor_id):
        raise HTTPException(status_code=403, detail="This chat session is not yours.")
    connection = connection_store.visitor_connection(visitor_id)
    if connection is None:
        return []
    return [
        {**message, "timestamp": message["created_at"]}
        for message in connection_store.list_messages(
            connection["id"],
            after_id,
            limit=None,
        )
    ]


@app.post("/v1/connect/messages")
@limiter.limit("30/minute")
def visitor_send_message(
    request: Request,
    body: ConnectionMessageRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    visitor_id = _require_visitor_id(request)
    connection = connection_store.visitor_connection(visitor_id)
    if connection is None or connection["status"] != "approved":
        raise HTTPException(
            status_code=403,
            detail="An approved owner conversation is required.",
        )
    message = connection_store.add_message(
        connection["id"],
        "visitor",
        body.message,
    )
    if message is None:
        raise HTTPException(
            status_code=409,
            detail="An approved owner conversation is required.",
        )
    background_tasks.add_task(
        _send_owner_email_notification,
        connection["display_name"],
        body.message,
    )
    return message


@app.post("/auth/send-otp")
@limiter.limit("5/hour")
def send_email_otp(request: Request, body: EmailOTPRequest) -> dict[str, str]:
    api_key = os.getenv("SENDGRID_API_KEY", "").strip()
    sender = os.getenv("IRIS_FROM_EMAIL", "").strip()
    if not api_key or not sender:
        raise HTTPException(
            status_code=503,
            detail="Email sign-in is not configured on this server.",
        )

    code = f"{secrets.randbelow(1_000_000):06d}"
    now = int(time.time())
    try:
        chat_store.issue_otp(body.email, _otp_digest(body.email, code), now)
    except OTPResendTooSoon as exc:
        raise HTTPException(
            status_code=429,
            detail="Please wait a minute before requesting another code.",
        ) from exc

    try:
        response = requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "personalizations": [
                    {
                        "to": [{"email": body.email}],
                        "subject": "Your IRIS sign-in code",
                    }
                ],
                "from": {"email": sender, "name": "IRIS"},
                "content": [
                    {
                        "type": "text/plain",
                        "value": (
                            f"Your IRIS sign-in code is {code}.\n\n"
                            "It expires in 10 minutes. If you did not request "
                            "this code, you can ignore this email."
                        ),
                    }
                ],
            },
            timeout=8,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        chat_store.remove_otp(body.email)
        logger.exception("Could not send an IRIS sign-in email.")
        raise HTTPException(
            status_code=502,
            detail="The sign-in email could not be sent. Please try again later.",
        ) from exc
    return {"message": "A sign-in code has been sent if the email is deliverable."}


@app.post("/auth/verify-otp")
@limiter.limit("10/minute")
def verify_email_otp(request: Request, body: VerifyOTPRequest) -> dict[str, str]:
    if not chat_store.verify_otp(
        body.email,
        _otp_digest(body.email, body.code),
        int(time.time()),
    ):
        raise HTTPException(
            status_code=401,
            detail="That sign-in code is invalid or expired.",
        )
    return {
        "token": _issue_chat_token(body.email),
        "email": body.email,
    }


@app.get("/api/chats")
def list_saved_chats(
    authorization: str | None = Header(default=None),
) -> list[dict[str, Any]]:
    email = _authenticated_email(authorization)
    return chat_store.list_threads(email, int(time.time()))


@app.post("/api/chats")
def create_saved_chat(
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    email = _authenticated_email(authorization)
    try:
        return chat_store.create_thread(email, uuid.uuid4().hex, int(time.time()))
    except ChatLimitReached as exc:
        raise HTTPException(
            status_code=409,
            detail="All five saved chat slots are in use. Delete a chat before starting another.",
        ) from exc


@app.get("/api/chats/{thread_id}")
def get_saved_chat(
    thread_id: str,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    email = _authenticated_email(authorization)
    history = chat_store.get_history(email, thread_id, int(time.time()))
    if history is None:
        raise HTTPException(status_code=404, detail="Saved chat not found.")
    return {"id": thread_id, "messages": history}


@app.delete("/api/chats/{thread_id}")
def delete_saved_chat(
    thread_id: str,
    authorization: str | None = Header(default=None),
) -> Response:
    email = _authenticated_email(authorization)
    if not chat_store.delete_thread(email, thread_id):
        raise HTTPException(status_code=404, detail="Saved chat not found.")
    return Response(status_code=204)


@app.post("/api/chats/{thread_id}/stream")
@limiter.limit("20/minute")
def stream_saved_chat(
    request: Request,
    thread_id: str,
    body: ChatPromptRequest,
    authorization: str | None = Header(default=None),
) -> StreamingResponse:
    email = _authenticated_email(authorization)
    previous_history = chat_store.get_history(email, thread_id, int(time.time()))
    if previous_history is None:
        raise HTTPException(status_code=404, detail="Saved chat not found.")
    stored_user = chat_store.append_message(
        email,
        thread_id,
        "user",
        body.prompt,
        body.image_data,
        int(time.time()),
    )
    if stored_user is None:
        raise HTTPException(status_code=404, detail="Saved chat not found.")
    full_history = [*previous_history, {
        "role": "user",
        "content": body.prompt,
        "image_data": body.image_data,
    }]

    def generate_events() -> Iterator[str]:
        try:
            answer = _identity_reply(body.prompt) if body.image_data is None else None
            if answer is None and any(turn.get("image_data") for turn in full_history):
                provider = synthesizer.router.providers.get("gemini")
                if provider is None or not getattr(provider, "available", False):
                    raise RuntimeError(
                        "Image inspection is unavailable: configure GEMINI_API_KEY."
                    )
                yield _sse_event("status", {"message": "Inspecting the image…"})
                answer = provider.generate_multimodal(full_history)
            elif answer is None:
                available = synthesizer.router.get_available_providers()
                if not available:
                    raise RuntimeError("No AI providers are configured on this server.")
                direct_route = re.match(
                    r"^@(groq|gemini|openrouter|ollama)\b(?:\s+(.*))?$",
                    body.prompt,
                    flags=re.IGNORECASE | re.DOTALL,
                )
                if direct_route:
                    provider_name = direct_route.group(1).lower()
                    provider_prompt = (direct_route.group(2) or "").strip()
                    provider = synthesizer.router.providers[provider_name]
                    if not provider_prompt:
                        raise ValueError("Add a prompt after the provider name.")
                    if not getattr(provider, "available", False):
                        raise ValueError("That assistant route is not configured.")
                    prior_context = "\n".join(
                        f"{turn['role'].upper()}: {turn['content']}"
                        for turn in previous_history
                    )
                    prompt = (
                        f"FULL CONVERSATION:\n{prior_context}\n\n"
                        f"CURRENT REQUEST:\n{provider_prompt}"
                        if prior_context
                        else provider_prompt
                    )
                    yield _sse_event("status", {"message": "Working on your request…"})
                    answer = provider.generate(prompt)
                    if (
                        not answer
                        or not str(answer).strip()
                        or provider_response_failed(answer)
                    ):
                        raise RuntimeError(
                            "The selected assistant route could not return a usable answer."
                        )
                else:
                    yield _sse_event(
                        "status",
                        {"message": "IRIS Council is comparing available models…"},
                    )
                    result = synthesizer.synthesize_chat(
                        re.sub(
                            r"^@(council|compare)\s*",
                            "",
                            body.prompt,
                            flags=re.IGNORECASE,
                        ).strip(),
                        previous_history,
                        creator_name=_owner_profile()["name"],
                    )
                    answer = result.get("answer", "")
                    if not result.get("providers"):
                        raise RuntimeError(
                            "IRIS Council could not get a usable answer from any configured model."
                        )
            if not isinstance(answer, str) or not answer.strip():
                raise RuntimeError("IRIS could not return a usable response.")
            answer = answer.strip()
            stored_answer = chat_store.append_message(
                email,
                thread_id,
                "assistant",
                answer,
                None,
                int(time.time()),
            )
            if stored_answer is None:
                raise RuntimeError(
                    "This saved chat was deleted before the response completed."
                )
            yield _sse_event("token", {"token": answer})
            yield _sse_event("done", {"done": True})
        except (RuntimeError, ValueError) as exc:
            yield _sse_event("error", {"error": str(exc)})
        except Exception:
            logger.exception("Saved IRIS chat generation failed")
            yield _sse_event(
                "error",
                {
                    "error": (
                        "IRIS could not complete the request. Check provider "
                        "configuration and server logs."
                    )
                },
            )

    return StreamingResponse(
        generate_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/healthz")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/chat/stream")
@public_rate_limit
def stream_chat(request: Request, body: PromptRequest) -> StreamingResponse:
    identity_reply = _identity_reply(body.prompt)
    if identity_reply is not None:
        return StreamingResponse(
            iter(
                (
                    _sse_event("token", {"token": identity_reply}),
                    _sse_event("done", {"done": True}),
                )
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    def generate_events() -> Iterator[str]:
        try:
            available = synthesizer.router.get_available_providers()
            direct_route = re.match(
                r"^@(groq|gemini|openrouter|ollama)\b(?:\s+(.*))?$",
                body.prompt,
                flags=re.IGNORECASE | re.DOTALL,
            )
            if direct_route:
                provider_name = direct_route.group(1).lower()
                provider_prompt = (direct_route.group(2) or "").strip()
                provider = synthesizer.router.providers[provider_name]
                if not provider_prompt:
                    yield _sse_event(
                        "error",
                        {"error": "Add a prompt after the provider name."},
                    )
                    return
                if not getattr(provider, "available", False):
                    yield _sse_event(
                        "error",
                        {"error": "That assistant route is not configured."},
                    )
                    return

                yield _sse_event(
                    "status",
                    {"message": "Working on your request…"},
                )
                answer = provider.generate(provider_prompt)
                if (
                    not answer
                    or not str(answer).strip()
                    or provider_response_failed(answer)
                ):
                    yield _sse_event(
                        "error",
                        {
                            "error": (
                                "The selected assistant route could not return "
                                "a usable answer."
                            )
                        },
                    )
                    return
                yield _sse_event("token", {"token": str(answer).strip()})
            else:
                if not available:
                    yield _sse_event(
                        "error",
                        {"error": "No AI providers are configured on this server."},
                    )
                    return

                yield _sse_event(
                    "status",
                    {"message": "IRIS Council is comparing available models…"},
                )
                result = synthesizer.synthesize_chat(
                    re.sub(
                        r"^@(council|compare)\s*",
                        "",
                        body.prompt,
                        flags=re.IGNORECASE,
                    ).strip(),
                    [turn.model_dump() for turn in body.history],
                    creator_name=_owner_profile()["name"],
                )
                answer = result.get("answer", "")
                participants = result.get("providers", [])
                if not answer.strip() or not participants:
                    yield _sse_event(
                        "error",
                        {
                            "error": (
                                "IRIS Council could not get a usable response "
                                "from any configured model."
                            )
                        },
                    )
                    return
                yield _sse_event(
                    "token",
                    {"token": answer},
                )
            yield _sse_event("done", {"done": True})
        except Exception:
            logger.exception("IRIS chat stream failed")
            yield _sse_event(
                "error",
                {"error": "IRIS Council generation failed. Check server logs."},
            )

    return StreamingResponse(
        generate_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/v1/research")
@public_rate_limit
def research(request: Request, body: PromptRequest) -> dict[str, Any]:
    try:
        results = search_web(query=body.prompt, max_results=5)
    except Exception as exc:
        logger.exception("IRIS web search failed")
        raise HTTPException(
            status_code=502,
            detail="Web search failed.",
        ) from exc

    sources = [
        {
            "title": str(result.get("title", ""))[:300],
            "url": str(result.get("url", ""))[:2_000],
            "findings": [str(result.get("snippet", ""))[:2_000]],
        }
        for result in results
        if isinstance(result, dict) and result.get("url")
    ]
    if not sources:
        raise HTTPException(status_code=502, detail="Web search returned no sources.")

    try:
        answer = synthesizer.synthesize(
            body.prompt,
            {"evidence": sources},
            mode="force_council",
        )
    except Exception as exc:
        logger.exception("IRIS research synthesis failed")
        raise HTTPException(
            status_code=502,
            detail="Research synthesis failed.",
        ) from exc
    return {"query": body.prompt, "answer": answer, "sources": sources}


@app.post("/v1/execute")
@public_rate_limit
def execute_code(request: Request, body: CodeRequest) -> dict[str, Any]:
    return _run_in_sandbox(body.code)


@app.post("/v1/admin/execute")
@public_rate_limit
def execute_admin_plan(
    request: Request,
    body: AdminExecutionRequest,
    x_iris_admin_key: str | None = Header(default=None, alias="X-IRIS-ADMIN-KEY"),
) -> dict[str, Any]:
    admin_key = _admin_key_or_raise(x_iris_admin_key)
    executor = IRISExecutor(ToolManager())
    return executor.execute_plan(
        body.plan,
        is_public_request=True,
        admin_key=admin_key,
    )

@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="theme-color" content="#090b12">
    <meta name="description" content="The IRIS - Personal Assistant of Adarsh Dwivedi.">
    <title>The IRIS - Personal Assistant of Adarsh Dwivedi</title>
    <style>
      :root {
        color-scheme: dark;
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        background: #090b12;
        color: #f4f5fb;
        font-synthesis: none;
        text-rendering: optimizeLegibility;
        --muted: #898da3;
        --line: #242735;
        --panel: #11131d;
        --accent: #a78bfa;
      }
      * { box-sizing: border-box; }
      [hidden] { display: none !important; }
      body {
        margin: 0;
        min-height: 100vh;
        overflow-x: hidden;
        background: #090b12;
      }
      body::before,
      body::after {
        position: fixed;
        inset: 0;
        content: "";
        pointer-events: none;
      }
      body::before {
        z-index: 0;
        background-image: url("/assets/adarsh-dwivedi.png");
        background-position: center 34%;
        background-size: cover;
        transform: scale(1.025);
      }
      body::after {
        z-index: 0;
        background:
          radial-gradient(ellipse at 78% 20%, #6951ab35 0, transparent 38%),
          linear-gradient(90deg, #080911a6 0%, #0a0b1359 42%, #0b0c1640 100%),
          linear-gradient(0deg, #090a12a8 0%, #090a1220 48%, #090a1266 100%);
      }
      button, textarea { font: inherit; }
      button { color: inherit; }
      .app {
        position: relative;
        z-index: 1;
        height: 100vh;
        height: 100dvh;
        min-height: 0;
        display: grid;
        grid-template-columns: 252px minmax(0, 1fr);
      }
      aside {
        display: flex;
        flex-direction: column;
        padding: 24px 16px 18px;
        border-right: 1px solid #ffffff15;
        background: #0a0b13a8;
        backdrop-filter: blur(18px);
        -webkit-backdrop-filter: blur(18px);
      }
      .brand {
        display: flex;
        align-items: center;
        gap: 11px;
        padding: 3px 8px 28px;
      }
      .brand-mark {
        display: grid;
        width: 38px;
        height: 38px;
        place-items: center;
        border: 1px solid #b7a1ff70;
        border-radius: 13px;
        background: linear-gradient(145deg, #8b69e8, #4f3a98);
        box-shadow: 0 6px 24px #7458ce42;
        font-size: 20px;
        font-weight: 750;
      }
      .brand-name { font-size: 17px; font-weight: 720; letter-spacing: .13em; }
      .brand-caption { margin-top: 3px; color: var(--muted); font-size: 11px; }
      .brand-caption { max-width: 165px; line-height: 1.4; }
      .new-chat {
        width: 100%;
        min-height: 43px;
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 0 13px;
        border: 1px solid #343244;
        border-radius: 11px;
        background: #171722;
        color: #e4defe;
        cursor: pointer;
        transition: border-color .18s, background .18s, transform .18s;
      }
      .new-chat:hover { border-color: #806bc1; background: #1d1a2a; }
      .new-chat:active { transform: scale(.985); }
      #chat-list { display: flex; flex-direction: column; gap: 6px; overflow-y: auto; }
      .saved-chat {
        display: flex;
        align-items: center;
        gap: 4px;
        min-width: 0;
        border: 1px solid transparent;
        border-radius: 10px;
      }
      .saved-chat[aria-current="true"] { border-color: #b7a1ff55; background: #9a7ee21b; }
      .saved-chat-title {
        flex: 1;
        min-width: 0;
        overflow: hidden;
        padding: 9px 10px;
        border: 0;
        background: transparent;
        color: #d8d7e3;
        text-align: left;
        text-overflow: ellipsis;
        white-space: nowrap;
        cursor: pointer;
      }
      .saved-chat-delete {
        width: 32px;
        height: 32px;
        margin-right: 4px;
        border: 0;
        border-radius: 8px;
        background: transparent;
        color: #9a9caf;
        cursor: pointer;
      }
      .saved-chat-delete:hover { background: #6d2d3b66; color: #ffd0d0; }
      #chat-list-status { min-height: 18px; margin: 8px 4px 0; color: #d5b7ff; font-size: 11px; line-height: 1.45; }
      .account-box { margin-top: auto; padding: 14px 8px 0; border-top: 1px solid #ffffff17; color: #a9aabd; font-size: 11px; overflow-wrap: anywhere; }
      .account-box button { margin-top: 8px; border: 0; background: transparent; color: #d2c5ff; cursor: pointer; }
      .connect-owner {
        width: 100%;
        min-height: 42px;
        margin-top: 9px;
        border: 1px solid #9d85e45c;
        border-radius: 11px;
        background: #8e6bd21a;
        color: #e4dcff;
        cursor: pointer;
      }
      .connect-owner:hover { border-color: #b7a1ff; background: #9a7ee22b; }
      .topbar-actions { display: flex; align-items: center; gap: 12px; }
      .owner-online-badge {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        margin-left: 5px;
        color: #9ee8b7;
        font-size: 10px;
        font-weight: 650;
        white-space: nowrap;
      }
      .owner-online-badge[hidden] { display: none; }
      .owner-online-dot {
        width: 7px;
        height: 7px;
        border-radius: 50%;
        background: #77e19d;
        box-shadow: 0 0 9px #77e19d99;
      }
      .topbar-connect {
        min-height: 36px;
        padding: 0 12px;
        border: 1px solid #9d85e45c;
        border-radius: 10px;
        background: #8e6bd21a;
        color: #e4dcff;
        cursor: pointer;
        font-size: 12px;
      }
      .topbar-connect:hover { border-color: #b7a1ff; background: #9a7ee22b; }
      #owner-connect-dialog {
        width: min(560px, calc(100vw - 28px));
        max-height: min(80vh, 720px);
        overflow: hidden;
        padding: 0;
        border: 1px solid #ffffff25;
        border-radius: 20px;
        background: #11121df2;
        color: #f4f5fb;
        box-shadow: 0 30px 100px #000b;
        backdrop-filter: blur(22px);
      }
      #owner-connect-dialog::backdrop { background: #05060bcc; backdrop-filter: blur(5px); }
      .connect-dialog-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 14px; padding: 20px 22px 14px; border-bottom: 1px solid #ffffff17; }
      .connect-dialog-head h2 { margin: 0; font-size: 18px; }
      .connect-dialog-head p { margin: 6px 0 0; color: #a5a7b6; font-size: 12px; line-height: 1.5; }
      #close-owner-chat { width: 34px; height: 34px; border: 1px solid #ffffff20; border-radius: 10px; background: #ffffff0a; cursor: pointer; }
      #connection-state { min-height: 24px; margin: 14px 22px 0; color: #c2b2fa; font-size: 12px; }
      #connection-request-form, #connection-message-form { display: flex; flex-direction: column; align-items: stretch; gap: 10px; margin: 0; padding: 14px 22px 20px; border: 0; background: transparent; box-shadow: none; }
      #connection-request-form input, #connection-request-form textarea, #connection-message-form textarea {
        width: 100%;
        min-height: 42px;
        max-height: 130px;
        padding: 10px 12px;
        border: 1px solid #ffffff20;
        border-radius: 11px;
        background: #090a12b8;
        color: inherit;
      }
      #connection-request-form textarea, #connection-message-form textarea { resize: vertical; }
      #connection-request-form button, #connection-message-form button { align-self: flex-end; min-height: 38px; padding: 0 14px; border: 1px solid #b49aff65; border-radius: 10px; background: #7458b6; color: white; cursor: pointer; }
      #connection-request-form button:disabled, #connection-message-form button:disabled { opacity: .6; cursor: wait; }
      #connection-transcript { max-height: 36vh; overflow-y: auto; display: flex; flex-direction: column; gap: 9px; padding: 10px 22px; }
      .connection-bubble { max-width: 86%; align-self: flex-start; border: 1px solid #ffffff20; border-radius: 12px 12px 12px 4px; padding: 9px 12px; background: #ffffff0c; font-size: 13px; line-height: 1.5; white-space: pre-wrap; overflow-wrap: anywhere; }
      .connection-bubble.owner { align-self: flex-end; border-color: #b49aff65; background: #654c9d7a; }
      .connection-empty { margin: 8px 0; color: #898da3; font-size: 12px; }
      .side-label {
        margin: 30px 9px 11px;
        color: #666b7d;
        font-size: 10px;
        font-weight: 700;
        letter-spacing: .14em;
        text-transform: uppercase;
      }
      .side-note {
        display: flex;
        gap: 10px;
        padding: 11px 9px;
        color: #b2b5c5;
        font-size: 12px;
        line-height: 1.55;
      }
      .side-note span:first-child { color: #b5a0fa; }
      .side-footer {
        margin-top: auto;
        padding: 13px 9px 0;
        border-top: 1px solid var(--line);
        color: #777b8e;
        font-size: 11px;
        line-height: 1.6;
      }
      .side-footer strong { color: #d5d3e3; font-weight: 600; }
      main {
        min-width: 0;
        height: 100%;
        min-height: 0;
        display: flex;
        flex-direction: column;
        position: relative;
        overflow: hidden;
      }
      .topbar {
        min-height: 70px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 0 clamp(20px, 5vw, 64px);
        border-bottom: 1px solid #ffffff18;
        background: #0b0c1494;
        backdrop-filter: blur(18px);
        -webkit-backdrop-filter: blur(18px);
      }
      .topbar-title { color: #c7c8d5; font-size: 13px; font-weight: 560; }
      .status {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        padding: 7px 10px;
        border: 1px solid #282a37;
        border-radius: 99px;
        color: #a7aabd;
        font-size: 11px;
      }
      .status-dot {
        width: 7px;
        height: 7px;
        border-radius: 50%;
        background: #7ed6a1;
        box-shadow: 0 0 10px #7ed6a18a;
      }
      .status.unavailable .status-dot {
        background: #e3a15f;
        box-shadow: 0 0 10px #e3a15f70;
      }
      .content {
        width: min(100%, 960px);
        flex: 1;
        min-height: 0;
        display: flex;
        flex-direction: column;
        margin: 0 auto;
        padding: 28px;
        overflow: hidden;
      }
      #welcome {
        flex: 1;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        margin: auto 0;
        padding: clamp(32px, 5vh, 56px) 24px;
        border: 1px solid #ffffff25;
        border-radius: 28px;
        background:
          radial-gradient(ellipse at 50% 0%, #6d55aa18 0, transparent 64%),
          linear-gradient(145deg, #10111d9c, #10111d8c);
        box-shadow: 0 28px 90px #0007, inset 0 1px #ffffff10;
        backdrop-filter: blur(12px);
        -webkit-backdrop-filter: blur(12px);
        text-align: center;
      }
      #welcome[hidden] { display: none; }
      .eyebrow {
        margin: 0 0 12px;
        color: #b6a4ee;
        font-size: 11px;
        font-weight: 700;
        letter-spacing: .17em;
        text-transform: uppercase;
      }
      h1 {
        margin: 0;
        font-size: clamp(31px, 5vw, 46px);
        font-weight: 650;
        letter-spacing: -.045em;
      }
      .welcome-copy {
        max-width: 475px;
        margin: 13px 0 30px;
        color: #a6a8b8;
        font-size: 15px;
        line-height: 1.7;
      }
      .suggestions {
        width: min(100%, 650px);
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 10px;
      }
      .suggestion {
        min-height: 70px;
        display: flex;
        align-items: flex-start;
        gap: 12px;
        padding: 14px;
        border: 1px solid #ffffff20;
        border-radius: 15px;
        background: #ffffff09;
        backdrop-filter: blur(8px);
        text-align: left;
        cursor: pointer;
        transition: border-color .18s, background .18s, transform .18s, box-shadow .18s;
      }
      .suggestion:hover {
        transform: translateY(-2px);
        border-color: #b7a1ff80;
        background: #9a7ee21b;
        box-shadow: 0 10px 28px #0003;
      }
      .suggestion-icon { color: #b9a3ff; font-size: 17px; line-height: 1; }
      .suggestion strong { display: block; color: #e2e0ec; font-size: 12px; font-weight: 600; }
      .suggestion small { display: block; margin-top: 5px; color: #898b9e; font-size: 11px; }
      #chat {
        flex: 1;
        min-height: 0;
        overscroll-behavior: contain;
        overflow-y: auto;
        overflow-x: hidden;
        display: flex;
        flex-direction: column;
        gap: 22px;
        padding: 34px 0 24px;
      }
      #chat:empty { display: none; }
      .message-row { display: flex; align-items: flex-start; gap: 11px; }
      .message-row.user { justify-content: flex-end; }
      .avatar {
        flex: 0 0 30px;
        width: 30px;
        height: 30px;
        display: grid;
        place-items: center;
        border: 1px solid #64519b;
        border-radius: 10px;
        background: linear-gradient(145deg, #7658bf, #44326f);
        color: white;
        font-size: 13px;
        font-weight: 700;
      }
      .msg {
        max-width: min(78%, 680px);
        padding: 12px 15px;
        border-radius: 14px;
        line-height: 1.7;
        white-space: pre-wrap;
        overflow-wrap: anywhere;
        font-size: 14px;
      }
      .message-image {
        display: block;
        max-width: min(100%, 360px);
        max-height: 300px;
        margin-top: 8px;
        border: 1px solid #ffffff2b;
        border-radius: 10px;
        object-fit: contain;
      }
      #attachment-preview {
        display: flex;
        align-items: center;
        gap: 10px;
        width: fit-content;
        max-width: 100%;
        margin: 0 0 9px 2px;
        padding: 7px;
        border: 1px solid #ffffff25;
        border-radius: 12px;
        background: #11121df2;
      }
      #attachment-preview img { width: 54px; height: 54px; border-radius: 8px; object-fit: cover; }
      #attachment-name { max-width: 190px; overflow: hidden; color: #c6c4d3; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
      #remove-attachment { width: 30px; height: 30px; border: 1px solid #ffffff20; border-radius: 9px; background: #ffffff0c; cursor: pointer; }
      #attach-image, #auth-trigger {
        flex: 0 0 38px;
        width: 38px;
        height: 38px;
        display: grid;
        place-items: center;
        border: 1px solid #ffffff1c;
        border-radius: 11px;
        background: #ffffff08;
        color: #d7cafa;
        cursor: pointer;
      }
      #attach-image:hover, #auth-trigger:hover { border-color: #b7a1ff80; background: #9a7ee21b; }
      #image-file { display: none; }
      .assistant .msg {
        border: 1px solid #ffffff20;
        border-top-left-radius: 5px;
        background: #11121dcc;
        backdrop-filter: blur(14px);
      }
      .user .msg {
        border: 1px solid #b29aff65;
        border-top-right-radius: 5px;
        background: #574582d9;
        backdrop-filter: blur(12px);
      }
      .error .msg { border-color: #783e49; background: #3a222b; color: #ffd0d0; }
      .composer-wrap {
        position: sticky;
        z-index: 3;
        bottom: 0;
        flex: 0 0 auto;
        padding: 12px 0 max(20px, env(safe-area-inset-bottom));
        background: linear-gradient(180deg, transparent, #090b12a8 24%, #090b12e8);
      }
      form {
        position: relative;
        display: flex;
        align-items: flex-end;
        gap: 12px;
        padding: 11px 11px 11px 16px;
        border: 1px solid #343341;
        border-radius: 17px;
        background: #10111bd9;
        box-shadow: 0 16px 48px #0005, inset 0 1px #ffffff0a;
        backdrop-filter: blur(18px);
        -webkit-backdrop-filter: blur(18px);
        transition: border-color .18s, box-shadow .18s;
      }
      form:focus-within {
        border-color: #7962ba;
        box-shadow: 0 0 0 3px #806bd51a, 0 12px 42px #0003;
      }
      textarea {
        flex: 1;
        min-width: 0;
        height: 40px;
        max-height: 112px;
        min-height: 40px;
        overflow-y: hidden;
        resize: none;
        border: 0;
        outline: 0;
        padding: 5px 0;
        color: inherit;
        background: transparent;
        line-height: 1.55;
      }
      textarea::placeholder { color: #77798d; }
      #prompt-form textarea { flex-basis: 40px; }
      #emoji-trigger {
        flex: 0 0 38px;
        width: 38px;
        height: 38px;
        display: grid;
        place-items: center;
        align-self: flex-end;
        border: 1px solid #ffffff1c;
        border-radius: 11px;
        background: #ffffff08;
        color: #d7cafa;
        font-size: 19px;
        cursor: pointer;
      }
      #emoji-trigger:hover,
      #emoji-trigger[aria-expanded="true"] {
        border-color: #b7a1ff80;
        background: #9a7ee21b;
      }
      #emoji-picker {
        position: absolute;
        z-index: 5;
        right: 8px;
        bottom: calc(100% + 10px);
        width: min(300px, calc(100vw - 48px));
        display: grid;
        grid-template-columns: repeat(7, 1fr);
        gap: 4px;
        padding: 10px;
        border: 1px solid #ffffff25;
        border-radius: 15px;
        background: #11121df5;
        box-shadow: 0 18px 54px #0009;
        backdrop-filter: blur(18px);
      }
      #emoji-picker[hidden] { display: none; }
      #auth-dialog {
        width: min(430px, calc(100vw - 28px));
        padding: 24px;
        border: 1px solid #ffffff25;
        border-radius: 20px;
        background: #11121df5;
        color: #f4f5fb;
        box-shadow: 0 30px 100px #000b;
      }
      #auth-dialog::backdrop { background: #05060bcc; backdrop-filter: blur(5px); }
      #auth-dialog h2 { margin: 0; font-size: 20px; }
      #auth-dialog p { color: #a5a7b6; font-size: 13px; line-height: 1.55; }
      #auth-email-form, #auth-code-form {
        display: flex;
        flex-direction: column;
        align-items: stretch;
        gap: 10px;
        margin: 0;
        padding: 0;
        border: 0;
        background: transparent;
        box-shadow: none;
        backdrop-filter: none;
      }
      #auth-dialog label { color: #c8c8d5; font-size: 12px; text-align: left; }
      #auth-email, #auth-code { width: 100%; min-height: 44px; padding: 10px 12px; border: 1px solid #ffffff25; border-radius: 10px; background: #090a12; color: inherit; }
      #auth-email-form button, #auth-code-form button { min-height: 42px; border: 1px solid #b49aff65; border-radius: 10px; background: #7458b6; cursor: pointer; }
      #auth-code-form button[type="button"] { background: #ffffff0c; color: #d7cafa; }
      #auth-status { min-height: 18px; color: #c6b3ff !important; }
      #chat-list-toggle { display: none; }
      .emoji-option {
        width: 36px;
        height: 36px;
        display: grid;
        place-items: center;
        border: 0;
        border-radius: 9px;
        background: transparent;
        font-size: 20px;
        cursor: pointer;
      }
      .emoji-option:hover,
      .emoji-option:focus-visible { background: #9a7ee22b; }
      #send {
        flex: 0 0 40px;
        width: 40px;
        height: 40px;
        display: grid;
        place-items: center;
        border: 0;
        border-radius: 12px;
        background: linear-gradient(145deg, #a38af2, #775ad0);
        color: white;
        cursor: pointer;
        box-shadow: 0 4px 14px #7657c54d;
        transition: opacity .18s, transform .18s;
      }
      #send:hover:not(:disabled) { transform: translateY(-1px); }
      #send:disabled { cursor: wait; opacity: .48; box-shadow: none; }
      .composer-hint { margin: 10px 0 0; color: #686b7c; font-size: 10px; text-align: center; }
      button:focus-visible { outline: 2px solid #c0afff; outline-offset: 3px; }
      @media (prefers-reduced-motion: reduce) {
        *, *::before, *::after { scroll-behavior: auto !important; transition-duration: .01ms !important; }
      }
      @media (max-width: 720px) {
        .app { grid-template-columns: 1fr; }
        aside { display: none; }
        .app.sidebar-open aside {
          position: fixed;
          z-index: 10;
          inset: 0 auto 0 0;
          width: min(300px, 86vw);
          display: flex;
          box-shadow: 12px 0 50px #0008;
        }
        main { height: 100dvh; min-height: 0; }
        .topbar { min-height: 60px; padding: 0 18px; }
        .topbar-title { max-width: 70%; font-size: 12px; line-height: 1.5; }
        .topbar-actions { gap: 7px; }
        .topbar-connect { padding: 0 9px; font-size: 11px; }
        #chat-list-toggle { display: grid; flex: 0 0 34px; width: 34px; height: 34px; place-items: center; border: 1px solid #ffffff20; border-radius: 9px; background: #ffffff08; cursor: pointer; }
        .content { padding: 18px; }
        #welcome { padding: 38px 18px; border-radius: 22px; }
        .welcome-copy { font-size: 14px; }
      }
      @media (max-width: 440px) {
        body::before { background-position: 54% 32%; }
        body::after {
          background:
            linear-gradient(180deg, #080911a8 0%, #090a124f 38%, #090a12b0 100%),
            linear-gradient(90deg, #08091145, #0809111c);
        }
        .suggestions { grid-template-columns: 1fr; }
        .suggestion { min-height: 58px; }
        #chat { gap: 16px; padding-top: 24px; }
        .msg { max-width: 88%; font-size: 13px; }
        .composer-wrap { padding-bottom: max(14px, env(safe-area-inset-bottom)); }
        #prompt-form { gap: 7px; padding-left: 12px; }
        #emoji-trigger { flex-basis: 34px; width: 34px; height: 36px; }
      }
    </style>
  </head>
  <body>
    <div class="app">
      <aside aria-label="IRIS workspace">
        <div class="brand">
          <div class="brand-mark" aria-hidden="true">i</div>
          <div><div class="brand-name">The IRIS</div><div class="brand-caption">Personal Assistant of Adarsh Dwivedi</div></div>
        </div>
        <button class="new-chat" id="new-chat" type="button"><span aria-hidden="true">＋</span> New conversation</button>
        <div class="side-label">Your chats <span id="chat-count">0 / 5</span></div>
        <div id="chat-list" aria-label="Saved conversations"></div>
        <p id="chat-list-status" role="status"></p>
        <button class="connect-owner" type="button" data-open-owner-chat>
          Chat with Adarsh
          <span class="owner-online-badge" data-owner-online hidden>
            <span class="owner-online-dot" aria-hidden="true"></span>Online now
          </span>
        </button>
        <div class="side-label">Made for momentum</div>
        <div class="side-note"><span aria-hidden="true">✳</span><span>Explore ideas and get clear, useful answers.</span></div>
        <div class="side-note"><span aria-hidden="true">⌘</span><span>Research, write, plan, and build in one place.</span></div>
        <div class="side-note"><span aria-hidden="true">↗</span><span>Ask a follow-up whenever you need more.</span></div>
        <div class="side-footer"><strong>IRIS</strong> · Intelligent Routing &amp; Iterative Synthesis<br>Created by Adarsh Dwivedi</div>
        <div class="account-box">
          <span id="account-email">Sign in to save conversations</span><br>
          <button id="signout-button" type="button" hidden>Sign out</button>
        </div>
      </aside>
      <main>
        <header class="topbar">
          <div class="topbar-title">The IRIS - Personal Assistant of Adarsh Dwivedi</div>
          <div class="topbar-actions">
            <button id="chat-list-toggle" type="button" aria-label="Show saved chats" aria-expanded="false">☰</button>
            <button class="topbar-connect" type="button" data-open-owner-chat>
              Chat with Adarsh
              <span class="owner-online-badge" data-owner-online hidden>
                <span class="owner-online-dot" aria-hidden="true"></span>Online now
              </span>
            </button>
            <button id="auth-trigger" type="button" aria-label="Sign in with email">↗</button>
            <div class="status"><span class="status-dot" aria-hidden="true"></span>Here to help</div>
          </div>
        </header>
        <div class="content">
          <section id="welcome" aria-labelledby="welcome-title">
            <p class="eyebrow">The IRIS · Personal Assistant</p>
            <h1 id="welcome-title">What’s on your mind?</h1>
            <p class="welcome-copy">Ask a question, untangle a tricky idea, or get a first draft moving. I’m here to help you make progress.</p>
            <div class="suggestions" aria-label="Suggested prompts">
              <button class="suggestion" type="button" data-prompt="Tell me about yourself and who created you">
                <span class="suggestion-icon" aria-hidden="true">✦</span><span><strong>Meet IRIS</strong><small>What I am and who created me</small></span>
              </button>
              <button class="suggestion" type="button" data-prompt="Help me brainstorm an idea">
                <span class="suggestion-icon" aria-hidden="true">◇</span><span><strong>Explore an idea</strong><small>Brainstorm something new</small></span>
              </button>
              <button class="suggestion" type="button" data-prompt="Help me write a clear, polished email">
                <span class="suggestion-icon" aria-hidden="true">↗</span><span><strong>Write with ease</strong><small>Turn a rough thought into a draft</small></span>
              </button>
              <button class="suggestion" type="button" data-prompt="Help me break a complex task into simple steps">
                <span class="suggestion-icon" aria-hidden="true">☷</span><span><strong>Make a plan</strong><small>Break a big task into steps</small></span>
              </button>
            </div>
          </section>
          <section id="chat" aria-live="polite" aria-label="Conversation"></section>
          <div class="composer-wrap">
            <div id="attachment-preview" hidden>
              <img id="attachment-thumbnail" alt="Image attachment preview">
              <span id="attachment-name"></span>
              <button id="remove-attachment" type="button" aria-label="Remove attachment">×</button>
            </div>
            <form id="prompt-form">
              <input id="image-file" type="file" accept="image/png,image/jpeg,image/webp,.png,.jpg,.jpeg,.webp">
              <textarea id="prompt" rows="1" maxlength="8000" aria-label="Your message" placeholder="Message IRIS…"></textarea>
              <button id="attach-image" type="button" aria-label="Attach an image">📎</button>
              <button id="emoji-trigger" type="button" aria-label="Insert emoji" aria-expanded="false" aria-controls="emoji-picker">☺</button>
              <div id="emoji-picker" role="group" aria-label="Choose an emoji" hidden>
                <button class="emoji-option" type="button" aria-label="Smiling face">😀</button>
                <button class="emoji-option" type="button" aria-label="Grinning face">😄</button>
                <button class="emoji-option" type="button" aria-label="Winking face">😉</button>
                <button class="emoji-option" type="button" aria-label="Heart eyes">😍</button>
                <button class="emoji-option" type="button" aria-label="Thinking face">🤔</button>
                <button class="emoji-option" type="button" aria-label="Thinking">🧐</button>
                <button class="emoji-option" type="button" aria-label="Crying with laughter">😂</button>
                <button class="emoji-option" type="button" aria-label="Thumbs up">👍</button>
                <button class="emoji-option" type="button" aria-label="Clapping">👏</button>
                <button class="emoji-option" type="button" aria-label="Waving hand">👋</button>
                <button class="emoji-option" type="button" aria-label="Folded hands">🙏</button>
                <button class="emoji-option" type="button" aria-label="Sparkles">✨</button>
                <button class="emoji-option" type="button" aria-label="Heart">❤️</button>
                <button class="emoji-option" type="button" aria-label="Fire">🔥</button>
              </div>
              <button id="send" type="submit" aria-label="Send message">
                <svg viewBox="0 0 24 24" width="19" height="19" fill="none" aria-hidden="true"><path d="M12 19V5m0 0-6 6m6-6 6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
              </button>
            </form>
            <p class="composer-hint">IRIS can make mistakes. Check important information.</p>
          </div>
        </div>
      </main>
    </div>
    <dialog id="auth-dialog" aria-labelledby="auth-title">
      <h2 id="auth-title">Sign in to IRIS</h2>
      <p>Use your email to save up to five chats and continue them across visits. Your conversations are retained for 10 days.</p>
      <form id="auth-email-form">
        <label for="auth-email">Email address</label>
        <input id="auth-email" type="email" maxlength="254" autocomplete="email" required placeholder="you@example.com">
        <button type="submit">Email me a sign-in code</button>
      </form>
      <form id="auth-code-form" hidden>
        <label for="auth-code">6-digit sign-in code</label>
        <input id="auth-code" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{6}" maxlength="6" required>
        <button type="submit">Verify and continue</button>
        <button id="auth-back-button" type="button">Change email or request another code</button>
      </form>
      <p id="auth-status" role="status"></p>
    </dialog>
    <dialog id="owner-connect-dialog" aria-labelledby="owner-connect-title">
      <div class="connect-dialog-head">
        <div>
          <h2 id="owner-connect-title">Connect with Adarsh</h2>
          <p>Send a private request. Your conversation starts only if Adarsh approves it.</p>
        </div>
        <button id="close-owner-chat" type="button" aria-label="Close private chat">×</button>
      </div>
      <p id="connection-state" role="status">Send a short message to request a private conversation.</p>
      <form id="connection-request-form">
        <input id="visitor-name" type="text" maxlength="80" autocomplete="nickname" placeholder="Your name (optional)">
        <textarea id="connection-request-message" rows="3" maxlength="2000" placeholder="What would you like to talk about?" required></textarea>
        <button type="submit">Request a conversation</button>
      </form>
      <div id="connection-transcript" aria-live="polite" hidden></div>
      <form id="connection-message-form" hidden>
        <textarea id="connection-message" rows="2" maxlength="2000" placeholder="Write a private message…" required></textarea>
        <button type="submit">Send</button>
      </form>
    </dialog>
    <script>
      const chat = document.getElementById("chat");
      const form = document.getElementById("prompt-form");
      const input = document.getElementById("prompt");
      const button = document.getElementById("send");
      const welcome = document.getElementById("welcome");
      const newChatButton = document.getElementById("new-chat");
      const chatList = document.getElementById("chat-list");
      const chatCount = document.getElementById("chat-count");
      const chatListStatus = document.getElementById("chat-list-status");
      const authDialog = document.getElementById("auth-dialog");
      const authEmailForm = document.getElementById("auth-email-form");
      const authCodeForm = document.getElementById("auth-code-form");
      const authStatus = document.getElementById("auth-status");
      const authEmail = document.getElementById("auth-email");
      const authCode = document.getElementById("auth-code");
      const accountEmail = document.getElementById("account-email");
      const signoutButton = document.getElementById("signout-button");
      const imageFile = document.getElementById("image-file");
      const attachmentPreview = document.getElementById("attachment-preview");
      const attachmentThumbnail = document.getElementById("attachment-thumbnail");
      const attachmentName = document.getElementById("attachment-name");
      const CACHE_TTL = 10 * 24 * 60 * 60 * 1000;
      const conversationHistory = [];
      let authToken = localStorage.getItem("iris_auth_token") || "";
      let signedInEmail = localStorage.getItem("iris_auth_email") || "";
      let activeThreadId = "";
      let savedThreads = [];
      let attachedImage = null;
      let activeRequest = null;

      function addMessage(text, kind, imageData = null) {
        const row = document.createElement("div");
        row.className = `message-row ${kind}`;
        if (kind === "assistant") {
          const avatar = document.createElement("span");
          avatar.className = "avatar";
          avatar.setAttribute("aria-hidden", "true");
          avatar.textContent = "i";
          row.appendChild(avatar);
        }
        const message = document.createElement("div");
        message.className = "msg";
        message.textContent = text;
        if (imageData) {
          const image = document.createElement("img");
          image.className = "message-image";
          image.src = imageData;
          image.alt = "Attached image";
          message.appendChild(image);
        }
        row.appendChild(message);
        chat.appendChild(row);
        chat.scrollTop = chat.scrollHeight;
        return message;
      }

      function cacheKey(threadId) {
        return `iris-chat-cache:${signedInEmail}:${threadId}`;
      }

      function saveLocalCache() {
        if (!activeThreadId || !signedInEmail) return;
        try {
          localStorage.setItem(
            cacheKey(activeThreadId),
            JSON.stringify({ savedAt: Date.now(), messages: conversationHistory }),
          );
          localStorage.setItem("iris_active_chat", activeThreadId);
        } catch (error) {
          chatListStatus.textContent = `Local history cache unavailable: ${error.message}`;
        }
      }

      function getFreshLocalCache(threadId) {
        try {
          const saved = JSON.parse(localStorage.getItem(cacheKey(threadId)) || "null");
          if (saved && Date.now() - saved.savedAt < CACHE_TTL) return saved;
        } catch (_) {
          return null;
        }
        return null;
      }

      function renderHistory(messages, saveCache = true) {
        chat.replaceChildren();
        conversationHistory.splice(
          0,
          conversationHistory.length,
          ...messages.map((message) => ({
            role: message.role,
            content: message.content,
            image_data: message.image_data || null,
          })),
        );
        for (const message of conversationHistory) {
          addMessage(
            message.content,
            message.role === "assistant" ? "assistant" : "user",
            message.image_data,
          );
        }
        welcome.hidden = conversationHistory.length > 0;
        if (saveCache) saveLocalCache();
      }

      async function chatApi(path, options = {}) {
        const headers = { ...(options.headers || {}) };
        if (authToken) headers.Authorization = `Bearer ${authToken}`;
        if (options.body) headers["Content-Type"] = "application/json";
        const response = await fetch(path, { ...options, headers });
        if (response.status === 401 && authToken) {
          signOut(false);
          if (!authDialog.open) authDialog.showModal();
        }
        if (response.status === 204) return null;
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(payload.detail || `Request failed (${response.status})`);
        }
        return payload;
      }

      function renderSavedChats() {
        chatList.replaceChildren();
        chatCount.textContent = `${savedThreads.length} / 5`;
        for (const thread of savedThreads) {
          const item = document.createElement("div");
          item.className = "saved-chat";
          item.setAttribute("aria-current", String(thread.id === activeThreadId));
          const title = document.createElement("button");
          title.className = "saved-chat-title";
          title.type = "button";
          title.textContent = thread.title || "New conversation";
          title.title = title.textContent;
          title.addEventListener("click", () => {
            selectThread(thread.id).catch((error) => {
              chatListStatus.textContent = error.message;
            });
          });
          const remove = document.createElement("button");
          remove.className = "saved-chat-delete";
          remove.type = "button";
          remove.textContent = "×";
          remove.setAttribute("aria-label", `Delete ${title.textContent}`);
          remove.addEventListener("click", async () => {
            if (!window.confirm(`Delete "${title.textContent}" and its messages?`)) return;
            try {
              await chatApi(`/api/chats/${encodeURIComponent(thread.id)}`, {
                method: "DELETE",
              });
              localStorage.removeItem(cacheKey(thread.id));
              savedThreads = savedThreads.filter((entry) => entry.id !== thread.id);
              if (activeThreadId === thread.id) {
                activeThreadId = "";
                conversationHistory.length = 0;
                chat.replaceChildren();
                welcome.hidden = false;
                if (savedThreads.length) await selectThread(savedThreads[0].id);
                else await createThread();
              } else {
                renderSavedChats();
              }
              chatListStatus.textContent = "";
            } catch (error) {
              chatListStatus.textContent = error.message;
            }
          });
          item.append(title, remove);
          chatList.appendChild(item);
        }
      }

      async function refreshSavedChats() {
        savedThreads = await chatApi("/api/chats");
        renderSavedChats();
      }

      async function selectThread(threadId) {
        activeThreadId = threadId;
        localStorage.setItem("iris_active_chat", threadId);
        chatListStatus.textContent = "";
        try {
          const result = await chatApi(`/api/chats/${encodeURIComponent(threadId)}`);
          renderHistory(result.messages);
        } catch (error) {
          const cached = getFreshLocalCache(threadId);
          if (!cached) throw error;
          renderHistory(cached.messages, false);
          chatListStatus.textContent = "Showing a recent local copy; server sync is unavailable.";
        }
        renderSavedChats();
        document.querySelector(".app").classList.remove("sidebar-open");
        document.getElementById("chat-list-toggle").setAttribute("aria-expanded", "false");
      }

      async function createThread() {
        if (!authToken) {
          if (!authDialog.open) authDialog.showModal();
          return;
        }
        closeEmojiPicker();
        input.value = "";
        input.style.height = "40px";
        try {
          const created = await chatApi("/api/chats", { method: "POST" });
          savedThreads.unshift(created);
          await selectThread(created.id);
        } catch (error) {
          chatListStatus.textContent = error.message;
        }
      }

      async function initializeChats() {
        if (!authToken) {
          if (!authDialog.open) authDialog.showModal();
          return;
        }
        accountEmail.textContent = signedInEmail;
        signoutButton.hidden = false;
        try {
          await refreshSavedChats();
          const preferred = localStorage.getItem("iris_active_chat");
          const selected = savedThreads.find((thread) => thread.id === preferred) || savedThreads[0];
          if (selected) await selectThread(selected.id);
          else await createThread();
        } catch (error) {
          const cachedThread = localStorage.getItem("iris_active_chat");
          const cached = cachedThread ? getFreshLocalCache(cachedThread) : null;
          if (cached && cachedThread) {
            activeThreadId = cachedThread;
            renderHistory(cached.messages, false);
            chatListStatus.textContent = `Offline copy loaded. ${error.message}`;
          } else {
            chatListStatus.textContent = error.message;
          }
        }
      }

      function signOut(showStatus = true) {
        if (activeRequest) activeRequest.abort();
        activeRequest = null;
        localStorage.removeItem("iris_auth_token");
        localStorage.removeItem("iris_auth_email");
        authToken = "";
        signedInEmail = "";
        activeThreadId = "";
        savedThreads = [];
        conversationHistory.length = 0;
        chat.replaceChildren();
        input.value = "";
        attachedImage = null;
        attachmentPreview.hidden = true;
        attachmentThumbnail.removeAttribute("src");
        button.disabled = false;
        newChatButton.disabled = false;
        renderSavedChats();
        accountEmail.textContent = "Sign in to save conversations";
        signoutButton.hidden = true;
        welcome.hidden = false;
        if (showStatus) {
          authStatus.textContent = "You have signed out.";
          if (!authDialog.open) authDialog.showModal();
        }
      }

      document.getElementById("auth-trigger").addEventListener("click", () => {
        if (!authDialog.open) authDialog.showModal();
      });
      document.getElementById("auth-back-button").addEventListener("click", () => {
        authCodeForm.hidden = true;
        authEmailForm.hidden = false;
        authCode.value = "";
        authEmail.focus();
      });
      authEmailForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        authStatus.textContent = "Sending your sign-in code…";
        try {
          await chatApi("/auth/send-otp", {
            method: "POST",
            body: JSON.stringify({ email: authEmail.value.trim() }),
          });
          authEmailForm.hidden = true;
          authCodeForm.hidden = false;
          authCode.focus();
          authStatus.textContent = "Check your email. The code expires in 10 minutes.";
        } catch (error) {
          authStatus.textContent = error.message;
        }
      });
      authCodeForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        authStatus.textContent = "Verifying your code…";
        try {
          const result = await chatApi("/auth/verify-otp", {
            method: "POST",
            body: JSON.stringify({
              email: authEmail.value.trim(),
              code: authCode.value.trim(),
            }),
          });
          authToken = result.token;
          signedInEmail = result.email;
          localStorage.setItem("iris_auth_token", authToken);
          localStorage.setItem("iris_auth_email", signedInEmail);
          authStatus.textContent = "";
          authCodeForm.hidden = true;
          authEmailForm.hidden = false;
          authDialog.close();
          await initializeChats();
        } catch (error) {
          authStatus.textContent = error.message;
        }
      });
      signoutButton.addEventListener("click", () => signOut());
      document.getElementById("chat-list-toggle").addEventListener("click", (event) => {
        const app = document.querySelector(".app");
        const opening = !app.classList.contains("sidebar-open");
        app.classList.toggle("sidebar-open", opening);
        event.currentTarget.setAttribute("aria-expanded", String(opening));
      });
      newChatButton.addEventListener("click", createThread);

      function setAttachedImage(file) {
        const extension = file.name.split(".").pop().toLowerCase();
        const allowedTypes = {
          png: "image/png",
          jpg: "image/jpeg",
          jpeg: "image/jpeg",
          webp: "image/webp",
        };
        const mimeType = allowedTypes[extension];
        if (!mimeType || (file.type && file.type !== mimeType)) {
          chatListStatus.textContent = "Choose a PNG, JPG, JPEG, or WebP image.";
          return;
        }
        if (!file.size || file.size > 5 * 1024 * 1024) {
          chatListStatus.textContent = "Images must be smaller than 5 MB.";
          return;
        }
        const reader = new FileReader();
        reader.onload = () => {
          attachedImage = { data: reader.result, name: file.name };
          attachmentThumbnail.src = reader.result;
          attachmentName.textContent = file.name;
          attachmentPreview.hidden = false;
          chatListStatus.textContent = "";
        };
        reader.onerror = () => {
          chatListStatus.textContent = "The selected image could not be read.";
        };
        reader.readAsDataURL(file);
      }

      document.getElementById("attach-image").addEventListener("click", () => imageFile.click());
      imageFile.addEventListener("change", () => {
        if (imageFile.files.length) setAttachedImage(imageFile.files[0]);
        imageFile.value = "";
      });
      document.getElementById("remove-attachment").addEventListener("click", () => {
        attachedImage = null;
        attachmentPreview.hidden = true;
        attachmentThumbnail.removeAttribute("src");
      });
      form.addEventListener("paste", (event) => {
        const imageItem = [...(event.clipboardData?.items || [])]
          .find((item) => item.type.startsWith("image/"));
        if (!imageItem) return;
        const file = imageItem.getAsFile();
        if (file) {
          event.preventDefault();
          setAttachedImage(file);
        }
      });

      document.querySelectorAll("[data-prompt]").forEach((suggestion) => {
        suggestion.addEventListener("click", () => {
          input.value = suggestion.dataset.prompt;
          form.requestSubmit();
        });
      });

      input.addEventListener("input", () => {
        input.style.height = "40px";
        const nextHeight = Math.min(input.scrollHeight, 112);
        input.style.height = `${nextHeight}px`;
        input.style.overflowY = input.scrollHeight > 112 ? "auto" : "hidden";
      });

      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey) {
          event.preventDefault();
          form.requestSubmit();
        }
      });

      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const prompt = input.value.trim() ||
          (attachedImage ? "Please inspect this image for UI bugs, errors, or anomalies." : "");
        if (!prompt || button.disabled) return;
        if (!authToken) {
          if (!authDialog.open) authDialog.showModal();
          return;
        }
        if (!activeThreadId) {
          await createThread();
          if (!activeThreadId) return;
        }

        closeEmojiPicker();
        welcome.hidden = true;
        const image = attachedImage;
        conversationHistory.push({
          role: "user",
          content: prompt,
          image_data: image ? image.data : null,
        });
        addMessage(prompt, "user", image ? image.data : null);
        saveLocalCache();
        attachedImage = null;
        attachmentPreview.hidden = true;
        attachmentThumbnail.removeAttribute("src");
        input.value = "";
        input.style.height = "40px";
        input.style.overflowY = "hidden";
        button.disabled = true;
        newChatButton.disabled = true;
        const controller = new AbortController();
        activeRequest = controller;
        const answer = addMessage("", "assistant");
        let hasToken = false;
        let streamError = "";
        try {
          const response = await fetch(
            `/api/chats/${encodeURIComponent(activeThreadId)}/stream`,
            {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              Authorization: `Bearer ${authToken}`,
            },
            body: JSON.stringify({
              prompt,
              image_data: image ? image.data : null,
            }),
            signal: controller.signal,
            },
          );
          if (!response.ok) {
            let detail = `Request failed (${response.status})`;
            if (response.status === 401) {
              signOut(false);
              if (!authDialog.open) authDialog.showModal();
            }
            try {
              const body = await response.json();
              if (body.detail) detail = body.detail;
            } catch (_) {}
            throw new Error(detail);
          }
          if (!response.body) throw new Error("Streaming is not supported by this browser.");

          const reader = response.body.getReader();
          const decoder = new TextDecoder("utf-8");
          let pending = "";
          const consumeFrame = (frame) => {
            const data = frame.split(/\\r?\\n/)
              .filter((line) => line.startsWith("data:"))
              .map((line) => line.slice(5).trimStart())
              .join("\\n");
            if (!data) return;
            const payload = JSON.parse(data);
            if (payload.status && !hasToken) {
              answer.textContent = payload.status;
            }
            if (payload.token) {
              if (!hasToken) answer.textContent = "";
              hasToken = true;
              answer.textContent += payload.token;
              conversationHistory.push({
                role: "assistant",
                content: answer.textContent,
                image_data: null,
              });
              saveLocalCache();
              if (chat.scrollHeight - chat.scrollTop - chat.clientHeight < 96) {
                chat.scrollTop = chat.scrollHeight;
              }
            }
            if (payload.error) streamError = payload.error;
          };

          while (true) {
            const { value, done } = await reader.read();
            pending += decoder.decode(value, { stream: !done });
            const frames = pending.split(/\\r?\\n\\r?\\n/);
            pending = frames.pop();
            for (const frame of frames) consumeFrame(frame);
            if (done) {
              if (pending) consumeFrame(pending);
              break;
            }
          }
          if (streamError) throw new Error(streamError);
          if (!hasToken) throw new Error("IRIS returned no response.");
          await refreshSavedChats();
        } catch (error) {
          if (error.name !== "AbortError") {
            answer.parentElement.classList.add("error");
            answer.textContent = `Error: ${error.message}`;
          }
        } finally {
          if (activeRequest === controller) {
            button.disabled = false;
            newChatButton.disabled = false;
            activeRequest = null;
            input.focus();
          }
        }
      });

      const emojiTrigger = document.getElementById("emoji-trigger");
      const emojiPicker = document.getElementById("emoji-picker");
      let emojiSelectionStart = 0;
      let emojiSelectionEnd = 0;

      function closeEmojiPicker() {
        emojiPicker.hidden = true;
        emojiTrigger.setAttribute("aria-expanded", "false");
      }

      emojiTrigger.addEventListener("click", () => {
        const opening = emojiPicker.hidden;
        emojiPicker.hidden = !opening;
        emojiTrigger.setAttribute("aria-expanded", String(opening));
        if (opening) {
          emojiSelectionStart = input.selectionStart;
          emojiSelectionEnd = input.selectionEnd;
        } else {
          input.focus();
        }
      });

      emojiPicker.addEventListener("mousedown", (event) => {
        event.preventDefault();
      });

      emojiPicker.querySelectorAll(".emoji-option").forEach((emojiButton) => {
        emojiButton.addEventListener("click", () => {
          const emoji = emojiButton.textContent;
          const value = input.value;
          input.value =
            value.slice(0, emojiSelectionStart) +
            emoji +
            value.slice(emojiSelectionEnd);
          const cursor = emojiSelectionStart + emoji.length;
          input.setSelectionRange(cursor, cursor);
          input.dispatchEvent(new Event("input", { bubbles: true }));
          closeEmojiPicker();
          input.focus();
        });
      });

      document.addEventListener("click", (event) => {
        if (!form.contains(event.target)) closeEmojiPicker();
      });

      document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && !emojiPicker.hidden) closeEmojiPicker();
      });

      const ownerChatDialog = document.getElementById("owner-connect-dialog");
      const connectionState = document.getElementById("connection-state");
      const connectionRequestForm = document.getElementById("connection-request-form");
      const connectionRequestMessage = document.getElementById("connection-request-message");
      const connectionMessageForm = document.getElementById("connection-message-form");
      const connectionMessage = document.getElementById("connection-message");
      const connectionTranscript = document.getElementById("connection-transcript");
      let connectionPollTimer = null;
      let connectionLastMessageId = 0;
      let connectionSessionId = "";
      let connectionMessages = [];
      async function refreshOwnerPresence() {
        try {
          const response = await fetch("/v1/presence", { cache: "no-store" });
          if (!response.ok) return;
          const presence = await response.json();
          document.querySelectorAll("[data-owner-online]").forEach((badge) => {
            badge.hidden = !presence.online;
          });
        } catch (_) {
          document.querySelectorAll("[data-owner-online]").forEach((badge) => {
            badge.hidden = true;
          });
        }
      }

      refreshOwnerPresence();
      window.setInterval(refreshOwnerPresence, 15000);

      function connectionApi(path, options = {}) {
        return fetch(path, {
          credentials: "same-origin",
          ...options,
          headers: {
            ...(options.body ? { "Content-Type": "application/json" } : {}),
            ...options.headers,
          },
        }).then(async (response) => {
          const result = await response.json().catch(() => ({}));
          if (!response.ok) {
            throw new Error(result.detail || `Request failed (${response.status})`);
          }
          return result;
        });
      }

      function renderConnectionMessages(messages) {
        for (const message of messages) {
          const bubble = document.createElement("div");
          bubble.className = `connection-bubble${message.sender === "admin" ? " owner" : ""}`;
          bubble.textContent = message.content;
          connectionTranscript.appendChild(bubble);
          connectionMessages.push(message);
          connectionLastMessageId = message.id;
        }
        if (messages.length) connectionTranscript.scrollTop = connectionTranscript.scrollHeight;
      }

      async function refreshOwnerConversation() {
        if (!ownerChatDialog.open) return;
        const connection = await connectionApi("/v1/connect/status");
        if (connection.session_id) {
          localStorage.setItem("iris_adarsh_chat_session", connection.session_id);
          if (connectionSessionId !== connection.session_id) {
            connectionSessionId = connection.session_id;
            connectionLastMessageId = 0;
            connectionMessages = [];
            connectionTranscript.replaceChildren();
          }
        }
        connectionRequestForm.hidden =
          connection.status === "pending" || connection.status === "approved";
        connectionMessageForm.hidden = connection.status !== "approved";
        connectionTranscript.hidden = connection.status === "not_requested";
        if (connection.status === "not_requested") {
          connectionState.textContent = "Send a short message to request a private conversation.";
          return;
        }
        if (connection.status === "pending") {
          connectionState.textContent = "Your request is waiting for Adarsh to review it.";
        } else if (connection.status === "approved") {
          connectionState.textContent = "Your private conversation is approved. Messages are only visible to you and Adarsh.";
        } else if (connection.status === "declined") {
          connectionState.textContent = "This request was declined. You can send another request below.";
        } else {
          connectionState.textContent = "This conversation is closed. You can request a new conversation below.";
        }
        const sessionId = localStorage.getItem("iris_adarsh_chat_session");
        const requestedAfterId = connectionLastMessageId;
        const params = new URLSearchParams({ after_id: String(requestedAfterId) });
        if (sessionId) params.set("session_id", sessionId);
        try {
          const messages = await connectionApi(
            `/api/adarsh-chat/history?${params.toString()}`,
          );
          renderConnectionMessages(messages);
          if (messages.length || requestedAfterId === 0) {
            localStorage.setItem(
              `iris-adarsh-chat-cache:${sessionId || connection.id}`,
              JSON.stringify({ savedAt: Date.now(), messages: connectionMessages }),
            );
          }
        } catch (error) {
          let cached = null;
          try {
            cached = JSON.parse(
              localStorage.getItem(
                `iris-adarsh-chat-cache:${sessionId || connection.id}`,
              ) || "null",
            );
          } catch (_) {}
          if (cached && Date.now() - cached.savedAt < CACHE_TTL) {
            connectionTranscript.replaceChildren();
            connectionLastMessageId = 0;
            connectionMessages = [];
            renderConnectionMessages(cached.messages);
            connectionState.textContent = "Showing a recent local copy; server history is unavailable.";
          } else {
            throw error;
          }
        }
      }

      function openOwnerChat() {
        if (!ownerChatDialog.open) ownerChatDialog.showModal();
        refreshOwnerConversation().catch((error) => {
          connectionState.textContent = error.message;
        });
        window.clearInterval(connectionPollTimer);
        connectionPollTimer = window.setInterval(() => {
          refreshOwnerConversation().catch((error) => {
            connectionState.textContent = error.message;
          });
        }, 4000);
      }

      document.querySelectorAll("[data-open-owner-chat]").forEach((openButton) => {
        openButton.addEventListener("click", openOwnerChat);
      });
      document.getElementById("close-owner-chat").addEventListener("click", () => {
        ownerChatDialog.close();
      });
      ownerChatDialog.addEventListener("close", () => {
        window.clearInterval(connectionPollTimer);
        connectionPollTimer = null;
      });

      connectionRequestForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        const requestButton = connectionRequestForm.querySelector("button");
        requestButton.disabled = true;
        connectionState.textContent = "Sending your request…";
        try {
          const created = await connectionApi("/v1/connect/requests", {
            method: "POST",
            body: JSON.stringify({
              display_name: document.getElementById("visitor-name").value.trim(),
              message: connectionRequestMessage.value.trim(),
            }),
          });
          if (created.session_id) {
            localStorage.setItem("iris_adarsh_chat_session", created.session_id);
            connectionSessionId = created.session_id;
          }
          connectionLastMessageId = 0;
          connectionMessages = [];
          connectionTranscript.replaceChildren();
          connectionRequestMessage.value = "";
          await refreshOwnerConversation();
        } catch (error) {
          connectionState.textContent = error.message;
        } finally {
          requestButton.disabled = false;
        }
      });

      connectionMessageForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        const message = connectionMessage.value.trim();
        if (!message) return;
        const sendButton = connectionMessageForm.querySelector("button");
        sendButton.disabled = true;
        try {
          await connectionApi("/v1/connect/messages", {
            method: "POST",
            body: JSON.stringify({ message }),
          });
          connectionMessage.value = "";
          await refreshOwnerConversation();
        } catch (error) {
          connectionState.textContent = error.message;
        } finally {
          sendButton.disabled = false;
        }
      });

      initializeChats();
    </script>
  </body>
</html>"""
