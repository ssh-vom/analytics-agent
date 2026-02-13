from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException

from chat.runtime.event_writer import (
    append_event_with_callback,
    append_event_with_parent_callback,
)
from worldline_service import WorldlineService
from chat.llm_client import LlmClient


class SubagentRunner:
    """Coordinates spawn_subagents event persistence + progress stream wiring."""

    def __init__(
        self,
        *,
        llm_client: LlmClient,
        worldline_service: WorldlineService,
        spawn_subagents_blocking: Callable[..., Awaitable[dict[str, Any]]],
        get_turn_coordinator: Callable[[], Any],
        run_child_turn: Callable[
            [str, str, int, bool], Awaitable[tuple[str, list[dict[str, Any]]]]
        ],
    ) -> None:
        self._llm_client = llm_client
        self._worldline_service = worldline_service
        self._spawn_subagents_blocking = spawn_subagents_blocking
        self._get_turn_coordinator = get_turn_coordinator
        self._run_child_turn = run_child_turn

    async def run(
        self,
        *,
        worldline_id: str,
        tool_call_id: str | None,
        tasks: list[dict[str, Any]] | None,
        goal: str | None,
        requested_from_event_id: str | None,
        from_event_id: str,
        from_event_resolution: str | None,
        timeout_s: int,
        max_iterations: int,
        max_subagents: int,
        max_parallel_subagents: int,
        on_event: Callable[[str, dict[str, Any]], Awaitable[None]] | None,
        on_delta: Callable[[str, dict[str, Any]], Awaitable[None]] | None,
    ) -> dict[str, Any]:
        call_event: dict[str, Any] | None = None

        def _with_aggregate_defaults(payload: dict[str, Any]) -> dict[str, Any]:
            normalized: dict[str, Any] = dict(payload)
            normalized.setdefault("loop_limit_failure_count", 0)
            normalized.setdefault("retried_task_count", 0)
            normalized.setdefault("recovered_task_count", 0)
            normalized.setdefault("failure_summary", {})
            tasks = normalized.get("tasks")
            if not isinstance(tasks, list):
                return normalized

            normalized_tasks: list[Any] = []
            for task in tasks:
                if not isinstance(task, dict):
                    normalized_tasks.append(task)
                    continue
                normalized_task = dict(task)
                normalized_task.setdefault("failure_code", None)
                normalized_task.setdefault("retry_count", 0)
                normalized_task.setdefault("recovered", False)
                normalized_task.setdefault("terminal_reason", None)
                normalized_tasks.append(normalized_task)
            normalized["tasks"] = normalized_tasks
            return normalized

        async def _emit_progress(progress: dict[str, Any]) -> None:
            if on_delta is None:
                return
            await on_delta(
                worldline_id,
                {
                    "type": "subagent_progress",
                    "call_id": tool_call_id,
                    **progress,
                },
            )

        async def _persist_call_event(prepared: dict[str, Any]) -> None:
            nonlocal call_event
            if call_event is not None:
                return
            call_event = await append_event_with_callback(
                worldline_id=worldline_id,
                event_type="tool_call_subagents",
                payload={
                    "goal": goal,
                    "tasks": tasks,
                    "requested_from_event_id": requested_from_event_id,
                    "from_event_id": from_event_id,
                    "from_event_resolution": from_event_resolution,
                    "timeout_s": timeout_s,
                    "max_iterations": max_iterations,
                    "max_subagents": max_subagents,
                    "max_parallel_subagents": max_parallel_subagents,
                    "call_id": tool_call_id,
                    **prepared,
                },
                on_event=on_event,
            )

        async def _persist_result(payload: dict[str, Any]) -> None:
            nonlocal call_event
            if call_event is None:
                await _persist_call_event(
                    {
                        "task_count": 0,
                        "requested_task_count": 0,
                        "accepted_task_count": 0,
                        "truncated_task_count": 0,
                        "accepted_tasks": [],
                    }
                )
            await append_event_with_parent_callback(
                worldline_id=worldline_id,
                parent_event_id=call_event.get("id") if call_event else None,
                event_type="tool_result_subagents",
                payload=payload,
                on_event=on_event,
            )

        try:
            result = await self._spawn_subagents_blocking(
                source_worldline_id=worldline_id,
                from_event_id=from_event_id,
                tasks=tasks,
                goal=goal,
                tool_call_id=tool_call_id,
                worldline_service=self._worldline_service,
                llm_client=self._llm_client,
                turn_coordinator=self._get_turn_coordinator(),
                run_child_turn=self._run_child_turn,
                on_progress=_emit_progress,
                on_prepared=_persist_call_event,
                timeout_s=timeout_s,
                max_iterations=max_iterations,
                max_subagents=max_subagents,
                max_parallel_subagents=max_parallel_subagents,
            )
            result = _with_aggregate_defaults(result)
            await _persist_result(result)
            return result
        except HTTPException as exc:
            payload = _with_aggregate_defaults(
                {"error": str(exc.detail), "status_code": exc.status_code}
            )
            await _persist_result(payload)
            return payload
        except asyncio.CancelledError as exc:  # pragma: no cover
            payload = _with_aggregate_defaults(
                {"error": str(exc), "error_type": type(exc).__name__}
            )
            await _persist_result(payload)
            return payload
        except Exception as exc:  # pragma: no cover
            payload = _with_aggregate_defaults({"error": str(exc)})
            await _persist_result(payload)
            return payload
