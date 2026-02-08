from __future__ import annotations

import os

try:
    from backend.chat.adapters.gemini_adapter import GeminiAdapter
    from backend.chat.adapters.openai_adapter import OpenAiAdapter
    from backend.chat.llm_client import LlmClient
except ModuleNotFoundError:
    from chat.adapters.gemini_adapter import GeminiAdapter
    from chat.adapters.openai_adapter import OpenAiAdapter
    from chat.llm_client import LlmClient


def build_llm_client(
    *,
    provider: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
) -> LlmClient:
    resolved_provider = (provider or os.getenv("LLM_PROVIDER", "openai")).lower().strip()
    if resolved_provider == "openai":
        return OpenAiAdapter(
            model=model or os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            api_key=api_key or os.getenv("OPENAI_API_KEY"),
        )
    if resolved_provider == "gemini":
        return GeminiAdapter(
            model=model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            api_key=api_key or os.getenv("GEMINI_API_KEY"),
        )

    raise ValueError(f"Unsupported LLM provider: {resolved_provider}")
