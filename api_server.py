"""Public FastAPI gateway for IRIS.

Run with ``uvicorn api_server:app`` after installing ``requirements.txt``.
Set IRIS_ADMIN_KEY and configure IRIS_CORS_ORIGINS before public deployment.
Use IRIS_RATE_LIMIT_STORAGE_URI for shared limits across multiple workers, and
trust forwarded client IP headers only from the known reverse proxy.
The public execution endpoint requires a local Docker daemon and a preloaded
IRIS_SANDBOX_IMAGE; it fails closed when that sandbox is unavailable.
"""

import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any, Iterator, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

load_dotenv(Path(__file__).resolve().parent / ".env")

from core.executor import IRISExecutor
from core.router import provider_response_failed
from core.synthesizer import AISynthesizer
from tools.manager import ToolManager
from tools.web_tool import search_web

logger = logging.getLogger("iris.api")

MAX_CODE_LENGTH = 20_000
MAX_CAPTURE_BYTES = 256_000
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
    allow_headers=["Content-Type", "X-IRIS-ADMIN-KEY"],
)

synthesizer = AISynthesizer()


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
    history: list[ChatTurn] = Field(default_factory=list, max_length=10)

    @field_validator("prompt")
    @classmethod
    def prompt_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("prompt must not be blank")
        return value


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


