from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from chat.runtime.capacity import CapacityLimitError, get_capacity_controller
from meta import get_conn, new_id

T = TypeVar("T")

JOB_STATUS_QUEUED = "queued"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_COMPLETED = "completed"
JOB_STATUS_FAILED = "failed"
JOB_STATUS_CANCELLED = "cancelled"

FINAL_JOB_STATUSES = {
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
    JOB_STATUS_CANCELLED,
}


@dataclass
class _WorldlineTask:
    factory: Callable[[], Awaitable[Any]]
    future: asyncio.Future[Any]


class WorldlineTurnCoordinator:
    """Serializes all turn execution per worldline while allowing parallel worldlines."""

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[_WorldlineTask]] = {}
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    async def run(self, worldline_id: str, factory: Callable[[], Awaitable[T]]) -> T:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[T] = loop.create_future()

        async with self._lock:
            queue = self._queues.get(worldline_id)
            if queue is None:
                queue = asyncio.Queue()
                self._queues[worldline_id] = queue
            await queue.put(_WorldlineTask(factory=factory, future=future))

            worker = self._workers.get(worldline_id)
            if worker is None or worker.done():
                self._workers[worldline_id] = asyncio.create_task(
                    self._worker_loop(worldline_id)
                )

        return await future

    async def shutdown(self) -> None:
        async with self._lock:
            workers = list(self._workers.values())
            self._workers.clear()
            queues = list(self._queues.values())
            self._queues.clear()

        for queue in queues:
            while not queue.empty():
                try:
                    queued = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if not queued.future.done():
                    queued.future.set_exception(
                        RuntimeError("worldline turn coordinator is shutting down")
                    )

        for worker in workers:
            worker.cancel()
        for worker in workers:
            try:
                await worker
            except asyncio.CancelledError:
                pass

    async def _worker_loop(self, worldline_id: str) -> None:
        while True:
            async with self._lock:
                queue = self._queues.get(worldline_id)
                if queue is None:
                    self._workers.pop(worldline_id, None)
                    return

            queued_task = await queue.get()
            try:
                result = await queued_task.factory()
                if not queued_task.future.done():
                    queued_task.future.set_result(result)
            except Exception as exc:
                if not queued_task.future.done():
                    queued_task.future.set_exception(exc)
            finally:
                queue.task_done()

            async with self._lock:
                queue_again = self._queues.get(worldline_id)
                if queue_again is queue and queue.empty():
                    self._queues.pop(worldline_id, None)
                    self._workers.pop(worldline_id, None)
                    return


