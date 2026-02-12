from __future__ import annotations

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

__all__ = [
    "ChatMessage",
    "LlmResponse",
    "StreamChunk",
    "ToolCall",
    "ToolDefinition",
]
