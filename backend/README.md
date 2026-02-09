# Backend Notes

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