@app.get("/healthz")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/providers")
@public_rate_limit
def provider_status(request: Request) -> dict[str, list[dict[str, Any]]]:
    providers = synthesizer.router.providers
    return {
        "providers": [
            {
                "name": name,
                "available": bool(getattr(provider, "available", False)),
            }
            for name, provider in providers.items()
        ]
    }


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
                        {"error": f"Add a prompt after @{provider_name}."},
                    )
                    return
                if not getattr(provider, "available", False):
                    yield _sse_event(
                        "error",
                        {"error": f"{provider_name.capitalize()} is not configured on this server."},
                    )
                    return

                yield _sse_event(
                    "status",
                    {"message": f"Sending directly to {provider_name.capitalize()}…"},
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
                                f"{provider_name.capitalize()} could not return "
                                "a usable answer."
                            )
                        },
                    )
                    return
                route_info = {
                    "providers": [provider_name],
                    "synthesized_by": None,
                    "mode": "direct",
                }
                yield _sse_event(
                    "token",
                    {"token": str(answer).strip(), "council": route_info},
                )
                yield _sse_event("council", route_info)
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
                route_info = {
                    "providers": participants,
                    "synthesized_by": result.get("synthesized_by"),
                    "mode": "council",
                }
                yield _sse_event(
                    "token",
                    {"token": answer, "council": route_info},
                )
                yield _sse_event("council", route_info)
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
    <meta name="description" content="IRIS is your AI workspace for questions, research, writing, and building.">
    <title>IRIS — Your AI workspace</title>
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
      body {
        margin: 0;
        min-height: 100vh;
        background:
          radial-gradient(ellipse at 74% 0%, #17132a 0, transparent 35rem),
          #090b12;
      }
      button, textarea { font: inherit; }
      button { color: inherit; }
      .app {
        min-height: 100vh;
        display: grid;
        grid-template-columns: 252px minmax(0, 1fr);
      }
      aside {
        display: flex;
        flex-direction: column;
        padding: 24px 16px 18px;
        border-right: 1px solid var(--line);
        background: #0c0e16d9;
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
        min-height: 100vh;
        display: flex;
        flex-direction: column;
        position: relative;
      }
      .topbar {
        min-height: 70px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 0 clamp(20px, 5vw, 64px);
        border-bottom: 1px solid #20222d;
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
        width: min(100%, 900px);
        flex: 1;
        display: flex;
        flex-direction: column;
        margin: 0 auto;
        padding: 0 28px;
      }
      #welcome {
        flex: 1;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        padding: 42px 0 32px;
        text-align: center;
      }
      #welcome[hidden] { display: none; }
      .welcome-mark {
        display: grid;
        width: 58px;
        height: 58px;
        place-items: center;
        margin-bottom: 22px;
        border: 1px solid #ad94fc66;
        border-radius: 20px;
        background: radial-gradient(circle at 30% 20%, #bba8ff, #7656cd 72%);
        box-shadow: 0 12px 44px #7456d442;
        color: white;
        font-size: 28px;
        font-weight: 750;
      }
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
        border: 1px solid #292b39;
        border-radius: 13px;
        background: #11131cbd;
        text-align: left;
        cursor: pointer;
        transition: border-color .18s, background .18s, transform .18s;
      }
      .suggestion:hover {
        transform: translateY(-2px);
        border-color: #6957a3;
        background: #171523;
      }
      .suggestion-icon { color: #b9a3ff; font-size: 17px; line-height: 1; }
      .suggestion strong { display: block; color: #e2e0ec; font-size: 12px; font-weight: 600; }
      .suggestion small { display: block; margin-top: 5px; color: #898b9e; font-size: 11px; }
      #chat {
        flex: 1;
        min-height: 0;
        overflow-y: auto;
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
      .assistant .msg { border: 1px solid #292b39; border-top-left-radius: 5px; background: #141620; }
      .user .msg { border: 1px solid #6955a0; border-top-right-radius: 5px; background: #403267; }
      .error .msg { border-color: #783e49; background: #3a222b; color: #ffd0d0; }
      .council-meta {
        flex-basis: 100%;
        margin: -14px 0 0 41px;
        color: #85879a;
        font-size: 10px;
      }
      .composer-wrap { padding: 12px 0 20px; }
      form {
        display: flex;
        align-items: flex-end;
        gap: 12px;
        padding: 11px 11px 11px 16px;
        border: 1px solid #343341;
        border-radius: 17px;
        background: #14151f;
        box-shadow: 0 12px 42px #0003;
        transition: border-color .18s, box-shadow .18s;
      }
      form:focus-within {
        border-color: #7962ba;
        box-shadow: 0 0 0 3px #806bd51a, 0 12px 42px #0003;
      }
      textarea {
        flex: 1;
        min-width: 0;
        max-height: 180px;
        min-height: 27px;
        resize: none;
        border: 0;
        outline: 0;
        padding: 5px 0;
        color: inherit;
        background: transparent;
        line-height: 1.55;
      }
      textarea::placeholder { color: #77798d; }
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
        main { min-height: 100dvh; }
        .topbar { min-height: 60px; padding: 0 18px; }
        .content { padding: 0 18px; }
        #welcome { padding: 34px 0 24px; }
        .welcome-copy { font-size: 14px; }
      }
      @media (max-width: 440px) {
        .suggestions { grid-template-columns: 1fr; }
        .suggestion { min-height: 58px; }
        #chat { gap: 16px; padding-top: 24px; }
        .msg { max-width: 88%; font-size: 13px; }
        .composer-wrap { padding-bottom: max(14px, env(safe-area-inset-bottom)); }
      }
    </style>
  </head>
  <body>
    <div class="app">
      <aside aria-label="IRIS workspace">
        <div class="brand">
          <div class="brand-mark" aria-hidden="true">i</div>
          <div><div class="brand-name">IRIS</div><div class="brand-caption">Your AI workspace</div></div>
        </div>
        <button class="new-chat" id="new-chat" type="button"><span aria-hidden="true">＋</span> New conversation</button>
        <div class="side-label">Made for momentum</div>
        <div class="side-note"><span aria-hidden="true">✳</span><span>Explore ideas and get clear, useful answers.</span></div>
        <div class="side-note"><span aria-hidden="true">⌘</span><span>Research, write, plan, and build in one place.</span></div>
        <div class="side-note"><span aria-hidden="true">↗</span><span>Ask a follow-up whenever you need more.</span></div>
        <div class="side-footer"><strong>IRIS</strong> · Intelligent Routing &amp; Iterative Synthesis<br>Created by Adarsh Dwivedi</div>
      </aside>
      <main>
        <header class="topbar">
          <div class="topbar-title">A little more clarity, one question at a time.</div>
          <div class="status" id="provider-status" title="Availability shows whether provider credentials are configured; each model is verified when used.">
            <span class="status-dot" aria-hidden="true"></span>
            <span id="provider-status-text">Checking council…</span>
          </div>
        </header>
        <div class="content">
          <section id="welcome" aria-labelledby="welcome-title">
            <div class="welcome-mark" aria-hidden="true">i</div>
            <p class="eyebrow">Your ideas start here</p>
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
            <form id="prompt-form">
              <textarea id="prompt" rows="1" maxlength="8000" aria-label="Your message" placeholder="Message IRIS…" required></textarea>
              <button id="send" type="submit" aria-label="Send message">
                <svg viewBox="0 0 24 24" width="19" height="19" fill="none" aria-hidden="true"><path d="M12 19V5m0 0-6 6m6-6 6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
              </button>
            </form>
            <p class="composer-hint">IRIS can make mistakes. Check important information.</p>
          </div>
        </div>
      </main>
    </div>
    <script>
      const chat = document.getElementById("chat");
      const form = document.getElementById("prompt-form");
      const input = document.getElementById("prompt");
      const button = document.getElementById("send");
      const welcome = document.getElementById("welcome");
      const newChatButton = document.getElementById("new-chat");
      const providerStatus = document.getElementById("provider-status");
      const providerStatusText = document.getElementById("provider-status-text");
      const conversationHistory = [];
      let activeRequest = null;

      fetch("/v1/providers")
        .then((response) => {
          if (!response.ok) throw new Error(`Status request failed (${response.status})`);
          return response.json();
        })
        .then(({ providers }) => {
          const configured = providers
            .filter((provider) => provider.available)
            .map((provider) => provider.name.toUpperCase());
          providerStatusText.textContent = configured.length
            ? `Council configured · ${configured.join(" + ")}`
            : "No council providers configured";
          if (!configured.length) providerStatus.classList.add("unavailable");
        })
        .catch(() => {
          providerStatusText.textContent = "Council status unavailable";
          providerStatus.classList.add("unavailable");
        });

      function addMessage(text, kind) {
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
        row.appendChild(message);
        chat.appendChild(row);
        row.scrollIntoView({ behavior: "smooth", block: "end" });
        return message;
      }

      function showCouncilMeta(answer, councilInfo) {
        const participants = (councilInfo.providers || [])
          .map((name) => name.toUpperCase());
        if (answer.parentElement.querySelector(".council-meta")) return;
        const judge = councilInfo.synthesized_by
          ? ` · synthesized by ${councilInfo.synthesized_by.toUpperCase()}`
          : "";
        const label = councilInfo.mode === "direct"
          ? `Direct · ${participants.join(", ")}`
          : participants.length > 1
            ? `IRIS Council · ${participants.join(" + ")}${judge}`
            : `Single model · ${participants.join("")} · no multi-model consensus`;
        const meta = document.createElement("span");
        meta.className = "council-meta";
        meta.textContent = label;
        answer.parentElement.appendChild(meta);
      }

      newChatButton.addEventListener("click", () => {
        if (activeRequest) activeRequest.abort();
        activeRequest = null;
        button.disabled = false;
        chat.replaceChildren();
        conversationHistory.length = 0;
        welcome.hidden = false;
        input.value = "";
        input.focus();
      });

      document.querySelectorAll("[data-prompt]").forEach((suggestion) => {
        suggestion.addEventListener("click", () => {
          input.value = suggestion.dataset.prompt;
          form.requestSubmit();
        });
      });

      input.addEventListener("input", () => {
        input.style.height = "auto";
        input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
      });

      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey) {
          event.preventDefault();
          form.requestSubmit();
        }
      });

      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const prompt = input.value.trim();
        if (!prompt || button.disabled) return;

        welcome.hidden = true;
        addMessage(prompt, "user");
        input.value = "";
        input.style.height = "auto";
        button.disabled = true;
        newChatButton.disabled = true;
        const controller = new AbortController();
        activeRequest = controller;
        const answer = addMessage("", "assistant");
        let hasToken = false;
        let historyStored = false;
        let streamError = "";
        try {
          const response = await fetch("/v1/chat/stream", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              prompt,
              history: conversationHistory.slice(-10),
            }),
            signal: controller.signal,
          });
          if (!response.ok) {
            let detail = `Request failed (${response.status})`;
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
              if (!historyStored) {
                conversationHistory.push(
                  { role: "user", content: prompt },
                  { role: "assistant", content: "" },
                );
                historyStored = true;
              }
              conversationHistory[conversationHistory.length - 1].content =
                answer.textContent;
              if (conversationHistory.length > 10) {
                conversationHistory.splice(0, conversationHistory.length - 10);
              }
              chat.scrollTop = chat.scrollHeight;
            }
            if (payload.error) streamError = payload.error;
            if (payload.council) {
              showCouncilMeta(answer, payload.council);
            }
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
    </script>
  </body>
</html>"""
