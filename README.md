---
title: IRIS Autonomous AI Assistant
emoji: ⚡
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# IRIS (Intelligent Routing & Iterative Synthesis)

Autonomous software engineering agent and recursive AI assistant created by **Adarsh Dwivedi**.

- **Fast-Path Conversation:** Groq LPU streaming (<400ms TTFT)
- **Deep Research:** Multi-source web verification & evidence extractor
- **Self-Development:** Sandboxed mutation engine with approval gates

## Deploying on Render

Deploy this repository as a Docker web service. The container listens on Render's
`PORT` environment variable (and defaults to port `7860` outside Render). Set
`GROQ_API_KEY` in the Render service's environment settings to enable chat. After
deployment, open the service URL to use the web chat; `/healthz` provides a
health check. Keep API keys in Render environment settings rather than committing
them to the repository.

The web chat sends conversational requests through the IRIS synthesizer in
forced-council mode, which queries all configured providers concurrently and
returns a single synthesized answer. Configure `GROQ_API_KEY`,
`GEMINI_API_KEY`, and `OPENROUTER_API_KEY` in Render to enable those providers.
Use `@gemini <prompt>` or `@openrouter <prompt>` to route a request directly to
one provider.

### Private owner conversations

Visitors can choose **Chat with Adarsh** to send a private conversation request.
Requests appear in the owner inbox at `/admin`; visitors cannot exchange messages
with the owner until a request is approved. The inbox uses an HTTP-only admin
session and is separate from `IRIS_ADMIN_KEY`.

To enable it on Render:

1. Set `IRIS_ADMIN_PASSWORD` to a unique, randomly generated password of at least
   16 characters. Do not reuse an AI provider key or commit this value.
2. Attach a persistent disk to the service, mounted at `/var/data`.
3. Set `IRIS_DATA_DIR` to `/var/data` and redeploy.

Connection requests and messages are stored in `live_connections.sqlite3` on
that disk and remain there across deploys. The owner can permanently delete a
conversation and its messages from the inbox. Without the password or writable
persistent storage, connection requests are unavailable; the regular IRIS chat
continues to work.
