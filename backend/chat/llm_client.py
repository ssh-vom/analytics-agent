from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class LlmResponse:
    text: str | None
    tool_calls: list[ToolCall]
    raw: Any | None = None


@dataclass(frozen=True)
class StreamChunk:
    """A single incremental piece from an LLM streaming response.

    Chunk types:
      - ``"text"``              – a text token (``text`` field carries the delta)
      - ``"tool_call_start"``   – signals a new tool call (``tool_call_id``, ``tool_name``)
      - ``"tool_call_delta"``   – an incremental piece of tool-call arguments JSON
      - ``"tool_call_done"``    – the tool call's argument stream is finished
    """

    type: str  # "text" | "tool_call_start" | "tool_call_delta" | "tool_call_done"
    text: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    arguments_delta: str | None = None


class LlmClient(Protocol):
    async def generate(
        self,
        *,
        messages: list[ChatMessage],
        tools: list[ToolDefinition],
        tool_choice: str | None = None,
        max_output_tokens: int | None = None,
    ) -> LlmResponse: ...

    async def generate_stream(
        self,
        *,
        messages: list[ChatMessage],
        tools: list[ToolDefinition],
        tool_choice: str | None = None,
        max_output_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]: ...
