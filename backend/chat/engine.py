from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from fastapi import HTTPException

try:
    from backend.chat.llm_client import (
        ChatMessage,
        LlmClient,
        LlmResponse,
        ToolCall,
        ToolDefinition,
    )
    from backend.meta import (
        append_event,
        event_row_to_dict,
        get_conn,
        get_worldline_row,
        set_worldline_head,
    )
    from backend.tools import (
        PythonToolRequest,
        SqlToolRequest,
        execute_python_tool,
        execute_sql_tool,
    )
    from backend.worldlines import get_worldline_events
    from backend.worldline_service import BranchOptions, WorldlineService
except ModuleNotFoundError:
    from chat.llm_client import (
        ChatMessage,
        LlmClient,
        LlmResponse,
        ToolCall,
        ToolDefinition,
    )
    from meta import (
        append_event,
        event_row_to_dict,
        get_conn,
        get_worldline_row,
        set_worldline_head,
    )
    from tools import (
        PythonToolRequest,
        SqlToolRequest,
        execute_python_tool,
        execute_sql_tool,
    )
    from worldlines import get_worldline_events
    from worldline_service import BranchOptions, WorldlineService


SQL_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sql": {"type": "string"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 10_000},
    },
    "required": ["sql"],
    "additionalProperties": False,
}

