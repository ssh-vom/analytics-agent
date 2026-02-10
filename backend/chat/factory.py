from __future__ import annotations

import os

try:
    from backend.chat.adapters.gemini_adapter import GeminiAdapter
    from backend.chat.adapters.openai_adapter import OpenAiAdapter
    from backend.chat.adapters.openrouter_adapter import OpenRouterAdapter
    from backend.chat.llm_client import LlmClient
    from backend.env_loader import load_env_once
except ModuleNotFoundError:
    from chat.adapters.gemini_adapter import GeminiAdapter
    from chat.adapters.openai_adapter import OpenAiAdapter
    from chat.adapters.openrouter_adapter import OpenRouterAdapter
    from chat.llm_client import LlmClient
    from env_loader import load_env_once


def build_llm_client(
    *,
    provider: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
) -> LlmClient:
    load_env_once()

    resolved_provider = (
        (provider or os.getenv("LLM_PROVIDER", "openrouter")).lower().strip()
    )
    if resolved_provider == "openai":
        return OpenAiAdapter(
            model=model or os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            api_key=api_key or os.getenv("OPENAI_API_KEY"),
        )
    if resolved_provider == "gemini":
        return GeminiAdapter(
            model=model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            api_key=api_key
            or os.getenv("GEMINI_API_KEY")
            or os.getenv("GOOGLE_API_KEY"),
        )
    if resolved_provider == "openrouter":
        return OpenRouterAdapter(
            model=model or os.getenv("OPENROUTER_MODEL", "openrouter/pony-alpha"),
            api_key=api_key
            or os.getenv("OPENROUTER_API_KEY")
            or os.getenv("OPENROUTER_KEY"),
            app_name=os.getenv("OPENROUTER_APP_NAME", "TextQL"),
            http_referer=os.getenv("OPENROUTER_HTTP_REFERER"),
        )

    raise ValueError(f"Unsupported LLM provider: {resolved_provider}")
