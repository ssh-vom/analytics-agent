# Backend Notes

## Current refactor state

- Event writes now use a shared atomic append-and-advance-head primitive.
- Branch creation restores DuckDB state at the fork boundary (`from_event_id`) with deterministic fallback behavior.
- Chat runtime orchestration is shared across immediate, streaming, and queued job execution paths.
- Worldline summaries are available via:
  - `GET /api/threads/{thread_id}/worldline-summaries`
  - includes message counts, last activity, and per-status job counts.

## Sandbox image (required)

Build the Python sandbox image before running tools:

```bash
docker build -t textql-sandbox:py311 backend/sandbox/runner
```

This image includes `numpy`, `pandas`, and `matplotlib`.

You can override the image with:

```bash
export SANDBOX_IMAGE=my-image:tag
```

## LLM providers

Set one of:

- `OPENAI_API_KEY`
- `GEMINI_API_KEY` (or `GOOGLE_API_KEY`)
- `OPENROUTER_API_KEY`

Optional OpenRouter config:

- `OPENROUTER_MODEL` (default: `openrouter/auto`)
- `OPENROUTER_APP_NAME` (default: `TextQL`)
- `OPENROUTER_HTTP_REFERER`

## Demo DuckDB generator

Generate a deterministic finance DuckDB file for connector testing:

```bash
uv run python backend/scripts/generate_finance_demo_db.py --overwrite
```

Default output is `backend/data/demo/finance_demo.duckdb`.
