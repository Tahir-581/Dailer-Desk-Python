# Dailer Desk: LiveKit Voice Agent Worker

The Python real-time voice worker behind [Dailer Desk](https://github.com/Tahir-581/Dailer-Desk). It runs phone and browser calls on [LiveKit Agents](https://docs.livekit.io/agents/): a streaming STT -> LLM -> TTS pipeline, RAG over each organization's knowledge base, node-based conversation flows, tool calls, and SIP telephony.

## Features

- **Voice pipeline.** LiveKit Agents `AgentSession` using LiveKit Inference. The defaults are Deepgram Nova-3 STT and GPT-4.1-mini, and the STT/LLM/TTS models are configurable per assistant. Includes Silero VAD, noise cancellation and "thinking" background sounds.
- **RAG.** LlamaIndex with a Postgres `PGVectorStore`, plus custom Gemini embedding and LLM wrappers (`src/rag_service.py`), scoped per organization.
- **Conversation flows.** A workflow engine (`src/workflows/`) loads flow graphs from the dashboard and runs conversation, logic split, extract-variable, press-digit, SMS, call-transfer, agent-transfer and end-call nodes.
- **Tools.** Call transfer, outbound calls, knowledge-base lookup, and user-defined HTTP functions with JSON-path response extraction (`src/tools/`).
- **SIP telephony.** Trunk management, inbound/outbound dispatch, session tracking, DTMF, and LiveKit Egress recording (`src/sip/`). An optional Flask webhook server (`src/webhook_server.py`) handles Twilio inbound calls and SMS.

## Stack

Python 3.12+ · LiveKit Agents · LiveKit Inference · LlamaIndex · PostgreSQL + pgvector · Google Gemini · Flask · Twilio · uv · Docker

## Getting started

```bash
cp .env.example .env.local    # LiveKit keys, DATABASE_URL, GEMINI_API_KEY, NEXTJS_API_URL, ...
uv sync
uv run src/agent.py download-files
uv run src/agent.py dev       # connect to LiveKit and wait for jobs
```

Other modes:

```bash
uv run src/agent.py console   # talk to the agent in your terminal
uv run src/agent.py start     # production worker (Docker CMD)
python src/webhook_server.py  # Twilio inbound webhook on :5000 (PSTN only)
```

`LIVEKIT_AGENT_NAME` must match `NEXT_PUBLIC_LIVEKIT_AGENT_NAME` in the dashboard. The worker loads assistant tools and workflows from the dashboard API at `NEXTJS_API_URL`.

## Project structure

```
src/
  agent.py            entrypoint: session setup, model selection, events, metrics
  rag_service.py      LlamaIndex + pgvector retrieval
  gemini_*.py         Gemini embedding / LLM adapters for LlamaIndex
  workflows/          flow engine and node types
  tools/              function tools and custom HTTP function executor
  sip/                SIP config, trunks, dispatcher, sessions, egress
  webhook_server.py   Twilio webhook (Flask)
Dockerfile, taskfile.yaml, pyproject.toml
```

## License

MIT. Parts of the project scaffold come from the LiveKit agent starter template (MIT, LiveKit, Inc.).
