from __future__ import annotations

from dataclasses import dataclass
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


class LlmClient(Protocol):
    async def generate(
        self,
        *,
        messages: list[ChatMessage],
        tools: list[ToolDefinition],
        tool_choice: str | None = None,
        max_output_tokens: int | None = None,
    ) -> LlmResponse: ...
