# ReTell LiveKit agent

Python LiveKit Agents worker for ReTell: STT/LLM/TTS, SIP, RAG against the dashboard Postgres, and conversation-flow execution.

Run this alongside `../Dailer-Desk` on port 3000.

## Prerequisites

- Python 3.12+ (CI uses 3.12; Docker uses 3.13)
- [uv](https://docs.astral.sh/uv/)
- Dashboard running locally (or reachable via `NEXTJS_API_URL`)
- The same LiveKit Cloud project the dashboard uses

## Setup

```bash
cp .env.example .env.local
# set DATABASE_URL, GEMINI_API_KEY, LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET,
# AGENT_SERVICE_SECRET (must match the dashboard), and NEXTJS_API_URL

uv sync
uv run src/agent.py download-files
uv run src/agent.py dev
```

`LIVEKIT_AGENT_NAME` must match `NEXT_PUBLIC_LIVEKIT_AGENT_NAME` on the dashboard (default `my-python-agent`). Empty `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` causes the process to exit at startup.

The worker registers with LiveKit using `LIVEKIT_URL` + API key/secret. When a job includes `org_id`, it fetches that org’s inference credentials from:

```
GET {NEXTJS_API_URL}/api/internal/livekit-credentials?orgId=...
X-Agent-Service-Secret: ...
```

See [docs/livekit-credentials.md](docs/livekit-credentials.md) if STT/TTS return 401.

## Other commands

```bash
uv run src/agent.py console   # terminal try-out without LiveKit Cloud
uv run src/agent.py start     # production worker (also the Docker CMD)
```

The Twilio SIP webhook (`python src/webhook_server.py`, port 5000) is only needed for inbound PSTN. Browser voice does not require it.
