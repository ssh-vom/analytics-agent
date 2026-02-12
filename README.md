# AnalyticZ

<img width="1705" height="991" alt="image" src="https://github.com/user-attachments/assets/02443af1-5788-41e4-9761-226effb11328" />


Analytics agent with SQL/Python tool execution and worldline branching.

## Refactor Status (Phase 9)

- Phases 0-8 are complete (security hardening, atomic event store, fork-boundary state restore, scheduler/runtime consolidation, backend/frontend decomposition, and worldline API efficiency).
- Phase 9 completed final hardening: full test/build verification, dead-code cleanup, and docs/LOC closure.
- Background chat turn scheduling is now part of the main product path (`/api/chat/jobs`).
- Worldlines UI now uses a summary API to avoid N+1 fetches:
  - `GET /api/threads/{thread_id}/worldline-summaries`

## Quick Start

**Prerequisites:** Docker, [uv](https://docs.astral.sh/uv/), Node.js/npm

```bash
# Build everything and run backend + frontend
make dev
```

- **Frontend:** http://127.0.0.1:5173
- **Backend API:** http://127.0.0.1:8000

### Alternative: script directly

```bash
./scripts/dev.sh          # Full build + run
./scripts/dev.sh --no-build   # Run only (after initial build)
```

### Manual steps

```bash
# 1. Build Docker sandbox (required for Python execution)
docker build -t textql-sandbox:py311 backend/sandbox/runner

# 2. Backend
cd backend && uv sync && uv run uvicorn main:app --reload --port 8000

# 3. Frontend (in another terminal)
cd frontend && npm install && npm run dev
```

## Environment

Set at least one LLM API key:

- `OPENROUTER_API_KEY` (default provider)
- `OPENAI_API_KEY`

## Useful API Endpoints

- `POST /api/chat/jobs`: queue a background chat turn.
- `GET /api/chat/jobs`: list background chat jobs (optionally filtered).
- `POST /api/chat/jobs/{job_id}/ack`: acknowledge a completed/failed job.
- `GET /api/threads/{thread_id}/worldline-summaries`: fetch worldline metadata (message counts, activity, and job summary) in one request.
