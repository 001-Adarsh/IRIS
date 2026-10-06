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
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any, Iterator

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
from core.synthesizer import AISynthesizer
from providers.groq_provider import GroqProvider
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

groq_provider = GroqProvider()
synthesizer = AISynthesizer()


class PromptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=8_000)

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


@app.post("/v1/chat/stream")
@public_rate_limit
def stream_chat(request: Request, body: PromptRequest) -> StreamingResponse:
    if not groq_provider.available:
        raise HTTPException(
            status_code=503,
            detail="The Groq provider is not configured.",
        )

    def generate_events() -> Iterator[str]:
        try:
            for token in groq_provider.stream(body.prompt):
                yield _sse_event("token", {"token": token})
            yield _sse_event("done", {"done": True})
        except Exception:
            logger.exception("IRIS chat stream failed")
            yield _sse_event("error", {"error": "Chat generation failed."})

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
    <meta name="theme-color" content="#10141d">
    <title>IRIS — AI Assistant</title>
    <style>
      :root {
        color-scheme: dark;
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        background: #10141d;
        color: #edf1f8;
      }
      * { box-sizing: border-box; }
      body {
        min-height: 100vh;
        margin: 0;
        display: grid;
        place-items: center;
        padding: 24px;
        background: radial-gradient(ellipse at top, #202b42 0, #10141d 55%);
      }
      main {
        width: min(100%, 760px);
        height: min(820px, calc(100vh - 48px));
        min-height: 480px;
        display: flex;
        flex-direction: column;
        overflow: hidden;
        border: 1px solid #30394a;
        border-radius: 20px;
        background: #171d28;
        box-shadow: 0 24px 80px #0006;
      }
      header { padding: 24px 26px 18px; border-bottom: 1px solid #30394a; }
      h1 { margin: 0; font-size: 1.35rem; letter-spacing: .02em; }
      header p { margin: 7px 0 0; color: #aab5c8; font-size: .92rem; }
      #chat {
        flex: 1;
        overflow-y: auto;
        display: flex;
        flex-direction: column;
        gap: 14px;
        padding: 22px 26px;
      }
      .msg {
        max-width: 88%;
        padding: 12px 15px;
        border-radius: 15px;
        line-height: 1.55;
        white-space: pre-wrap;
        overflow-wrap: anywhere;
      }
      .assistant { align-self: flex-start; background: #242d3b; }
      .user { align-self: flex-end; background: #315ac7; }
      .error { color: #ffd0d0; border: 1px solid #783e49; background: #3a222b; }
      form { display: flex; gap: 10px; padding: 16px; border-top: 1px solid #30394a; }
      textarea {
        flex: 1;
        min-width: 0;
        max-height: 150px;
        resize: vertical;
        border: 1px solid #3a4558;
        border-radius: 12px;
        padding: 12px 14px;
        color: inherit;
        background: #111722;
        font: inherit;
      }
      textarea:focus { outline: 2px solid #6487ee; outline-offset: 1px; }
      button {
        align-self: flex-end;
        min-height: 44px;
        border: 0;
        border-radius: 12px;
        padding: 0 18px;
        color: white;
        background: #4169df;
        font: inherit;
        font-weight: 650;
        cursor: pointer;
      }
      button:disabled { cursor: wait; opacity: .6; }
      @media (max-width: 520px) {
        body { padding: 0; }
        main { width: 100%; height: 100vh; min-height: 0; border: 0; border-radius: 0; }
        header { padding: 20px; }
        #chat { padding: 18px; }
        form { padding: 12px; }
        button { padding: 0 14px; }
      }
    </style>
  </head>
  <body>
    <main>
      <header>
        <h1>IRIS</h1>
        <p>Your AI assistant. Ask a question to get started.</p>
      </header>
      <section id="chat" aria-live="polite" aria-label="Conversation">
        <div class="msg assistant">Hello! I’m IRIS. How can I help?</div>
      </section>
      <form id="prompt-form">
        <textarea id="prompt" rows="1" maxlength="8000" aria-label="Your message" placeholder="Message IRIS…" required></textarea>
        <button id="send" type="submit">Send</button>
      </form>
    </main>
    <script>
      const chat = document.getElementById("chat");
      const form = document.getElementById("prompt-form");
      const input = document.getElementById("prompt");
      const button = document.getElementById("send");

      function addMessage(text, kind) {
        const message = document.createElement("div");
        message.className = `msg ${kind}`;
        message.textContent = text;
        chat.appendChild(message);
        chat.scrollTop = chat.scrollHeight;
        return message;
      }

      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const prompt = input.value.trim();
        if (!prompt || button.disabled) return;

        addMessage(prompt, "user");
        input.value = "";
        button.disabled = true;
        const answer = addMessage("", "assistant");
        try {
          const response = await fetch("/v1/chat/stream", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ prompt }),
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
          const decoder = new TextDecoder();
          let pending = "";
          let streamError = "";
          const consumeFrame = (frame) => {
            const data = frame.split(/\\r?\\n/)
              .filter((line) => line.startsWith("data:"))
              .map((line) => line.slice(5).trimStart())
              .join("\\n");
            if (!data) return;
            const payload = JSON.parse(data);
            if (payload.token) {
              answer.textContent += payload.token;
              chat.scrollTop = chat.scrollHeight;
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
        } catch (error) {
          answer.classList.add("error");
          answer.textContent = `Error: ${error.message}`;
        } finally {
          button.disabled = false;
          input.focus();
        }
      });
    </script>
  </body>
</html>"""
