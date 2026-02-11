from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from fastapi import HTTPException

from debug_log import debug_log as _debug_log
from chat.event_store import (
    append_worldline_event,
    events_since_rowid,
    load_event_by_id,
    max_worldline_rowid,
)
from chat.llm_client import (
    ChatMessage,
    LlmClient,
    LlmResponse,
    ToolCall,
    ToolDefinition,
)
from chat.message_builder import build_llm_messages_from_events
from chat.streaming_bridge import stream_llm_response
from chat.tooling import (
    tool_definitions,
    tool_name_to_delta_type,
    tool_signature,
)
from tools import (
    PythonToolRequest,
    SqlToolRequest,
    execute_python_tool,
    execute_sql_tool,
)
from worldlines import get_worldline_events
from worldline_service import BranchOptions, WorldlineService

logger = logging.getLogger(__name__)


@dataclass
class ChatEngine:
    llm_client: LlmClient
    max_iterations: int = 20
    max_output_tokens: int | None = 1500
    worldline_service: WorldlineService = field(default_factory=WorldlineService)

    async def run_turn(
        self,
        worldline_id: str,
        message: str,
        on_event: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
        on_delta: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        if not message or not message.strip():
            raise HTTPException(status_code=400, detail="message must not be empty")

        # #region agent log
        _debug_log(
            run_id="initial",
            hypothesis_id="H3_H4",
            location="backend/chat/engine.py:run_turn:start",
            message="Starting chat turn",
            data={
                "worldline_id": worldline_id,
                "message_preview": message[:200],
            },
        )
        # #endregion

        active_worldline_id = worldline_id
        starting_rowid_by_worldline = {
            active_worldline_id: self._max_worldline_rowid(active_worldline_id)
        }
        user_event = self._append_worldline_event(
            worldline_id=active_worldline_id,
            event_type="user_message",
            payload={"text": message},
        )
        if on_event is not None:
            await on_event(active_worldline_id, user_event)

        messages = await self._build_llm_messages(active_worldline_id)
        # #region agent log
        _debug_log(
            run_id="initial",
            hypothesis_id="H6_H7",
            location="backend/chat/engine.py:run_turn:built_messages",
            message="Built LLM messages for turn",
            data={
                "worldline_id": active_worldline_id,
                "message_count": len(messages),
                "tail": [
                    {
                        "role": msg.role,
                        "content_preview": (msg.content or "")[:140],
                        "has_tool_calls": bool(msg.tool_calls),
                        "tool_call_count": len(msg.tool_calls or []),
                    }
                    for msg in messages[-6:]
                ],
            },
        )
        # #endregion
        final_text: str | None = None
        successful_tool_signatures: set[str] = set()
        successful_tool_results: dict[str, dict[str, Any]] = {}
        python_succeeded_in_turn = False
        empty_response_retries = 0

        for _ in range(self.max_iterations):
            # ----- LLM call: stream when on_delta is available, else batch -----
            if on_delta is not None:
                response = await self._stream_llm_response(
                    worldline_id=active_worldline_id,
                    messages=messages,
                    tools=self._tool_definitions(
                        include_python=not python_succeeded_in_turn
                    ),
                    on_delta=on_delta,
                )
            else:
                response = await self.llm_client.generate(
                    messages=messages,
                    tools=self._tool_definitions(
                        include_python=not python_succeeded_in_turn
                    ),
                    max_output_tokens=self.max_output_tokens,
                )

            # ----- Emit assistant_plan if text accompanies tool calls ----------
            if response.text and response.tool_calls:
                plan_event = self._append_worldline_event(
                    worldline_id=active_worldline_id,
                    event_type="assistant_plan",
                    payload={"text": response.text},
                )
                if on_event is not None:
                    await on_event(active_worldline_id, plan_event)

            if response.text:
                messages.append(ChatMessage(role="assistant", content=response.text))

            if response.tool_calls:
                repeated_call_detected = False
                for tool_call in response.tool_calls:
                    tool_name = (tool_call.name or "").strip()
                    delta_type = self._tool_name_to_delta_type(tool_name)
                    # #region agent log
                    _debug_log(
                        run_id="initial",
                        hypothesis_id="H3_H4",
                        location="backend/chat/engine.py:run_turn:tool_call",
                        message="Model emitted tool call",
                        data={
                            "worldline_id": active_worldline_id,
                            "tool_name": tool_name,
                            "tool_call_id": tool_call.id,
                            "tool_args_preview": json.dumps(
                                tool_call.arguments or {},
                                ensure_ascii=True,
                                default=str,
                            )[:220],
                        },
                    )
                    # #endregion

                    if tool_name == "run_python" and python_succeeded_in_turn:
                        final_text = (
                            "Python already ran successfully in this turn, so I stopped "
                            "additional Python executions and finalized the result."
                        )
                        if on_delta is not None and delta_type is not None:
                            await on_delta(
                                active_worldline_id,
                                {
                                    "type": delta_type,
                                    "call_id": tool_call.id or None,
                                    "skipped": True,
                                    "reason": "python_already_succeeded_in_turn",
                                },
                            )
                        repeated_call_detected = True
                        break

                    signature = self._tool_signature(
                        worldline_id=active_worldline_id,
                        tool_call=tool_call,
                    )
                    if signature in successful_tool_signatures:
                        if on_delta is not None and delta_type is not None:
                            await on_delta(
                                active_worldline_id,
                                {
                                    "type": delta_type,
                                    "call_id": tool_call.id or None,
                                    "skipped": True,
                                    "reason": "repeated_identical_tool_call",
                                },
                            )
                        final_text = (
                            "I stopped because the model repeated the same tool call "
                            "with identical arguments in this turn."
                        )
                        repeated_call_detected = True
                        break

                    # Note: tool call deltas were already streamed in _stream_llm_response.
                    # For the non-streaming path (on_delta is None), no deltas are emitted.

                    # Small delay between tool calls to avoid burning through API requests too fast
                    await asyncio.sleep(0.4)

                    tool_result, switched_worldline_id = await self._execute_tool_call(
                        worldline_id=active_worldline_id,
                        tool_call=tool_call,
                        carried_user_message=message,
                        on_event=on_event,
                    )
                    if (
                        switched_worldline_id
                        and switched_worldline_id != active_worldline_id
                    ):
                        active_worldline_id = switched_worldline_id
                        starting_rowid_by_worldline.setdefault(active_worldline_id, 0)
                        messages = await self._build_llm_messages(active_worldline_id)
                        # Reset per-turn state for the new worldline context
                        python_succeeded_in_turn = False
                        successful_tool_signatures.clear()

                    serialized = json.dumps(
                        tool_result,
                        ensure_ascii=True,
                        default=str,
                    )
                    if len(serialized) > 12_000:
                        serialized = serialized[:12_000] + "...(truncated)"
                    messages.append(
                        ChatMessage(
                            role="tool",
                            content=serialized,
                            tool_call_id=tool_call.id or None,
                        )
                    )
                    if not tool_result.get("error"):
                        successful_tool_signatures.add(signature)
                        successful_tool_results[signature] = tool_result
                        if tool_name == "run_python":
                            python_succeeded_in_turn = True
                if repeated_call_detected:
                    break
                continue

            if response.text:
                final_text = response.text
                break

            # The LLM returned no text and no tool calls.  This typically
            # happens when the conversation history confuses the model.
            # Retry once by continuing the loop (which makes a fresh LLM
            # call); give up on the second empty response.
            empty_response_retries += 1
            if empty_response_retries <= 1:
                logger.warning(
                    "Empty LLM response (no text, no tool_calls). "
                    "messages=%d, last_role=%s. Retrying (attempt %d).",
                    len(messages),
                    messages[-1].role if messages else "N/A",
                    empty_response_retries,
                )
                continue

            logger.warning(
                "Empty LLM response persisted after retry. "
                "messages=%d, last_role=%s. Giving up.",
                len(messages),
                messages[-1].role if messages else "N/A",
            )
            final_text = (
                "I wasn't able to generate a response for that request. "
                "Could you try rephrasing your question?"
            )
            break

        if final_text is None:
            final_text = (
                "I reached the tool-loop limit before producing a final answer."
            )

        assistant_event = self._append_worldline_event(
            worldline_id=active_worldline_id,
            event_type="assistant_message",
            payload={"text": final_text},
        )
        if on_event is not None:
            await on_event(active_worldline_id, assistant_event)

        events = self._events_since_rowid(
            worldline_id=active_worldline_id,
            rowid=starting_rowid_by_worldline[active_worldline_id],
        )

        return (
            active_worldline_id,
            events,
        )

    # ---- real-time streaming bridge -----------------------------------------

    async def _stream_llm_response(
        self,
        *,
        worldline_id: str,
        messages: list[ChatMessage],
        tools: list[ToolDefinition],
        on_delta: Callable[[str, dict[str, Any]], Awaitable[None]],
    ) -> LlmResponse:
        return await stream_llm_response(
            llm_client=self.llm_client,
            worldline_id=worldline_id,
            messages=messages,
            tools=tools,
            max_output_tokens=self.max_output_tokens,
            on_delta=on_delta,
        )

    def _tool_name_to_delta_type(self, tool_name: str) -> str | None:
        return tool_name_to_delta_type(tool_name)

    def _tool_definitions(self, *, include_python: bool = True) -> list[ToolDefinition]:
        return tool_definitions(include_python=include_python)

    # ---- tool execution -----------------------------------------------------

    async def _execute_tool_call(
        self,
        worldline_id: str,
        tool_call: ToolCall,
        carried_user_message: str,
        on_event: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    ) -> tuple[dict[str, Any], str | None]:
        name = (tool_call.name or "").strip()
        args = tool_call.arguments or {}

        if name == "run_sql":
            sql = args.get("sql")
            if not isinstance(sql, str) or not sql.strip():
                err_result = {"error": "run_sql requires a non-empty 'sql' string"}
                if on_event is not None:
                    await self._persist_failed_tool_call(
                        worldline_id,
                        "tool_call_sql",
                        "tool_result_sql",
                        {
                            "sql": str(sql) if sql else "",
                            "limit": 100,
                            "call_id": tool_call.id,
                        },
                        err_result,
                        on_event,
                    )
                return err_result, None

            raw_limit = args.get("limit", 100)
            try:
                limit = int(raw_limit)
            except (TypeError, ValueError):
                limit = 100
            limit = max(1, min(limit, 10_000))

            try:
                result = await execute_sql_tool(
                    SqlToolRequest(
                        worldline_id=worldline_id,
                        sql=sql,
                        limit=limit,
                        call_id=tool_call.id or None,
                    ),
                    on_event=(
                        None
                        if on_event is None
                        else lambda event: on_event(worldline_id, event)
                    ),
                )
                return result, None
            except HTTPException as exc:
                return {"error": str(exc.detail), "status_code": exc.status_code}, None
            except Exception as exc:  # pragma: no cover
                return {"error": str(exc)}, None

        if name == "run_python":
            code = args.get("code")
            if not isinstance(code, str) or not code.strip():
                # #region agent log
                _debug_log(
                    run_id="initial",
                    hypothesis_id="H8",
                    location="backend/chat/engine.py:_execute_tool_call:run_python_invalid_args",
                    message="run_python call missing/invalid code argument",
                    data={
                        "worldline_id": worldline_id,
                        "call_id": tool_call.id,
                        "args_keys": sorted(list(args.keys())),
                        "args_preview": json.dumps(
                            args, ensure_ascii=True, default=str
                        )[:220],
                    },
                )
                # #endregion
                err_result = {"error": "run_python requires a non-empty 'code' string"}
                if on_event is not None:
                    await self._persist_failed_tool_call(
                        worldline_id,
                        "tool_call_python",
                        "tool_result_python",
                        {
                            "code": str(code) if code else "",
                            "timeout": 30,
                            "call_id": tool_call.id,
                        },
                        err_result,
                        on_event,
                    )
                return err_result, None

            raw_timeout = args.get("timeout", 30)
            try:
                timeout = int(raw_timeout)
            except (TypeError, ValueError):
                timeout = 30
            timeout = max(1, min(timeout, 120))

            try:
                result = await execute_python_tool(
                    PythonToolRequest(
                        worldline_id=worldline_id,
                        code=code,
                        timeout=timeout,
                        call_id=tool_call.id or None,
                    ),
                    on_event=(
                        None
                        if on_event is None
                        else lambda event: on_event(worldline_id, event)
                    ),
                )
                return result, None
            except HTTPException as exc:
                return {"error": str(exc.detail), "status_code": exc.status_code}, None
            except Exception as exc:  # pragma: no cover
                return {"error": str(exc)}, None

        if name == "time_travel":
            from_event_id = args.get("from_event_id")
            if not isinstance(from_event_id, str) or not from_event_id.strip():
                return {"error": "time_travel requires 'from_event_id'"}, None

            name_arg = args.get("name")
            branch_name = name_arg if isinstance(name_arg, str) and name_arg else None

            try:
                branch_result = self.worldline_service.branch_from_event(
                    BranchOptions(
                        source_worldline_id=worldline_id,
                        from_event_id=from_event_id,
                        name=branch_name,
                        append_events=True,
                        carried_user_message=carried_user_message,
                    )
                )
                if on_event is not None:
                    for event_id in branch_result.created_event_ids:
                        event = self._load_event_by_id(event_id)
                        await on_event(branch_result.new_worldline_id, event)
                return branch_result.to_tool_result(), branch_result.new_worldline_id
            except HTTPException as exc:
                return {"error": str(exc.detail), "status_code": exc.status_code}, None
            except Exception as exc:  # pragma: no cover
                return {"error": str(exc)}, None

        return {"error": f"unknown tool '{name}'"}, None

    async def _persist_failed_tool_call(
        self,
        worldline_id: str,
        call_type: str,
        result_type: str,
        call_payload: dict[str, Any],
        result_payload: dict[str, Any],
        on_event: Callable[[str, dict[str, Any]], Awaitable[None]],
    ) -> None:
        """Persist a tool call and its error result so the user sees the attempt."""
        call_event = self._append_worldline_event(
            worldline_id=worldline_id,
            event_type=call_type,
            payload=call_payload,
        )
        if on_event is not None:
            await on_event(worldline_id, call_event)

        result_event = self._append_worldline_event(
            worldline_id=worldline_id,
            event_type=result_type,
            payload=result_payload,
        )
        if on_event is not None:
            await on_event(worldline_id, result_event)

    # ---- message building ---------------------------------------------------

    async def _build_llm_messages(self, worldline_id: str) -> list[ChatMessage]:
        timeline = await get_worldline_events(
            worldline_id=worldline_id,
            limit=100,
            cursor=None,
        )
        events = timeline.get("events", [])
        return build_llm_messages_from_events(list(events))

    # ---- helpers ------------------------------------------------------------

    def _tool_signature(self, *, worldline_id: str, tool_call: ToolCall) -> str:
        return tool_signature(
            worldline_id=worldline_id,
            tool_call=tool_call,
        )

    def _append_worldline_event(
        self,
        *,
        worldline_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return append_worldline_event(
            worldline_id=worldline_id,
            event_type=event_type,
            payload=payload,
        )

    def _load_event_by_id(self, event_id: str) -> dict[str, Any]:
        return load_event_by_id(event_id)

    def _max_worldline_rowid(self, worldline_id: str) -> int:
        return max_worldline_rowid(worldline_id)

    def _events_since_rowid(
        self, *, worldline_id: str, rowid: int
    ) -> list[dict[str, Any]]:
        return events_since_rowid(worldline_id=worldline_id, rowid=rowid)
