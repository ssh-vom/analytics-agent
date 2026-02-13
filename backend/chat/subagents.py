from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from typing import Any, Awaitable, Callable

from fastapi import HTTPException

from chat.jobs import WorldlineTurnCoordinator
from chat.llm_client import ChatMessage, LlmClient
from chat.runtime.capacity import CapacityLimitError, get_capacity_controller
from meta import get_conn, new_id
from worldline_service import BranchOptions, WorldlineService

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_S = 300
_DEFAULT_MAX_ITERATIONS = 8
_DEFAULT_MAX_SUBAGENTS = 8
_DEFAULT_MAX_PARALLEL_SUBAGENTS = 3
_MAX_SUBAGENTS = 50
_MAX_PARALLEL_SUBAGENTS = 10
_MAX_RETRIES_PER_SUBAGENT = 3
_RETRY_DELAY_BASE_SECONDS = 1.0
_RETRY_DELAY_MAX_SECONDS = 8.0
_LOOP_LIMIT_TEXT_MARKER = "i reached the tool-loop limit"
_LOOP_LIMIT_REASON = "max_iterations_reached"
_LOOP_LIMIT_FAILURE_CODE = "subagent_loop_limit"
_RETRYABLE_ERROR_SUBSTRINGS = (
    "429",  # Rate limit
    "503",  # Service unavailable
    "timeout",
    "connection",
    "network",
    "temporarily unavailable",
)


def resolve_worldline_head_event_id(worldline_id: str) -> str:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT head_event_id FROM worldlines WHERE id = ?",
            (worldline_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="worldline not found")
    head_event_id = str(row["head_event_id"] or "").strip()
    if not head_event_id:
        raise HTTPException(
            status_code=400,
            detail="spawn_subagents requires a non-empty worldline timeline",
        )
    return head_event_id


def resolve_fork_event_id_or_head(
    *,
    source_worldline_id: str,
    requested_from_event_id: str | None,
) -> tuple[str, str | None]:
    """Return a safe fork event id for branching.

    If requested event is missing or not in source history, fall back to current head.
    """
    with get_conn() as conn:
        worldline_row = conn.execute(
            "SELECT head_event_id FROM worldlines WHERE id = ?",
            (source_worldline_id,),
        ).fetchone()
        if worldline_row is None:
            raise HTTPException(status_code=404, detail="worldline not found")

        head_event_id = str(worldline_row["head_event_id"] or "").strip()
        if not head_event_id:
            raise HTTPException(
                status_code=400,
                detail="spawn_subagents requires a non-empty worldline timeline",
            )

        requested = str(requested_from_event_id or "").strip()
        if not requested:
            return head_event_id, "defaulted_to_current_head"
        if requested == head_event_id:
            return requested, None

        in_history = conn.execute(
            """
            WITH RECURSIVE chain AS (
                SELECT id, parent_event_id
                FROM events
                WHERE id = ?
                UNION ALL
                SELECT e.id, e.parent_event_id
                FROM events e
                JOIN chain ON chain.parent_event_id = e.id
            )
            SELECT 1 AS found
            FROM chain
            WHERE id = ?
            LIMIT 1
            """,
            (head_event_id, requested),
        ).fetchone()
        if in_history is not None:
            return requested, None

        exists_anywhere = conn.execute(
            "SELECT 1 AS found FROM events WHERE id = ? LIMIT 1",
            (requested,),
        ).fetchone()
        if exists_anywhere is None:
            return head_event_id, "requested_from_event_id_not_found_fell_back_to_head"
        return head_event_id, "requested_from_event_id_not_in_history_fell_back_to_head"


def _assistant_text_from_events(events: list[dict[str, Any]]) -> str | None:
    for event in reversed(events):
        if event.get("type") != "assistant_message":
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        text = payload.get("text")
        if isinstance(text, str) and text.strip():
            return text
    return None


