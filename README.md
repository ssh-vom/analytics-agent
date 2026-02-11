# TextQL

Analytics agent with SQL/Python tool execution and worldline branching.

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
- `GEMINI_API_KEY` or `GOOGLE_API_KEY`
