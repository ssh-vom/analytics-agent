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


_UNSUPPORTED_SCHEMA_KEYS = {
    "additionalProperties",
    "additional_properties",
}


def _messages_to_gemini_contents(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    """Convert ChatMessages to Gemini contents format."""
    result: list[dict[str, Any]] = []
    for m in messages:
        role = _to_gemini_role(m.role)
        parts: list[dict[str, Any]] = []
        if m.content:
            parts.append({"text": m.content})
        if m.role == "assistant" and m.tool_calls:
            for tc in m.tool_calls:
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                if isinstance(fn, dict) and fn.get("name"):
                    args_raw = fn.get("arguments", "{}")
                    args = json.loads(args_raw) if isinstance(args_raw, str) else (fn.get("arguments") or {})
                    parts.append({
                        "function_call": {
                            "name": fn["name"],
                            "args": args if isinstance(args, dict) else {},
                        }
                    })
        if not parts:
            parts = [{"text": m.content or ""}]
        result.append({"role": role, "parts": parts})
    return result


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

        contents = _messages_to_gemini_contents(messages)
        response = client.models.generate_content(
            model=self.model,
            contents=contents,
            config=genai_types.GenerateContentConfig(**config_kwargs),
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
        """Stream tokens from the Gemini API.

        Gemini's ``generate_content_stream()`` is synchronous, so we run it in
        a thread and pipe chunks through an ``asyncio.Queue``.

        Each Gemini streaming chunk may contain ``text`` parts and/or
        ``function_call`` parts.  Because Gemini does not provide incremental
        function-call argument deltas (it sends the full arguments in one
        chunk), we emit start + delta-with-full-args + done in sequence.
        """
        import queue as _queue

        _sentinel = object()

        def _run_sync() -> None:
            try:
                from google import genai
                from google.genai import types as genai_types
            except ModuleNotFoundError as exc:
                sync_queue.put(exc)
                sync_queue.put(_sentinel)
                return

            if not self.api_key:
                sync_queue.put(
                    RuntimeError(
                        "Gemini API key is missing. Set GEMINI_API_KEY or GOOGLE_API_KEY."
                    )
                )
                sync_queue.put(_sentinel)
                return

            try:
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
                    "tools": [
                        genai_types.Tool(function_declarations=function_declarations)
                    ]
                }
                if max_output_tokens is not None:
                    config_kwargs["max_output_tokens"] = max_output_tokens
                if tool_choice is not None:
                    config_kwargs["tool_choice"] = tool_choice

                for chunk in client.models.generate_content_stream(
                    model=self.model,
                    contents=[
                        {
                            "role": _to_gemini_role(m.role),
                            "parts": [{"text": m.content}],
                        }
                        for m in messages
                    ],
                    config=genai_types.GenerateContentConfig(**config_kwargs),
                ):
                    sync_queue.put(chunk)
            except Exception as exc:
                sync_queue.put(exc)
            finally:
                sync_queue.put(_sentinel)

        sync_queue: _queue.Queue = _queue.Queue()
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, _run_sync)

        tool_call_counter = 0
        while True:
            item = await loop.run_in_executor(None, sync_queue.get)
            if item is _sentinel:
                break
            if isinstance(item, Exception):
                raise item

            # item is a Gemini streaming chunk
            candidates = getattr(item, "candidates", []) or []
            for candidate in candidates:
                content = getattr(candidate, "content", None)
                parts = getattr(content, "parts", []) if content is not None else []
                for part in parts or []:
                    function_call = getattr(part, "function_call", None)
                    if function_call is not None:
                        tool_call_counter += 1
                        name = getattr(function_call, "name", "") or ""
                        raw_args = getattr(function_call, "args", {}) or {}
                        if isinstance(raw_args, str):
                            args_json = raw_args
                        else:
                            args_json = json.dumps(dict(raw_args), ensure_ascii=True)

                        call_id = f"call_{tool_call_counter}"
                        yield StreamChunk(
                            type="tool_call_start",
                            tool_call_id=call_id,
                            tool_name=name,
                        )
                        yield StreamChunk(
                            type="tool_call_delta",
                            tool_call_id=call_id,
                            arguments_delta=args_json,
                        )
                        yield StreamChunk(
                            type="tool_call_done",
                            tool_call_id=call_id,
                        )
                        continue

                    text_value = getattr(part, "text", None)
                    if text_value:
                        yield StreamChunk(type="text", text=text_value)

    # ---- shared helpers -----------------------------------------------------

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
