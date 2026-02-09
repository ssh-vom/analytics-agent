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
class OpenAiAdapter:
    model: str
    api_key: str | None = None

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

        client = OpenAI(api_key=self.api_key)
        response = client.responses.create(
            model=self.model,
            input=[{"role": m.role, "content": m.content} for m in messages],
            tools=[
                {
                    "type": "function",
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                }
                for t in tools
            ],
            tool_choice=tool_choice or "auto",
            max_output_tokens=max_output_tokens,
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
        """Stream tokens from the OpenAI Responses API.

        Uses the *async* client so we get native ``async for`` iteration over
        server-sent events, avoiding thread-pool overhead.

        The Responses API emits events like:
          - ``response.output_text.delta``   -> text token
          - ``response.function_call_arguments.delta`` -> tool-call argument chunk
          - ``response.output_item.added``   -> new output item (message or function_call)
          - ``response.output_item.done``    -> output item finished
        """
        try:
            from openai import AsyncOpenAI
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "OpenAI SDK not installed. Add dependency: `openai`."
            ) from exc

        client = AsyncOpenAI(api_key=self.api_key)

        stream = await client.responses.create(
            model=self.model,
            input=[{"role": m.role, "content": m.content} for m in messages],
            tools=[
                {
                    "type": "function",
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                }
                for t in tools
            ],
            tool_choice=tool_choice or "auto",
            max_output_tokens=max_output_tokens,
            stream=True,
        )

        async for event in stream:
            event_type = getattr(event, "type", "")

            # --- text deltas ------------------------------------------------
            if event_type == "response.output_text.delta":
                delta = getattr(event, "delta", "")
                if delta:
                    yield StreamChunk(type="text", text=delta)
                continue

            # --- new output item (function_call or message) ------------------
            if event_type == "response.output_item.added":
                item = getattr(event, "item", None)
                item_type = getattr(item, "type", "")
                if item_type == "function_call":
                    call_id = getattr(item, "call_id", None) or getattr(
                        item, "id", None
                    )
                    name = getattr(item, "name", "")
                    yield StreamChunk(
                        type="tool_call_start",
                        tool_call_id=call_id,
                        tool_name=name,
                    )
                continue

            # --- function-call argument deltas -------------------------------
            if event_type == "response.function_call_arguments.delta":
                delta = getattr(event, "delta", "")
                call_id = getattr(event, "item_id", None)
                if delta:
                    yield StreamChunk(
                        type="tool_call_delta",
                        tool_call_id=call_id,
                        arguments_delta=delta,
                    )
                continue

            # --- function-call argument stream done --------------------------
            if event_type == "response.function_call_arguments.done":
                call_id = getattr(event, "item_id", None)
                yield StreamChunk(
                    type="tool_call_done",
                    tool_call_id=call_id,
                )
                continue

    # ---- shared helpers -----------------------------------------------------

    def _parse_response(self, response: Any) -> LlmResponse:
        output_items = getattr(response, "output", []) or []

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for item in output_items:
            item_type = getattr(item, "type", "")
            if item_type == "function_call":
                raw_arguments = getattr(item, "arguments", "{}") or "{}"
                try:
                    arguments = json.loads(raw_arguments)
                except json.JSONDecodeError:
                    arguments = {"_raw": raw_arguments}

                call_id = getattr(item, "call_id", None) or getattr(item, "id", None)
                tool_calls.append(
                    ToolCall(
                        id=call_id or f"call_{len(tool_calls) + 1}",
                        name=getattr(item, "name", ""),
                        arguments=arguments,
                    )
                )
                continue

            if item_type == "message":
                for content in getattr(item, "content", []) or []:
                    content_type = getattr(content, "type", "")
                    if content_type in {"output_text", "text"}:
                        text_value = getattr(content, "text", None)
                        if text_value:
                            text_parts.append(text_value)

        text = "".join(text_parts).strip() or None
        return LlmResponse(text=text, tool_calls=tool_calls, raw=response)