def _assistant_payload_from_events(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in reversed(events):
        if event.get("type") != "assistant_message":
            continue
        payload = event.get("payload")
        if isinstance(payload, dict):
            return payload
    return None


def _state_trace_reasons(payload: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    trace = payload.get("state_trace")
    if not isinstance(trace, list):
        return reasons
    for step in trace:
        if not isinstance(step, dict):
            continue
        reason = step.get("reason")
        if isinstance(reason, str) and reason.strip():
            reasons.append(reason.strip())
    return reasons


def _terminal_reason_from_events(events: list[dict[str, Any]]) -> str | None:
    payload = _assistant_payload_from_events(events)
    if payload is None:
        return None
    reasons = _state_trace_reasons(payload)
    if _LOOP_LIMIT_REASON in reasons:
        return _LOOP_LIMIT_REASON
    if reasons:
        return reasons[-1]
    text = payload.get("text")
    if isinstance(text, str) and _LOOP_LIMIT_TEXT_MARKER in text.lower():
        return _LOOP_LIMIT_REASON
    return None


def _is_loop_limit_outcome(
    events: list[dict[str, Any]],
    *,
    assistant_text: str | None = None,
) -> bool:
    text = assistant_text if assistant_text is not None else _assistant_text_from_events(events)
    if isinstance(text, str) and _LOOP_LIMIT_TEXT_MARKER in text.lower():
        return True
    payload = _assistant_payload_from_events(events)
    if payload is None:
        return False
    return _LOOP_LIMIT_REASON in _state_trace_reasons(payload)


def _fallback_task_split(goal: str, *, max_tasks: int) -> list[dict[str, str]]:
    clean_goal = re.sub(r"\s+", " ", goal or "").strip()
    if not clean_goal:
        return []
    base = [
        {
            "label": "schema-scout",
            "message": (
                f"Investigate schema and relevant tables for this goal: {clean_goal}. "
                "Return only the key tables/columns needed."
            ),
        },
        {
            "label": "metrics-core",
            "message": (
                f"Compute the core metrics and primary findings for this goal: {clean_goal}. "
                "Focus on concise, high-signal results."
            ),
        },
        {
            "label": "quality-checks",
            "message": (
                f"Investigate anomalies, edge-cases, and caveats for this goal: {clean_goal}. "
                "Return risks, outliers, and confidence notes."
            ),
        },
    ]
    return base[: max(1, min(max_tasks, len(base)))]


async def derive_tasks_from_goal(
    *,
    llm_client: LlmClient,
    goal: str,
    max_tasks: int,
) -> list[dict[str, str]]:
    normalized_goal = (goal or "").strip()
    if not normalized_goal:
        return []

    prompt = (
        "Split the user goal into independent parallel analysis tasks. "
        "Return strict JSON with shape: "
        '{"tasks":[{"label":"short-id","message":"task prompt"}]}. '
        f"Create between 2 and {max(2, min(max_tasks, 10))} tasks. "
        "Each message must be concrete and self-contained. No markdown."
    )
    response = await llm_client.generate(
        messages=[
            ChatMessage(role="system", content=prompt),
            ChatMessage(role="user", content=normalized_goal),
        ],
        tools=[],
    )
    text = (response.text or "").strip()
    if not text:
        return _fallback_task_split(normalized_goal, max_tasks=max_tasks)

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return _fallback_task_split(normalized_goal, max_tasks=max_tasks)

    if not isinstance(parsed, dict) or not isinstance(parsed.get("tasks"), list):
        return _fallback_task_split(normalized_goal, max_tasks=max_tasks)

    output: list[dict[str, str]] = []
    for idx, entry in enumerate(parsed["tasks"]):
        if not isinstance(entry, dict):
            continue
        message = str(entry.get("message") or "").strip()
        if not message:
            continue
        label = (
            str(entry.get("label") or f"task-{idx + 1}").strip() or f"task-{idx + 1}"
        )
        output.append({"label": label[:80], "message": message[:4000]})
        if len(output) >= max_tasks:
            break

    if output:
        return output
    return _fallback_task_split(normalized_goal, max_tasks=max_tasks)


def _is_retryable_error(error_str: str) -> bool:
    """Check if an error is transient/retryable (rate limits, timeouts, etc.)."""
    if not error_str:
        return False
    error_lower = error_str.lower()
    return any(substr in error_lower for substr in _RETRYABLE_ERROR_SUBSTRINGS)


async def _run_with_retry(
    coro_fn: Callable[[], Awaitable[tuple[str, list[dict[str, Any]]]]],
    *,
    max_retries: int = _MAX_RETRIES_PER_SUBAGENT,
    base_delay: float = _RETRY_DELAY_BASE_SECONDS,
    max_delay: float = _RETRY_DELAY_MAX_SECONDS,
) -> tuple[str, list[dict[str, Any]]]:
    """Run a coroutine with exponential backoff retry for transient errors."""
    last_exception: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            return await coro_fn()
        except Exception as exc:
            last_exception = exc
            error_str = str(exc)

            # Check if this is a retryable error
            if not _is_retryable_error(error_str):
                raise

            # Don't retry on the last attempt
            if attempt >= max_retries:
                break

            # Calculate delay with exponential backoff and jitter
            delay = min(base_delay * (2**attempt), max_delay)
            jitter = random.uniform(0, 0.5)  # Add 0-50% jitter
            actual_delay = delay * (1 + jitter)

            logger.warning(
                "subagent retryable error (attempt %d/%d): %s. Retrying in %.2fs",
                attempt + 1,
                max_retries + 1,
                error_str[:200],
                actual_delay,
            )
            await asyncio.sleep(actual_delay)

    # All retries exhausted
    raise last_exception if last_exception else RuntimeError("All retries failed")


async def spawn_subagents_blocking(
    *,
    source_worldline_id: str,
    from_event_id: str,
    tasks: list[dict[str, Any]] | None,
    goal: str | None,
    tool_call_id: str | None,
    worldline_service: WorldlineService,
    llm_client: LlmClient,
    turn_coordinator: WorldlineTurnCoordinator,
    run_child_turn: Callable[
        [str, str, int, bool], Awaitable[tuple[str, list[dict[str, Any]]]]
    ],
    on_progress: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    on_prepared: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    timeout_s: int = _DEFAULT_TIMEOUT_S,
    max_iterations: int = _DEFAULT_MAX_ITERATIONS,
    max_subagents: int = _DEFAULT_MAX_SUBAGENTS,
    max_parallel_subagents: int = _DEFAULT_MAX_PARALLEL_SUBAGENTS,
) -> dict[str, Any]:
    fanout_group_id = new_id("fanout")
    normalized_timeout_s = max(1, min(int(timeout_s), 1800))
    normalized_max_iterations = max(1, min(int(max_iterations), 100))
    normalized_max_subagents = max(1, min(int(max_subagents), _MAX_SUBAGENTS))
    normalized_max_parallel = max(
        1, min(int(max_parallel_subagents), _MAX_PARALLEL_SUBAGENTS)
    )
    capacity = get_capacity_controller()

    resolved_tasks: list[dict[str, Any]] = []
    requested_task_count = 0
    if isinstance(tasks, list):
        requested_task_count = len(tasks)
        for item in tasks:
            if isinstance(item, dict):
                resolved_tasks.append(item)
            if len(resolved_tasks) >= normalized_max_subagents:
                break
    if not resolved_tasks and isinstance(goal, str) and goal.strip():
        derived = await derive_tasks_from_goal(
            llm_client=llm_client,
            goal=goal,
            max_tasks=normalized_max_subagents,
        )
        resolved_tasks = [
            {"label": t["label"], "message": t["message"]} for t in derived
        ]
    if not resolved_tasks:
        raise HTTPException(
            status_code=400,
            detail="spawn_subagents could not derive tasks from input",
        )
    if requested_task_count == 0:
        requested_task_count = len(resolved_tasks)
    accepted_task_count = len(resolved_tasks)
    truncated_task_count = max(0, requested_task_count - accepted_task_count)

    logger.info(
        "spawn_subagents_blocking: %d tasks, timeout=%ds, max_iter=%d, source=%s",
        len(resolved_tasks),
        normalized_timeout_s,
        normalized_max_iterations,
        source_worldline_id,
    )

    child_runs: list[dict[str, Any]] = []
    accepted_tasks: list[dict[str, Any]] = []
    for idx, task in enumerate(resolved_tasks):
        task_message = str(task.get("message") or "").strip()
        if not task_message:
            raise HTTPException(
                status_code=400,
                detail=f"spawn_subagents task #{idx + 1} message must be non-empty",
            )
        task_label_raw = str(task.get("label") or "").strip()
        task_label = task_label_raw or f"task-{idx + 1}"
        branch_name_raw = str(task.get("branch_name") or "").strip()
        branch_name = branch_name_raw or f"subagent-{idx + 1}"
        ordering_key = f"{fanout_group_id}:{idx}"

        branch = worldline_service.branch_from_event(
            BranchOptions(
                source_worldline_id=source_worldline_id,
                from_event_id=from_event_id,
                name=branch_name,
                append_events=False,
                carried_user_message=None,
            )
        )

        child_runs.append(
            {
                "task_index": idx,
                "task_label": task_label,
                "task_message": task_message,
                "child_run_id": new_id("childrun"),
                "child_worldline_id": branch.new_worldline_id,
                "branch_name": branch.name,
                "ordering_key": ordering_key,
                "status": "queued",
            }
        )
        accepted_tasks.append(
            {
                "task_index": idx,
                "task_label": task_label,
                "branch_name": branch.name,
                "child_worldline_id": branch.new_worldline_id,
                "ordering_key": ordering_key,
            }
        )

    if on_prepared is not None:
        await on_prepared(
            {
                "task_count": accepted_task_count,
                "requested_task_count": requested_task_count,
                "accepted_task_count": accepted_task_count,
                "truncated_task_count": truncated_task_count,
                "accepted_tasks": accepted_tasks,
            }
        )

    completed_count = 0
    failed_count = 0
    timed_out_count = 0
    task_results: list[dict[str, Any]] = []

    # Semaphore for bounded parallelism to avoid rate limits
    semaphore = asyncio.Semaphore(normalized_max_parallel)
    status_lock = asyncio.Lock()
    status_by_task_index = {
        int(run["task_index"]): "queued"
        for run in child_runs
    }
    progress_sequence = 0
    capacity_wait_total_ms = 0

    def _status_counters() -> dict[str, int]:
        counters = {
            "queued_count": 0,
            "running_count": 0,
            "completed_count": 0,
            "failed_count": 0,
            "timed_out_count": 0,
        }
        for status in status_by_task_index.values():
            if status == "queued":
                counters["queued_count"] += 1
            elif status == "running":
                counters["running_count"] += 1
            elif status == "completed":
                counters["completed_count"] += 1
            elif status == "timeout":
                counters["timed_out_count"] += 1
            else:
                counters["failed_count"] += 1
        return counters

    async def _emit_progress(
        *,
        run: dict[str, Any],
        status: str,
        phase: str,
        result_worldline_id: str | None = None,
        assistant_preview: str | None = None,
        error: str | None = None,
        queue_reason: str | None = None,
        retry_count: int = 0,
        force: bool = False,
    ) -> None:
        nonlocal progress_sequence
        if on_progress is None:
            return
        task_index = int(run.get("task_index", 0))
        async with status_lock:
            previous = status_by_task_index.get(task_index)
            if previous == status and not force:
                return
            status_by_task_index[task_index] = status
            counters = _status_counters()
            progress_sequence += 1
            group_seq = progress_sequence
        await on_progress(
            {
                "fanout_group_id": fanout_group_id,
                "group_seq": group_seq,
                "parent_tool_call_id": tool_call_id,
                "source_worldline_id": source_worldline_id,
                "from_event_id": from_event_id,
                "task_index": task_index,
                "task_label": run.get("task_label"),
                "task_status": status,
                "phase": phase,
                "task_count": accepted_task_count,
                "max_subagents": normalized_max_subagents,
                "max_parallel_subagents": normalized_max_parallel,
                "child_worldline_id": run.get("child_worldline_id"),
                "result_worldline_id": result_worldline_id,
                "ordering_key": run.get("ordering_key"),
                "assistant_preview": assistant_preview or "",
                "error": error,
                "queue_reason": queue_reason,
                "retry_count": retry_count,
                **counters,
            }
        )

    def _timeout_result(run: dict[str, Any], *, retry_count: int = 0) -> dict[str, Any]:
        return {
            **run,
            "status": "timeout",
            "error": (f"timed out after waiting {normalized_timeout_s}s for child run"),
            "failure_code": "subagent_timeout",
            "retry_count": retry_count,
            "recovered": False,
            "terminal_reason": "timeout",
            "result_worldline_id": run["child_worldline_id"],
            "assistant_preview": "",
            "assistant_text": None,
        }

    async def _run_one_with_semaphore(run: dict[str, Any]) -> dict[str, Any]:
        """Run a subagent with semaphore-controlled concurrency and retry logic."""
        nonlocal capacity_wait_total_ms
        child_wid = str(run["child_worldline_id"])
        task_label = str(run.get("task_label", ""))
        retry_count = 0

        async def _run_attempt(
            *,
            allow_tools: bool,
        ) -> dict[str, Any]:
            async def _factory() -> tuple[str, list[dict[str, Any]]]:
                return await run_child_turn(
                    child_wid,
                    str(run["task_message"]),
                    normalized_max_iterations,
                    allow_tools,
                )

            active_worldline_id, child_events = await _run_with_retry(
                lambda: turn_coordinator.run(child_wid, _factory),
            )
            assistant_text = _assistant_text_from_events(child_events)
            return {
                "result_worldline_id": active_worldline_id,
                "assistant_text": assistant_text,
                "assistant_preview": (assistant_text or "")[:220],
                "terminal_reason": _terminal_reason_from_events(child_events),
                "is_loop_limit": _is_loop_limit_outcome(
                    child_events, assistant_text=assistant_text
                ),
                "events_count": len(child_events),
            }

        try:
            async with semaphore:
                try:
                    async with capacity.lease_subagent() as lease:
                        capacity_wait_total_ms += lease.wait_ms
                        await _emit_progress(
                            run=run,
                            status="running",
                            phase="started",
                            queue_reason=lease.queue_reason,
                            retry_count=retry_count,
                        )
                        logger.info(
                            "subagent _run_one starting: label=%s worldline=%s (parallel limit: %d)",
                            task_label,
                            child_wid,
                            normalized_max_parallel,
                        )

                        try:
                            initial_attempt = await _run_attempt(allow_tools=True)
                            final_attempt = initial_attempt
                            recovered = False

                            if initial_attempt["is_loop_limit"]:
                                retry_count = 1
                                await _emit_progress(
                                    run=run,
                                    status="running",
                                    phase="retrying",
                                    result_worldline_id=initial_attempt[
                                        "result_worldline_id"
                                    ],
                                    assistant_preview=initial_attempt[
                                        "assistant_preview"
                                    ],
                                    retry_count=retry_count,
                                    force=True,
                                )
                                final_attempt = await _run_attempt(allow_tools=False)
                                recovered = not bool(final_attempt["is_loop_limit"])

                            if not recovered and retry_count == 1:
                                error_str = (
                                    "subagent reached tool-loop limit after synthesis-only retry"
                                )
                                await _emit_progress(
                                    run=run,
                                    status="failed",
                                    phase="finished",
                                    result_worldline_id=final_attempt[
                                        "result_worldline_id"
                                    ],
                                    assistant_preview=final_attempt[
                                        "assistant_preview"
                                    ],
                                    error=error_str,
                                    retry_count=retry_count,
                                )
                                logger.warning(
                                    "subagent _run_one loop-limit terminal: label=%s worldline=%s",
                                    task_label,
                                    final_attempt["result_worldline_id"],
                                )
                                return {
                                    **run,
                                    "status": "failed",
                                    "error": error_str,
                                    "failure_code": _LOOP_LIMIT_FAILURE_CODE,
                                    "retry_count": retry_count,
                                    "recovered": False,
                                    "terminal_reason": _LOOP_LIMIT_REASON,
                                    "result_worldline_id": final_attempt[
                                        "result_worldline_id"
                                    ],
                                    "assistant_preview": final_attempt[
                                        "assistant_preview"
                                    ],
                                    "assistant_text": final_attempt["assistant_text"],
                                }

                            await _emit_progress(
                                run=run,
                                status="completed",
                                phase="finished",
                                result_worldline_id=final_attempt["result_worldline_id"],
                                assistant_preview=final_attempt["assistant_preview"],
                                retry_count=retry_count,
                            )
                            logger.info(
                                "subagent _run_one completed: label=%s worldline=%s events=%d retry_count=%d recovered=%s",
                                task_label,
                                final_attempt["result_worldline_id"],
                                int(final_attempt["events_count"]),
                                retry_count,
                                recovered,
                            )
                            return {
                                **run,
                                "status": "completed",
                                "error": None,
                                "failure_code": None,
                                "retry_count": retry_count,
                                "recovered": recovered,
                                "terminal_reason": final_attempt["terminal_reason"],
                                "result_worldline_id": final_attempt[
                                    "result_worldline_id"
                                ],
                                "assistant_preview": final_attempt[
                                    "assistant_preview"
                                ],
                                "assistant_text": final_attempt["assistant_text"],
                            }
                        except asyncio.CancelledError:
                            logger.warning(
                                "subagent _run_one cancelled: label=%s worldline=%s",
                                task_label,
                                child_wid,
                            )
                            timeout_result = _timeout_result(
                                run, retry_count=retry_count
                            )
                            await _emit_progress(
                                run=run,
                                status="timeout",
                                phase="finished",
                                result_worldline_id=str(
                                    timeout_result.get("result_worldline_id") or child_wid
                                ),
                                error=str(timeout_result.get("error") or ""),
                                retry_count=retry_count,
                            )
                            return timeout_result
                        except Exception as exc:
                            error_str = str(exc)
                            logger.error(
                                "subagent _run_one failed: label=%s worldline=%s error=%s",
                                task_label,
                                child_wid,
                                error_str[:4000],
                                exc_info=True,
                            )
                            await _emit_progress(
                                run=run,
                                status="failed",
                                phase="finished",
                                result_worldline_id=run["child_worldline_id"],
                                error=error_str[:4000],
                                retry_count=retry_count,
                            )
                            return {
                                **run,
                                "status": "failed",
                                "error": error_str[:4000],
                                "failure_code": "subagent_error",
                                "retry_count": retry_count,
                                "recovered": False,
                                "terminal_reason": "error",
                                "result_worldline_id": run["child_worldline_id"],
                                "assistant_preview": "",
                                "assistant_text": None,
                            }
                except CapacityLimitError as exc:
                    await _emit_progress(
                        run=run,
                        status="failed",
                        phase="finished",
                        result_worldline_id=run["child_worldline_id"],
                        error=str(exc),
                        queue_reason="capacity_limit_reached",
                        retry_count=retry_count,
                    )
                    return {
                        **run,
                        "status": "failed",
                        "error": str(exc),
                        "error_code": "subagent_capacity_limit_reached",
                        "failure_code": "subagent_capacity_limit_reached",
                        "retry_count": retry_count,
                        "recovered": False,
                        "terminal_reason": "capacity_limit_reached",
                        "result_worldline_id": run["child_worldline_id"],
                        "assistant_preview": "",
                        "assistant_text": None,
                    }
        except asyncio.CancelledError:
            timeout_result = _timeout_result(run, retry_count=retry_count)
            await _emit_progress(
                run=run,
                status="timeout",
                phase="finished",
                result_worldline_id=str(timeout_result.get("result_worldline_id") or child_wid),
                error=str(timeout_result.get("error") or ""),
                retry_count=retry_count,
            )
            return timeout_result

    for run in child_runs:
        await _emit_progress(
            run=run,
            status="queued",
            phase="queued",
            result_worldline_id=str(run.get("child_worldline_id") or ""),
            force=True,
        )

    # Create all tasks but they will respect the semaphore
    task_to_run: dict[asyncio.Task[dict[str, Any]], dict[str, Any]] = {
        asyncio.create_task(_run_one_with_semaphore(run)): run for run in child_runs
    }

    # Wait for all tasks with global timeout
    done, pending = await asyncio.wait(
        set(task_to_run.keys()), timeout=normalized_timeout_s
    )

    # Cancel stragglers and wait for them to finish
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    # Collect results from ALL tasks (done + formerly-pending)
    all_tasks = list(done) + list(pending)
    for task in all_tasks:
        run = task_to_run[task]
        try:
            result = task.result()
        except asyncio.CancelledError:
            result = _timeout_result(run, retry_count=0)
            await _emit_progress(
                run=run,
                status="timeout",
                phase="finished",
                result_worldline_id=str(result.get("result_worldline_id") or ""),
                error=str(result.get("error") or ""),
                retry_count=int(result.get("retry_count") or 0),
            )
        except Exception as exc:
            result = {
                **run,
                "status": "failed",
                "error": str(exc)[:4000],
                "failure_code": "subagent_error",
                "retry_count": 0,
                "recovered": False,
                "terminal_reason": "error",
                "result_worldline_id": run["child_worldline_id"],
                "assistant_preview": "",
                "assistant_text": None,
            }
            await _emit_progress(
                run=run,
                status="failed",
                phase="finished",
                result_worldline_id=str(result.get("result_worldline_id") or ""),
                error=str(result.get("error") or ""),
                retry_count=int(result.get("retry_count") or 0),
            )
        if result["status"] == "completed":
            completed_count += 1
        elif result["status"] == "timeout":
            timed_out_count += 1
        else:
            failed_count += 1
        task_results.append(result)

    sorted_tasks = sorted(task_results, key=lambda item: int(item.get("task_index", 0)))
    loop_limit_failure_count = sum(
        1
        for task in sorted_tasks
        if str(task.get("failure_code") or "") == _LOOP_LIMIT_FAILURE_CODE
    )
    retried_task_count = sum(
        1 for task in sorted_tasks if int(task.get("retry_count") or 0) > 0
    )
    recovered_task_count = sum(1 for task in sorted_tasks if bool(task.get("recovered")))
    failure_summary: dict[str, int] = {}
    for task in sorted_tasks:
        failure_code = str(task.get("failure_code") or "").strip()
        if not failure_code:
            continue
        failure_summary[failure_code] = failure_summary.get(failure_code, 0) + 1

    partial_failure = failed_count > 0 or timed_out_count > 0
    return {
        "fanout_group_id": fanout_group_id,
        "parent_tool_call_id": tool_call_id,
        "source_worldline_id": source_worldline_id,
        "from_event_id": from_event_id,
        "task_count": accepted_task_count,
        "requested_task_count": requested_task_count,
        "accepted_task_count": accepted_task_count,
        "truncated_task_count": truncated_task_count,
        "accepted_tasks": accepted_tasks,
        "max_subagents": normalized_max_subagents,
        "max_parallel_subagents": normalized_max_parallel,
        "capacity_wait_ms": capacity_wait_total_ms,
        "completed_count": completed_count,
        "failed_count": failed_count,
        "timed_out_count": timed_out_count,
        "loop_limit_failure_count": loop_limit_failure_count,
        "retried_task_count": retried_task_count,
        "recovered_task_count": recovered_task_count,
        "failure_summary": failure_summary,
        "all_completed": not partial_failure,
        "partial_failure": partial_failure,
        "tasks": sorted_tasks,
    }
