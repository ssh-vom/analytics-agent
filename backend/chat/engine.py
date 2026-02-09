from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from fastapi import HTTPException

try:
    from backend.chat.llm_client import ChatMessage, LlmClient, ToolCall, ToolDefinition
    from backend.meta import (
        append_event,
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
    from chat.llm_client import ChatMessage, LlmClient, ToolCall, ToolDefinition
    from meta import append_event, get_conn, get_worldline_row, set_worldline_head
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
    max_iterations: int = 6
    max_output_tokens: int | None = 1500
    worldline_service: WorldlineService = field(default_factory=WorldlineService)

    async def run_turn(
        self,
        worldline_id: str,
        message: str,
        on_event: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
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
        tool_call_count: dict[str, int] = {}
        python_succeeded_in_turn = False
        max_tool_calls_per_turn = {
            "run_sql": 3,
            "run_python": 3,
            "time_travel": 1,
        }

        for _ in range(self.max_iterations):
            response = await self.llm_client.generate(
                messages=messages,
                tools=self._tool_definitions(include_python=not python_succeeded_in_turn),
                max_output_tokens=self.max_output_tokens,
            )

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

                    tool_call_count[tool_name] = tool_call_count.get(tool_name, 0) + 1

                    max_calls = max_tool_calls_per_turn.get(tool_name)
                    if max_calls is not None and tool_call_count[tool_name] > max_calls:
                        final_text = (
                            f"I stopped because `{tool_name}` was called too many times "
                            "in one turn. Please refine the request and try again."
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

                    serialized = json.dumps(
                        tool_result,
                        ensure_ascii=True,
                        default=str,
                    )
                    if len(serialized) > 12_000:
                        serialized = serialized[:12_000] + "...(truncated)"
                    messages.append(
                        ChatMessage(
                            role="assistant",
                            content=f"Tool result for {tool_call.name}: {serialized}",
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
                        "Use for plotting, data manipulation, and file artifacts."
                    ),
                    input_schema=PYTHON_TOOL_SCHEMA,
                ),
            )
        return tools

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
                return {"error": "run_sql requires a non-empty 'sql' string"}, None

            raw_limit = args.get("limit", 100)
            try:
                limit = int(raw_limit)
            except (TypeError, ValueError):
                limit = 100
            limit = max(1, min(limit, 10_000))

            try:
                result = await execute_sql_tool(
                    SqlToolRequest(worldline_id=worldline_id, sql=sql, limit=limit),
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
                return {"error": "run_python requires a non-empty 'code' string"}, None

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

    async def _build_llm_messages(self, worldline_id: str) -> list[ChatMessage]:
        timeline = await get_worldline_events(
            worldline_id=worldline_id,
            limit=100,
            cursor=None,
        )
        events = timeline.get("events", [])
        messages: list[ChatMessage] = []

        for event in events:
            event_type = event.get("type")
            payload = event.get("payload", {})

            if event_type == "user_message":
                text = payload.get("text")
                if text:
                    messages.append(ChatMessage(role="user", content=str(text)))
                continue

            if event_type == "assistant_message":
                text = payload.get("text")
                if text:
                    messages.append(ChatMessage(role="assistant", content=str(text)))
                continue

            if event_type in {"tool_result_sql", "tool_result_python"}:
                summary = json.dumps(payload, ensure_ascii=True, default=str)
                if len(summary) > 2_000:
                    summary = summary[:2_000] + "...(truncated)"
                messages.append(
                    ChatMessage(
                        role="assistant",
                        content=f"Prior {event_type} result: {summary}",
                    )
                )

        return messages

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

        return {
            "id": row["id"],
            "parent_event_id": row["parent_event_id"],
            "type": row["type"],
            "payload": json.loads(row["payload_json"]),
            "created_at": row["created_at"],
        }

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
            output.append(
                {
                    "id": row["id"],
                    "parent_event_id": row["parent_event_id"],
                    "type": row["type"],
                    "payload": json.loads(row["payload_json"]),
                    "created_at": row["created_at"],
                }
            )
        return output