class ChatJobScheduler:
    """Background scheduler for queued chat turns."""

    def __init__(
        self,
        *,
        turn_coordinator: WorldlineTurnCoordinator,
        engine_factory: Callable[[str | None, str | None, int], Any] | None = None,
        turn_runner: Callable[
            [str, str, str | None, str | None, int],
            Awaitable[tuple[str, list[dict[str, Any]]]],
        ]
        | None = None,
    ) -> None:
        self._turn_coordinator = turn_coordinator
        if turn_runner is not None:
            self._turn_runner = turn_runner
        else:
            if engine_factory is None:
                raise ValueError(
                    "engine_factory is required when turn_runner is not set"
                )

            async def _default_turn_runner(
                worldline_id: str,
                message: str,
                provider: str | None,
                model: str | None,
                max_iterations: int,
            ) -> tuple[str, list[dict[str, Any]]]:
                engine = engine_factory(provider, model, max_iterations)
                return await engine.run_turn(worldline_id=worldline_id, message=message)

            self._turn_runner = _default_turn_runner
        self._started = False
        self._start_lock = asyncio.Lock()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._tasks_lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._start_lock:
            if self._started:
                return
            self._started = True

            with get_conn() as conn:
                conn.execute(
                    """
                    UPDATE chat_turn_jobs
                    SET status = ?, started_at = NULL
                    WHERE status = ?
                    """,
                    (JOB_STATUS_QUEUED, JOB_STATUS_RUNNING),
                )
                rows = conn.execute(
                    """
                    SELECT id
                    FROM chat_turn_jobs
                    WHERE status = ?
                    ORDER BY datetime(created_at) ASC, id ASC
                    """,
                    (JOB_STATUS_QUEUED,),
                ).fetchall()
                conn.commit()

            for row in rows:
                await self._schedule_without_start(str(row["id"]))

    async def shutdown(self) -> None:
        async with self._tasks_lock:
            tasks = list(self._tasks.values())
            self._tasks.clear()

        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def schedule(self, job_id: str) -> None:
        await self.start()
        await self._schedule_without_start(job_id)

    async def _schedule_without_start(self, job_id: str) -> None:
        """Schedule a job task assuming the scheduler is already initialized."""
        if not self._started:
            return

        async with self._tasks_lock:
            existing = self._tasks.get(job_id)
            if existing is not None and not existing.done():
                return

            task = asyncio.create_task(self._run_job(job_id))
            self._tasks[job_id] = task
            task.add_done_callback(
                lambda _: asyncio.create_task(self._clear_task(job_id))
            )

    async def _clear_task(self, job_id: str) -> None:
        async with self._tasks_lock:
            task = self._tasks.get(job_id)
            if task is not None and task.done():
                self._tasks.pop(job_id, None)

    async def _run_job(self, job_id: str) -> None:
        row = self._load_job(job_id)
        if row is None:
            return
        if row["status"] != JOB_STATUS_QUEUED:
            return

        worldline_id = str(row["worldline_id"])
        request_payload = json.loads(row["request_json"])

        async def execute() -> None:
            if not self._mark_running(job_id):
                return

            try:
                async with get_capacity_controller().lease_turn():
                    active_worldline_id, events = await self._turn_runner(
                        worldline_id,
                        str(request_payload.get("message") or ""),
                        request_payload.get("provider"),
                        request_payload.get("model"),
                        int(request_payload.get("max_iterations") or 6),
                    )
                self._mark_completed(
                    job_id=job_id,
                    result_worldline_id=active_worldline_id,
                    events=events,
                )
            except CapacityLimitError as exc:
                self._mark_failed(job_id, str(exc))
            except Exception as exc:
                self._mark_failed(job_id, str(exc))

        await self._turn_coordinator.run(worldline_id, execute)

    def _load_job(self, job_id: str):
        with get_conn() as conn:
            return conn.execute(
                """
                SELECT id, worldline_id, request_json, status
                FROM chat_turn_jobs
                WHERE id = ?
                """,
                (job_id,),
            ).fetchone()

    def _mark_running(self, job_id: str) -> bool:
        with get_conn() as conn:
            cursor = conn.execute(
                """
                UPDATE chat_turn_jobs
                SET status = ?, started_at = CURRENT_TIMESTAMP, error = NULL
                WHERE id = ? AND status = ?
                """,
                (JOB_STATUS_RUNNING, job_id, JOB_STATUS_QUEUED),
            )
            conn.commit()
            return cursor.rowcount > 0

    def _mark_completed(
        self,
        *,
        job_id: str,
        result_worldline_id: str,
        events: list[dict[str, Any]],
    ) -> None:
        summary = self._build_summary(events)
        with get_conn() as conn:
            conn.execute(
                """
                UPDATE chat_turn_jobs
                SET
                    status = ?,
                    result_worldline_id = ?,
                    result_summary_json = ?,
                    error = NULL,
                    finished_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    JOB_STATUS_COMPLETED,
                    result_worldline_id,
                    json.dumps(summary, ensure_ascii=True),
                    job_id,
                ),
            )
            conn.commit()

    def _mark_failed(self, job_id: str, error: str) -> None:
        with get_conn() as conn:
            conn.execute(
                """
                UPDATE chat_turn_jobs
                SET
                    status = ?,
                    error = ?,
                    finished_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (JOB_STATUS_FAILED, error[:4000], job_id),
            )
            conn.commit()

    @staticmethod
    def _build_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
        assistant_text = ""
        for event in reversed(events):
            if event.get("type") == "assistant_message":
                payload = event.get("payload") or {}
                if isinstance(payload, dict):
                    assistant_text = str(payload.get("text") or "")
                break

        return {
            "event_count": len(events),
            "assistant_preview": assistant_text[:220],
        }


def enqueue_chat_turn_job(
    *,
    thread_id: str,
    worldline_id: str,
    message: str,
    provider: str | None,
    model: str | None,
    max_iterations: int,
    parent_job_id: str | None = None,
    fanout_group_id: str | None = None,
    task_label: str | None = None,
    parent_tool_call_id: str | None = None,
) -> str:
    job_id = new_id("job")
    request_json = json.dumps(
        {
            "message": message,
            "provider": provider,
            "model": model,
            "max_iterations": max_iterations,
        },
        ensure_ascii=True,
    )

    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO chat_turn_jobs
            (
                id,
                thread_id,
                worldline_id,
                request_json,
                parent_job_id,
                fanout_group_id,
                task_label,
                parent_tool_call_id,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                thread_id,
                worldline_id,
                request_json,
                parent_job_id,
                fanout_group_id,
                task_label,
                parent_tool_call_id,
                JOB_STATUS_QUEUED,
            ),
        )
        conn.commit()

    return job_id
