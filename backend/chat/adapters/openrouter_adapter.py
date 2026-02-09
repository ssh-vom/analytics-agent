from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

try:
    from backend.chat.llm_client import ChatMessage, LlmResponse, ToolCall, ToolDefinition
except ModuleNotFoundError:
    from chat.llm_client import ChatMessage, LlmResponse, ToolCall, ToolDefinition


@dataclass(frozen=True)
class OpenRouterAdapter:
    model: str
    api_key: str | None = None
    base_url: str = "https://openrouter.ai/api/v1"
    app_name: str = "TextQL"
    http_referer: str | None = None

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
            from openai import OpenAI
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "OpenAI SDK not installed. Add dependency: `openai`."
            ) from exc

        if not self.api_key:
            raise RuntimeError("OpenRouter API key is missing. Set OPENROUTER_API_KEY.")

        extra_headers: dict[str, str] = {"X-Title": self.app_name}
        if self.http_referer:
            extra_headers["HTTP-Referer"] = self.http_referer

        client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            default_headers=extra_headers,
        )

        response = client.chat.completions.create(
            model=self.model,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                }
                for t in tools
            ],
            tool_choice=tool_choice or "auto",
            max_tokens=max_output_tokens,
        )
        return self._parse_response(response)

    def _parse_response(self, response: Any) -> LlmResponse:
        choices = getattr(response, "choices", []) or []
        if not choices:
            return LlmResponse(text=None, tool_calls=[], raw=response)

        message = getattr(choices[0], "message", None)
        if message is None:
            return LlmResponse(text=None, tool_calls=[], raw=response)

        text = getattr(message, "content", None)
        if isinstance(text, list):
            text = "".join(str(part) for part in text if part is not None)

        tool_calls: list[ToolCall] = []
        raw_tool_calls = getattr(message, "tool_calls", []) or []
        for raw_call in raw_tool_calls:
            function = getattr(raw_call, "function", None)
            name = getattr(function, "name", "") if function is not None else ""
            arguments_raw = (
                getattr(function, "arguments", "{}") if function is not None else "{}"
            )
            try:
                arguments = json.loads(arguments_raw) if arguments_raw else {}
            except json.JSONDecodeError:
                arguments = {"_raw": str(arguments_raw)}

            call_id = getattr(raw_call, "id", None) or f"call_{len(tool_calls) + 1}"
            tool_calls.append(
                ToolCall(
                    id=call_id,
                    name=name,
                    arguments=arguments,
                )
            )

        normalized_text = str(text).strip() if text else None
        return LlmResponse(text=normalized_text or None, tool_calls=tool_calls, raw=response)
