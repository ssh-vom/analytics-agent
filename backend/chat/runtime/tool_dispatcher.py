from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException

from api.tools import PythonToolRequest, SqlToolRequest
from chat.llm_client import ToolCall
from chat.runtime.subagent_runner import SubagentRunner
from worldline_service import BranchOptions, WorldlineService


class ToolDispatcher:
    def __init__(
        self,
        *,
        llm_client,
        worldline_service: WorldlineService,
        load_event_by_id: Callable[[str], dict[str, Any]],
        run_child_turn: Callable[
            [str, str, int, int, bool], Awaitable[tuple[str, list[dict[str, Any]]]]
        ],
        execute_sql_tool: Callable[..., Awaitable[dict[str, Any]]],
        execute_python_tool: Callable[..., Awaitable[dict[str, Any]]],
        resolve_fork_event_id_or_head: Callable[..., tuple[str, str | None]],
        spawn_subagents_blocking: Callable[..., Awaitable[dict[str, Any]]],
        get_turn_coordinator: Callable[[], Any],
    ) -> None:
        self._llm_client = llm_client
        self._worldline_service = worldline_service
        self._load_event_by_id = load_event_by_id
        self._run_child_turn = run_child_turn
        self._execute_sql_tool = execute_sql_tool
        self._execute_python_tool = execute_python_tool
        self._resolve_fork_event_id_or_head = resolve_fork_event_id_or_head
        self._spawn_subagents_blocking = spawn_subagents_blocking
        self._get_turn_coordinator = get_turn_coordinator

    async def execute_tool_call(
        self,
        *,
        worldline_id: str,
        tool_call: ToolCall,
        carried_user_message: str,
        allowed_external_aliases: list[str] | None,
        on_event: Callable[[str, dict[str, Any]], Awaitable[None]] | None,
        on_delta: Callable[[str, dict[str, Any]], Awaitable[None]] | None,
        subagent_depth: int,
    ) -> tuple[dict[str, Any], str | None]:
        name = (tool_call.name or "").strip()
        args = tool_call.arguments or {}

        if name == "run_sql":
            sql = args.get("sql")
            if not isinstance(sql, str) or not sql.strip():
                return {"error": "run_sql requires a non-empty 'sql' string"}, None

            limit = self._coerce_int_arg(
                args.get("limit"), default=100, minimum=1, maximum=10_000
            )

            try:
                result = await self._execute_sql_tool(
                    SqlToolRequest(
                        worldline_id=worldline_id,
                        sql=sql,
                        limit=limit,
                        allowed_external_aliases=allowed_external_aliases,
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
                return {"error": "run_python requires a non-empty 'code' string"}, None

            timeout = self._coerce_int_arg(
                args.get("timeout"), default=30, minimum=1, maximum=120
            )

            try:
                result = await self._execute_python_tool(
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
                if isinstance(exc.detail, dict):
                    result_payload: dict[str, Any] = dict(exc.detail)
                    if "status_code" not in result_payload:
                        result_payload["status_code"] = exc.status_code
                    return result_payload, None
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
                branch_result = self._worldline_service.branch_from_event(
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

        if name == "spawn_subagents":
            if subagent_depth > 0:
                return {
                    "error": "spawn_subagents is disabled for subagent child turns",
                    "error_code": "spawn_subagents_nested_not_allowed",
                }, None

            tasks = args.get("tasks")
            goal = args.get("goal")
            if (not isinstance(tasks, list) or not tasks) and (
                not isinstance(goal, str) or not goal.strip()
            ):
                return {
                    "error": "spawn_subagents requires non-empty 'goal' or 'tasks'"
                }, None

            raw_from_event_id = args.get("from_event_id")
            requested_from_event_id = (
                raw_from_event_id.strip()
                if isinstance(raw_from_event_id, str) and raw_from_event_id.strip()
                else None
            )
            try:
                from_event_id, from_event_resolution = (
                    self._resolve_fork_event_id_or_head(
                        source_worldline_id=worldline_id,
                        requested_from_event_id=requested_from_event_id,
                    )
                )
            except HTTPException as exc:
                return {"error": str(exc.detail), "status_code": exc.status_code}, None

            timeout_s = self._coerce_int_arg(
                args.get("timeout_s"),
                default=300,
                minimum=1,
                maximum=1800,
            )
            max_iterations = self._coerce_int_arg(
                args.get("max_iterations"),
                default=8,
                minimum=1,
                maximum=100,
            )
            max_subagents = self._coerce_int_arg(
                args.get("max_subagents"),
                default=8,
                minimum=1,
                maximum=50,
            )
            max_parallel_subagents = self._coerce_int_arg(
                args.get("max_parallel_subagents"),
                default=3,
                minimum=1,
                maximum=10,
            )

            runner = SubagentRunner(
                llm_client=self._llm_client,
                worldline_service=self._worldline_service,
                spawn_subagents_blocking=self._spawn_subagents_blocking,
                get_turn_coordinator=self._get_turn_coordinator,
                run_child_turn=lambda child_worldline_id, child_message, child_max_iterations, allow_tools=True: (
                    self._run_child_turn(
                        child_worldline_id,
                        child_message,
                        child_max_iterations,
                        subagent_depth + 1,
                        allow_tools,
                    )
                ),
            )
            result = await runner.run(
                worldline_id=worldline_id,
                tool_call_id=tool_call.id or None,
                tasks=tasks if isinstance(tasks, list) else None,
                goal=goal if isinstance(goal, str) else None,
                requested_from_event_id=requested_from_event_id,
                from_event_id=from_event_id,
                from_event_resolution=from_event_resolution,
                timeout_s=timeout_s,
                max_iterations=max_iterations,
                max_subagents=max_subagents,
                max_parallel_subagents=max_parallel_subagents,
                on_event=on_event,
                on_delta=on_delta,
            )
            return result, None

        return {"error": f"unknown tool '{name}'"}, None

    @staticmethod
    def _coerce_int_arg(
        raw_value: Any,
        *,
        default: int,
        minimum: int,
        maximum: int,
    ) -> int:
        try:
            parsed = int(raw_value if raw_value is not None else default)
        except TypeError, ValueError:
            parsed = default
        return max(minimum, min(parsed, maximum))
