from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from chat.llm_client import (
    ChatMessage,
    LlmClient,
    LlmResponse,
    ToolCall,
    ToolDefinition,
)
from chat.tooling import (
    looks_like_complete_tool_args,
    normalize_tool_arguments,
    tool_name_to_delta_type,
)


async def stream_llm_response(
    *,
    llm_client: LlmClient,
    worldline_id: str,
    messages: list[ChatMessage],
    tools: list[ToolDefinition],
    max_output_tokens: int | None,
    on_delta: Callable[[str, dict[str, Any]], Awaitable[None]],
) -> LlmResponse:
    text_buffer: list[str] = []
    tool_call_accum: dict[str, dict[str, Any]] = {}
    last_tool_call_id: str | None = None
    emitted_text = False

    stream = llm_client.generate_stream(
        messages=messages,
        tools=tools,
        max_output_tokens=max_output_tokens,
    )

    async for chunk in stream:
        if chunk.type == "text":
            delta_text = chunk.text or ""
            if delta_text:
                text_buffer.append(delta_text)
                emitted_text = True
                await on_delta(
                    worldline_id,
                    {"type": "assistant_text", "delta": delta_text},
                )
            continue

        if chunk.type == "tool_call_start":
            call_id = chunk.tool_call_id or f"call_{len(tool_call_accum) + 1}"
            last_tool_call_id = call_id
            tool_name = chunk.tool_name or ""
            tool_call_accum[call_id] = {
                "name": tool_name,
                "args_parts": [],
            }

            if emitted_text:
                await on_delta(
                    worldline_id,
                    {"type": "assistant_text", "done": True},
                )
                emitted_text = False
            continue

        if chunk.type == "tool_call_delta":
            call_id = (chunk.tool_call_id or "").strip() or last_tool_call_id or ""
            args_delta = chunk.arguments_delta or ""
            if call_id and call_id in tool_call_accum:
                accum = tool_call_accum[call_id]
                if looks_like_complete_tool_args(args_delta):
                    accum["args_parts"] = [args_delta]
                else:
                    accum["args_parts"].append(args_delta)

            accum = tool_call_accum.get(call_id) if call_id else None
            tool_name = accum["name"] if accum else ""
            delta_type = tool_name_to_delta_type(tool_name)

            if delta_type and args_delta:
                await on_delta(
                    worldline_id,
                    {
                        "type": delta_type,
                        "call_id": call_id,
                        "delta": args_delta,
                    },
                )
            continue

        if chunk.type == "tool_call_done":
            call_id = (chunk.tool_call_id or "").strip() or last_tool_call_id or ""
            accum = tool_call_accum.get(call_id) if call_id else None
            tool_name = accum["name"] if accum else ""
            delta_type = tool_name_to_delta_type(tool_name)
            if delta_type:
                await on_delta(
                    worldline_id,
                    {
                        "type": delta_type,
                        "call_id": call_id,
                        "done": True,
                    },
                )
            continue

    if emitted_text:
        await on_delta(
            worldline_id,
            {"type": "assistant_text", "done": True},
        )

    full_text = "".join(text_buffer).strip() or None

    tool_calls: list[ToolCall] = []
    for call_id, accum in tool_call_accum.items():
        raw_json = "".join(accum["args_parts"])
        try:
            arguments = json.loads(raw_json) if raw_json else {}
        except json.JSONDecodeError:
            arguments = {"_raw": raw_json}
        arguments = normalize_tool_arguments(accum["name"], arguments)
        tool_calls.append(
            ToolCall(
                id=call_id,
                name=accum["name"],
                arguments=arguments,
            )
        )

    return LlmResponse(text=full_text, tool_calls=tool_calls)
