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
reports which models responded. Configure `GROQ_API_KEY`, `GEMINI_API_KEY`, and
`OPENROUTER_API_KEY` in Render to enable those providers. `/v1/providers` reports
which provider credentials are configured (not a live upstream connectivity
check). Use `@gemini <prompt>` or `@openrouter <prompt>` to route a request
directly to one provider.
