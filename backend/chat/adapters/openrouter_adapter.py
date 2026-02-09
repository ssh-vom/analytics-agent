from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

try:
    from backend.chat.llm_client import (
        ChatMessage,
        LlmResponse,
        StreamChunk,
        ToolCall,
        ToolDefinition,
    )
except ModuleNotFoundError:
    from chat.llm_client import (
        ChatMessage,
        LlmResponse,
        StreamChunk,
        ToolCall,
        ToolDefinition,
    )


@dataclass(frozen=True)
class OpenRouterAdapter:
    model: str
    api_key: str | None = None
    base_url: str = "https://openrouter.ai/api/v1"
    app_name: str = "TextQL"
    http_referer: str | None = None

    # ---- non-streaming (unchanged) ------------------------------------------

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

        api_messages = self._messages_to_api(messages)
        response = client.chat.completions.create(
            model=self.model,
            messages=api_messages,
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

    # ---- streaming ----------------------------------------------------------

    async def generate_stream(
        self,
        *,
        messages: list[ChatMessage],
        tools: list[ToolDefinition],
        tool_choice: str | None = None,
        max_output_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Stream tokens from OpenRouter via the async OpenAI Chat Completions API.

        Uses ``AsyncOpenAI`` with ``stream=True`` to get ``ChatCompletionChunk``
        objects.  Each chunk's ``delta`` may carry:
          - ``.content`` – text token
          - ``.tool_calls`` – incremental tool-call info (index, id, function name/arguments)
        """
        try:
            from openai import AsyncOpenAI
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "OpenAI SDK not installed. Add dependency: `openai`."
            ) from exc

        if not self.api_key:
            raise RuntimeError("OpenRouter API key is missing. Set OPENROUTER_API_KEY.")

        extra_headers: dict[str, str] = {"X-Title": self.app_name}
        if self.http_referer:
            extra_headers["HTTP-Referer"] = self.http_referer

        client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            default_headers=extra_headers,
        )

        api_messages = self._messages_to_api(messages)
        stream = await client.chat.completions.create(
            model=self.model,
            messages=api_messages,
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
            stream=True,
        )

        # Track which tool-call indices we've already sent a "start" for.
        started_tool_calls: dict[int, str] = {}  # index -> call_id

        async for chunk in stream:
            choices = getattr(chunk, "choices", []) or []
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            if delta is None:
                continue

            # --- text content ------------------------------------------------
            content = getattr(delta, "content", None)
            if content:
                yield StreamChunk(type="text", text=content)

            # --- tool calls --------------------------------------------------
            raw_tool_calls = getattr(delta, "tool_calls", None) or []
            for tc in raw_tool_calls:
                index = getattr(tc, "index", 0)
                call_id = getattr(tc, "id", None)
                function = getattr(tc, "function", None)
                fn_name = getattr(function, "name", None) if function else None
                fn_args = getattr(function, "arguments", None) if function else None

                # First chunk for this tool-call index → emit start
                if index not in started_tool_calls:
                    resolved_id = call_id or f"call_{index + 1}"
                    started_tool_calls[index] = resolved_id
                    yield StreamChunk(
                        type="tool_call_start",
                        tool_call_id=resolved_id,
                        tool_name=fn_name or "",
                    )

                resolved_id = started_tool_calls[index]

                # Argument delta
                if fn_args:
                    yield StreamChunk(
                        type="tool_call_delta",
                        tool_call_id=resolved_id,
                        arguments_delta=fn_args,
                    )

        # After the stream ends, emit "done" for all tool calls that started.
        for call_id in started_tool_calls.values():
            yield StreamChunk(
                type="tool_call_done",
                tool_call_id=call_id,
            )

    # ---- shared helpers -----------------------------------------------------

    def _messages_to_api(self, messages: list[ChatMessage]) -> list[dict[str, Any]]:
        """Convert ChatMessages to OpenAI/OpenRouter API format."""
        result: list[dict[str, Any]] = []
        for m in messages:
            msg: dict[str, Any] = {"role": m.role, "content": m.content or ""}
            if m.role == "tool" and m.tool_call_id:
                msg["tool_call_id"] = m.tool_call_id
            if m.role == "assistant" and m.tool_calls:
                msg["tool_calls"] = m.tool_calls
            result.append(msg)
        return result

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
        return LlmResponse(
            text=normalized_text or None, tool_calls=tool_calls, raw=response
        )
