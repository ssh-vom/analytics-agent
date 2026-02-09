from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

try:
    from backend.chat.llm_client import ChatMessage, LlmResponse, ToolCall, ToolDefinition
except ModuleNotFoundError:
    from chat.llm_client import ChatMessage, LlmResponse, ToolCall, ToolDefinition


_UNSUPPORTED_SCHEMA_KEYS = {
    "additionalProperties",
    "additional_properties",
}


def _to_gemini_role(role: str) -> str:
    normalized = (role or "").strip().lower()
    if normalized == "assistant":
        return "model"
    if normalized in {"model", "user"}:
        return normalized
    return "user"


def _sanitize_schema(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, inner in value.items():
            if key in _UNSUPPORTED_SCHEMA_KEYS:
                continue
            sanitized[key] = _sanitize_schema(inner)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_schema(item) for item in value]
    return value


@dataclass(frozen=True)
class GeminiAdapter:
    model: str
    api_key: str | None = None

    async def generate(
        self,
        *,
        messages: list[ChatMessage],
        tools: list[ToolDefinition],
        tool_choice: str | None = None,
        max_output_tokens: int | None = None,
    ) -> LlmResponse:
        return await asyncio.to_thread(
            self._generate_sync,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            max_output_tokens=max_output_tokens,
        )

    def _generate_sync(
        self,
        *,
        messages: list[ChatMessage],
        tools: list[ToolDefinition],
        tool_choice: str | None,
        max_output_tokens: int | None,
    ) -> LlmResponse:
        try:
            from google import genai
            from google.genai import types as genai_types
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Gemini SDK not installed. Add dependency: `google-genai`."
            ) from exc

        if not self.api_key:
            raise RuntimeError(
                "Gemini API key is missing. Set GEMINI_API_KEY or GOOGLE_API_KEY."
            )

        client = genai.Client(api_key=self.api_key)
        function_declarations = [
            genai_types.FunctionDeclaration(
                name=t.name,
                description=t.description,
                parameters=_sanitize_schema(t.input_schema),
            )
            for t in tools
        ]
        config_kwargs: dict[str, Any] = {
            "tools": [genai_types.Tool(function_declarations=function_declarations)]
        }
        if max_output_tokens is not None:
            config_kwargs["max_output_tokens"] = max_output_tokens
        if tool_choice is not None:
            # Keep this permissive; provider-specific options may vary by SDK version.
            config_kwargs["tool_choice"] = tool_choice

        response = client.models.generate_content(
            model=self.model,
            contents=[
                {"role": _to_gemini_role(m.role), "parts": [{"text": m.content}]}
                for m in messages
            ],
            config=genai_types.GenerateContentConfig(**config_kwargs),
        )
        return self._parse_response(response)

    def _parse_response(self, response: Any) -> LlmResponse:
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        candidates = getattr(response, "candidates", []) or []
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            parts = getattr(content, "parts", []) if content is not None else []
            for part in parts or []:
                function_call = getattr(part, "function_call", None)
                if function_call is not None:
                    name = getattr(function_call, "name", "") or ""
                    raw_args = getattr(function_call, "args", {}) or {}
                    if isinstance(raw_args, str):
                        try:
                            arguments = json.loads(raw_args)
                        except json.JSONDecodeError:
                            arguments = {"_raw": raw_args}
                    else:
                        arguments = dict(raw_args)
                    tool_calls.append(
                        ToolCall(
                            id=f"call_{len(tool_calls) + 1}",
                            name=name,
                            arguments=arguments,
                        )
                    )
                    continue

                text_value = getattr(part, "text", None)
                if text_value:
                    text_parts.append(text_value)

        text = "".join(text_parts).strip() or None
        return LlmResponse(text=text, tool_calls=tool_calls, raw=response)