PYTHON_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "code": {"type": "string"},
        "timeout": {"type": "integer", "minimum": 1, "maximum": 120},
    },
    "required": ["code"],
    "additionalProperties": False,
}
TIME_TRAVEL_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "from_event_id": {"type": "string"},
        "name": {"type": "string"},
    },
    "required": ["from_event_id"],
    "additionalProperties": False,
}


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
        final_text: str | None = None
        successful_tool_signatures: set[str] = set()
        python_succeeded_in_turn = False

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

                    if tool_name == "run_python" and python_succeeded_in_turn:
                        final_text = (
                            "Python already ran successfully in this turn, so I stopped "
                            "additional Python executions and finalized the result."
                        )
                        repeated_call_detected = True
                        break

                    signature = self._tool_signature(
                        worldline_id=active_worldline_id,
                        tool_call=tool_call,
                    )
                    if signature in successful_tool_signatures:
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
                        if tool_name == "run_python":
                            python_succeeded_in_turn = True
                if repeated_call_detected:
                    break
                continue

            final_text = response.text or "Done."
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
        """Consume ``generate_stream()`` from the adapter, emit deltas in
        real-time, and return the accumulated ``LlmResponse``.

        This replaces the old simulated-streaming approach.

        Delta protocol emitted to ``on_delta``:
          - ``{"type": "assistant_text", "delta": "..."}``
          - ``{"type": "assistant_text", "done": True}``
          - ``{"type": "tool_call_sql"|"tool_call_python", "call_id": "...", "delta": "..."}``
          - ``{"type": "tool_call_sql"|"tool_call_python", "call_id": "...", "done": True}``
        """
        text_buffer: list[str] = []
        # Per-tool-call accumulators:  call_id -> {name, args_json_parts}
        tool_call_accum: dict[str, dict[str, Any]] = {}
        # Track whether we've emitted text and whether we've sent the text-done signal
        emitted_text = False

        stream = self.llm_client.generate_stream(
            messages=messages,
            tools=tools,
            max_output_tokens=self.max_output_tokens,
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
                tool_name = chunk.tool_name or ""
                tool_call_accum[call_id] = {
                    "name": tool_name,
                    "args_parts": [],
                }

                # If we were streaming text and a tool call starts, close the
                # text stream (text will become an assistant_plan event).
                if emitted_text:
                    await on_delta(
                        worldline_id,
                        {"type": "assistant_text", "done": True},
                    )
                    emitted_text = False
                continue

            if chunk.type == "tool_call_delta":
                call_id = chunk.tool_call_id or ""
                args_delta = chunk.arguments_delta or ""
                if call_id in tool_call_accum:
                    tool_call_accum[call_id]["args_parts"].append(args_delta)

                # Determine the delta event type from the tool name
                accum = tool_call_accum.get(call_id)
                tool_name = accum["name"] if accum else ""
                delta_type = self._tool_name_to_delta_type(tool_name)

                if delta_type and args_delta:
                    # For SQL/Python, we need to parse the arguments delta and
                    # extract the code/sql content.  The raw args_delta is a
                    # fragment of JSON, so we cannot parse it incrementally.
                    # Instead, we forward the raw JSON fragment.  The engine
                    # will do a final parse when the call is done.
                    # However, for a better UX we try to extract code tokens.
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
                call_id = chunk.tool_call_id or ""
                accum = tool_call_accum.get(call_id)
                tool_name = accum["name"] if accum else ""
                delta_type = self._tool_name_to_delta_type(tool_name)
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

        # If text was streamed but no tool_call_start closed it, close now.
        if emitted_text:
            await on_delta(
                worldline_id,
                {"type": "assistant_text", "done": True},
            )

        # ----- Assemble the final LlmResponse from accumulated buffers -----
        full_text = "".join(text_buffer).strip() or None

        tool_calls: list[ToolCall] = []
        for call_id, accum in tool_call_accum.items():
            raw_json = "".join(accum["args_parts"])
            try:
                arguments = json.loads(raw_json) if raw_json else {}
            except json.JSONDecodeError:
                arguments = {"_raw": raw_json}
            tool_calls.append(
                ToolCall(
                    id=call_id,
                    name=accum["name"],
                    arguments=arguments,
                )
            )

        return LlmResponse(text=full_text, tool_calls=tool_calls)

    @staticmethod
    def _tool_name_to_delta_type(tool_name: str) -> str | None:
        """Map an LLM tool name to the SSE delta type for streaming."""
        if tool_name == "run_sql":
            return "tool_call_sql"
        if tool_name == "run_python":
            return "tool_call_python"
        return None

    # ---- tool definitions ---------------------------------------------------

    def _tool_definitions(self, *, include_python: bool = True) -> list[ToolDefinition]:
        tools: list[ToolDefinition] = [
            ToolDefinition(
                name="run_sql",
                description=(
                    "Execute a read-only SQL query against the worldline DuckDB. "
                    "Use for table reads and aggregations."
                ),
                input_schema=SQL_TOOL_SCHEMA,
            ),
            ToolDefinition(
                name="time_travel",
                description=(
                    "Create a new worldline from a prior event and continue execution there."
                ),
                input_schema=TIME_TRAVEL_TOOL_SCHEMA,
            ),
        ]

        if include_python:
            tools.insert(
                1,
                ToolDefinition(
                    name="run_python",
                    description=(
                        "Execute Python in the sandbox workspace for this worldline. "
                        "Use for plotting, data manipulation, and file artifacts. "
                        "For plots: use matplotlib (plt.plot, plt.bar, etc.) and call "
                        "plt.savefig('plot.png') before plt.show() to persist the image."
                    ),
                    input_schema=PYTHON_TOOL_SCHEMA,
                ),
            )
        return tools

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
                        {"sql": str(sql) if sql else "", "limit": 100, "call_id": tool_call.id},
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
                err_result = {"error": "run_python requires a non-empty 'code' string"}
                if on_event is not None:
                    await self._persist_failed_tool_call(
                        worldline_id,
                        "tool_call_python",
                        "tool_result_python",
                        {"code": str(code) if code else "", "timeout": 30, "call_id": tool_call.id},
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
        messages: list[ChatMessage] = []

        # Add system message explaining available tools and when to use them
        system_prompt = """You are an AI assistant with access to tools for data analysis. You have two main tools available:

1. **run_sql**: Execute SQL queries against a DuckDB database containing user data. Use this when:
   - The user asks questions about data, tables, or specific values
   - You need to retrieve, filter, aggregate, or explore data
   - The request involves counting, summing, or analyzing structured data

2. **run_python**: Execute Python code in a sandboxed environment. Use this when:
   - You need to create visualizations, charts, or plots (matplotlib is available)
   - Complex data manipulation or statistical analysis is required
   - Working with files, dataframes, or generating data insights

   For plots: use matplotlib (e.g. plt.plot, plt.bar) and call plt.savefig('filename.png') 
   to persist the image. The working directory is /workspace; saved files become viewable artifacts.

**CRITICAL - You MUST execute tools:**
- NEVER respond with only a plan or description of what you would do. You MUST actually call run_sql and/or run_python.
- When the user asks for analysis, data exploration, or visualizations: call the tools first, then summarize the results.
- If you need to explore the schema: call run_sql with a query like "SELECT * FROM table LIMIT 5" or "PRAGMA table_info(table)".
- Do not say "Let me..." or "I'll..." without immediately following with a tool call in the same response.

**Guidelines:**
- Call SQL first to retrieve the data, then use Python if you need to visualize or further analyze it
- After getting results, provide insights and context, not just raw data
- If a query might be expensive or return many rows, add appropriate LIMIT clauses

The user is expecting you to help them explore and understand their data. Use the appropriate tool(s) to deliver helpful analysis and insights."""
        messages.append(ChatMessage(role="system", content=system_prompt))

        # Events are newest-first (depth DESC); reverse for chronological order
        events_chrono = list(reversed(events))
        by_id = {e["id"]: e for e in events}

        pending_plan: str | None = None
        pending_tool_calls: list[dict[str, Any]] = []
        assistant_emitted_for_turn = False

        for event in events_chrono:
            event_type = event.get("type")
            payload = event.get("payload", {})

            if event_type == "user_message":
                pending_plan = None
                pending_tool_calls = []
                assistant_emitted_for_turn = False
                text = payload.get("text")
                if text:
                    messages.append(ChatMessage(role="user", content=str(text)))
                continue

            if event_type == "assistant_plan":
                pending_plan = (payload.get("text") or "").strip() or None
                pending_tool_calls = []
                assistant_emitted_for_turn = False
                continue

            if event_type == "assistant_message":
                # Emit pending assistant with tool_calls before final message
                if pending_tool_calls and not assistant_emitted_for_turn:
                    tool_calls_spec = [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc["arguments"] or {}),
                            },
                        }
                        for tc in pending_tool_calls
                    ]
                    content = (pending_plan or "").strip()
                    messages.append(
                        ChatMessage(
                            role="assistant",
                            content=content or "",
                            tool_calls=tool_calls_spec,
                        )
                    )
                    assistant_emitted_for_turn = True
                pending_plan = None
                pending_tool_calls = []
                assistant_emitted_for_turn = False
                text = payload.get("text")
                if text:
                    messages.append(ChatMessage(role="assistant", content=str(text)))
                continue

            if event_type in {"tool_call_sql", "tool_call_python"}:
                name = "run_sql" if "sql" in event_type else "run_python"
                args = dict(payload)
                args.pop("call_id", None)
                call_id = payload.get("call_id") or event.get("id", "")
                pending_tool_calls.append(
                    {"id": call_id, "name": name, "arguments": args}
                )
                continue

            if event_type in {"tool_result_sql", "tool_result_python"}:
                parent_id = event.get("parent_event_id") or ""
                parent = by_id.get(parent_id, {})
                call_id = (parent.get("payload") or {}).get("call_id") or parent_id
                summary = json.dumps(payload, ensure_ascii=True, default=str)
                if len(summary) > 2_000:
                    summary = summary[:2_000] + "...(truncated)"
                if pending_tool_calls and not assistant_emitted_for_turn:
                    tool_calls_spec = [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc["arguments"] or {}),
                            },
                        }
                        for tc in pending_tool_calls
                    ]
                    content = (pending_plan or "").strip()
                    messages.append(
                        ChatMessage(
                            role="assistant",
                            content=content or "",
                            tool_calls=tool_calls_spec,
                        )
                    )
                    assistant_emitted_for_turn = True
                messages.append(
                    ChatMessage(
                        role="tool",
                        content=summary,
                        tool_call_id=call_id or None,
                    )
                )
                continue

        return messages

    # ---- helpers ------------------------------------------------------------

    def _tool_signature(self, *, worldline_id: str, tool_call: ToolCall) -> str:
        return json.dumps(
            {
                "worldline_id": worldline_id,
                "name": tool_call.name,
                "arguments": tool_call.arguments or {},
            },
            ensure_ascii=True,
            sort_keys=True,
            default=str,
        )

    def _append_worldline_event(
        self,
        *,
        worldline_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        with get_conn() as conn:
            worldline = get_worldline_row(conn, worldline_id)
            if worldline is None:
                raise HTTPException(status_code=404, detail="worldline not found")

            parent_event_id = worldline["head_event_id"]
            event_id = append_event(
                conn=conn,
                worldline_id=worldline_id,
                parent_event_id=parent_event_id,
                event_type=event_type,
                payload=payload,
            )
            set_worldline_head(conn, worldline_id, event_id)
            conn.commit()
            return self._load_event_by_id(event_id)

    def _load_event_by_id(self, event_id: str) -> dict[str, Any]:
        with get_conn() as conn:
            row = conn.execute(
                """
                SELECT id, parent_event_id, type, payload_json, created_at
                FROM events
                WHERE id = ?
                """,
                (event_id,),
            ).fetchone()

        return event_row_to_dict(row)

    def _max_worldline_rowid(self, worldline_id: str) -> int:
        with get_conn() as conn:
            worldline = get_worldline_row(conn, worldline_id)
            if worldline is None:
                raise HTTPException(status_code=404, detail="worldline not found")

            row = conn.execute(
                "SELECT COALESCE(MAX(rowid), 0) AS max_rowid FROM events WHERE worldline_id = ?",
                (worldline_id,),
            ).fetchone()
            return int(row["max_rowid"])

    def _events_since_rowid(
        self, *, worldline_id: str, rowid: int
    ) -> list[dict[str, Any]]:
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, parent_event_id, type, payload_json, created_at
                FROM events
                WHERE worldline_id = ? AND rowid > ?
                ORDER BY rowid ASC
                """,
                (worldline_id, rowid),
            ).fetchall()

        output: list[dict[str, Any]] = []
        for row in rows:
            output.append(event_row_to_dict(row))
        return output
