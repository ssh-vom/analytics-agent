"""
API routes for chat operations.
Thin route layer - runtime logic lives in services/chat_runtime.py
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from chat import build_llm_client
from chat.engine import ChatEngine
from chat.jobs import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    enqueue_chat_turn_job,
)
from chat.runtime.capacity import CapacityLimitError, get_capacity_controller
from meta import get_conn
from services.chat_runtime import (
    _ensure_chat_runtime,
    get_turn_coordinator,
)

router = APIRouter(prefix="/api", tags=["chat"])


class ChatRequest(BaseModel):
    worldline_id: str
    message: str
    provider: str | None = None
    model: str | None = None
    max_iterations: int = Field(default=6, ge=1, le=100)


class ChatJobRequest(BaseModel):
    worldline_id: str
    message: str
    provider: str | None = None
    model: str | None = None
    max_iterations: int = Field(default=6, ge=1, le=100)


class ChatJobAckRequest(BaseModel):
    seen: bool = True


ALLOWED_JOB_STATUSES = {
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
    JOB_STATUS_CANCELLED,
}


def _build_chat_engine(body: ChatRequest) -> ChatEngine:
    try:
        llm_client = build_llm_client(provider=body.provider, model=body.model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return ChatEngine(
        llm_client=llm_client,
        max_iterations=body.max_iterations,
    )


def _resolve_worldline_thread_id(worldline_id: str) -> str:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT thread_id FROM worldlines WHERE id = ?",
            (worldline_id,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="worldline not found")
    return str(row["thread_id"])


def _parse_status_filter(raw: str | None) -> list[str]:
    if raw is None or not raw.strip():
        return []
    statuses = [part.strip().lower() for part in raw.split(",") if part.strip()]
    invalid = [status for status in statuses if status not in ALLOWED_JOB_STATUSES]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"invalid job status filter: {', '.join(invalid)}",
        )
    return statuses


def _job_row_to_dict(row) -> dict[str, Any]:
    request_payload = {}
    summary_payload = None
    try:
        request_payload = json.loads(row["request_json"])
    except Exception:
        request_payload = {}

    if row["result_summary_json"]:
        try:
            summary_payload = json.loads(row["result_summary_json"])
        except Exception:
            summary_payload = None

    return {
        "id": row["id"],
        "thread_id": row["thread_id"],
        "worldline_id": row["worldline_id"],
        "parent_job_id": row["parent_job_id"] if "parent_job_id" in row.keys() else None,
        "fanout_group_id": (
            row["fanout_group_id"] if "fanout_group_id" in row.keys() else None
        ),
        "task_label": row["task_label"] if "task_label" in row.keys() else None,
        "parent_tool_call_id": (
            row["parent_tool_call_id"] if "parent_tool_call_id" in row.keys() else None
        ),
        "status": row["status"],
        "error": row["error"],
        "request": request_payload,
        "result_worldline_id": row["result_worldline_id"],
        "result_summary": summary_payload,
        "seen_at": row["seen_at"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
    }


def _load_job(job_id: str) -> dict[str, Any]:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT
                id,
                thread_id,
                worldline_id,
                request_json,
                parent_job_id,
                fanout_group_id,
                task_label,
                parent_tool_call_id,
                status,
                error,
                result_worldline_id,
                result_summary_json,
                seen_at,
                created_at,
                started_at,
                finished_at
            FROM chat_turn_jobs
            WHERE id = ?
            """,
            (job_id,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="chat job not found")
    return _job_row_to_dict(row)


def _encode_sse_frame(
    payload: dict[str, Any],
    *,
    event: str = "event",
    event_id: int | None = None,
) -> str:
    lines: list[str] = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append(
        "data: "
        + json.dumps(payload, ensure_ascii=True, default=str, separators=(",", ":"))
    )
    return "\n".join(lines) + "\n\n"


async def _run_chat_turn(
    body: ChatRequest,
    *,
    on_event=None,
    on_delta=None,
) -> tuple[str, list[dict[str, Any]]]:
    engine = _build_chat_engine(body)
    return await engine.run_turn(
        worldline_id=body.worldline_id,
        message=body.message,
        on_event=on_event,
        on_delta=on_delta,
    )


async def _run_chat_turn_serialized(
    turn_coordinator,
    body: ChatRequest,
    *,
    on_event=None,
    on_delta=None,
) -> tuple[str, list[dict[str, Any]]]:
    async def run_now() -> tuple[str, list[dict[str, Any]]]:
        async with get_capacity_controller().lease_turn():
            return await _run_chat_turn(body, on_event=on_event, on_delta=on_delta)

    try:
        return await turn_coordinator.run(body.worldline_id, run_now)
    except CapacityLimitError as exc:
        raise HTTPException(
            status_code=429,
            detail={
                "error": str(exc),
                "error_code": "turn_capacity_limit_reached",
            },
        ) from exc


@router.post("/chat")
async def chat(body: ChatRequest):
    turn_coordinator, scheduler = _ensure_chat_runtime()
    await scheduler.start()

    active_worldline_id, events = await _run_chat_turn_serialized(
        turn_coordinator,
        body,
    )
    return {"worldline_id": active_worldline_id, "events": events}


@router.post("/chat/stream")
async def chat_stream(body: ChatRequest):
    turn_coordinator, scheduler = _ensure_chat_runtime()
    await scheduler.start()

    async def event_stream() -> AsyncIterator[str]:
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        seq = 0

        async def on_event(worldline_id: str, event: dict[str, Any]) -> None:
            nonlocal seq
            seq += 1
            await queue.put(
                _encode_sse_frame(
                    {
                        "seq": seq,
                        "worldline_id": worldline_id,
                        "event": event,
                    },
                    event="event",
                    event_id=seq,
                )
            )

        async def on_delta(worldline_id: str, delta: dict[str, Any]) -> None:
            nonlocal seq
            seq += 1
            await queue.put(
                _encode_sse_frame(
                    {
                        "seq": seq,
                        "worldline_id": worldline_id,
                        "delta": delta,
                    },
                    event="delta",
                    event_id=seq,
                )
            )

        async def run_engine() -> None:
            nonlocal seq
            try:
                active_worldline_id, _ = await _run_chat_turn_serialized(
                    turn_coordinator,
                    body,
                    on_event=on_event,
                    on_delta=on_delta,
                )
                seq += 1
                await queue.put(
                    _encode_sse_frame(
                        {
                            "seq": seq,
                            "worldline_id": active_worldline_id,
                            "done": True,
                        },
                        event="done",
                        event_id=seq,
                    )
                )
            except Exception as exc:
                seq += 1
                await queue.put(
                    _encode_sse_frame(
                        {"seq": seq, "error": str(exc)},
                        event="error",
                        event_id=seq,
                    )
                )
            finally:
                await queue.put(None)

        task = asyncio.create_task(run_engine())
        try:
            while True:
                frame = await queue.get()
                if frame is None:
                    break
                yield frame
        finally:
            if not task.done():
                # Do not cancel the active turn on client disconnect; let backend
                # finish and persist terminal events (especially subagent fan-out results).
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/chat/jobs")
async def create_chat_job(body: ChatJobRequest):
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="message must not be empty")

    _, scheduler = _ensure_chat_runtime()
    await scheduler.start()
    thread_id = _resolve_worldline_thread_id(body.worldline_id)
    job_id = enqueue_chat_turn_job(
        thread_id=thread_id,
        worldline_id=body.worldline_id,
        message=message,
        provider=body.provider,
        model=body.model,
        max_iterations=body.max_iterations,
    )
    await scheduler.schedule(job_id)

    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT
                id,
                thread_id,
                worldline_id,
                request_json,
                parent_job_id,
                fanout_group_id,
                task_label,
                parent_tool_call_id,
                status,
                error,
                result_worldline_id,
                result_summary_json,
                seen_at,
                created_at,
                started_at,
                finished_at
            FROM chat_turn_jobs
            WHERE id = ?
            """,
            (job_id,),
        ).fetchone()
        position_row = conn.execute(
            """
            SELECT COUNT(*) AS queue_position
            FROM chat_turn_jobs
            WHERE worldline_id = ?
              AND status IN (?, ?)
              AND datetime(created_at) <= datetime((SELECT created_at FROM chat_turn_jobs WHERE id = ?))
            """,
            (
                body.worldline_id,
                JOB_STATUS_QUEUED,
                JOB_STATUS_RUNNING,
                job_id,
            ),
        ).fetchone()

    result = _job_row_to_dict(row)
    result["queue_position"] = int(position_row["queue_position"])
    return result


@router.get("/chat/jobs")
async def list_chat_jobs(
    thread_id: str | None = None,
    worldline_id: str | None = None,
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
):
    statuses = _parse_status_filter(status)
    where_clauses: list[str] = []
    params: list[Any] = []

    if thread_id:
        where_clauses.append("thread_id = ?")
        params.append(thread_id)
    if worldline_id:
        where_clauses.append("worldline_id = ?")
        params.append(worldline_id)
    if statuses:
        placeholders = ",".join(["?"] * len(statuses))
        where_clauses.append(f"status IN ({placeholders})")
        params.extend(statuses)

    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)

    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT
                id,
                thread_id,
                worldline_id,
                request_json,
                parent_job_id,
                fanout_group_id,
                task_label,
                parent_tool_call_id,
                status,
                error,
                result_worldline_id,
                result_summary_json,
                seen_at,
                created_at,
                started_at,
                finished_at
            FROM chat_turn_jobs
            {where_sql}
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()

    return {
        "jobs": [_job_row_to_dict(row) for row in rows],
        "count": len(rows),
    }


@router.get("/chat/session")
async def get_chat_session(thread_id: str):
    with get_conn() as conn:
        thread_row = conn.execute(
            """
            SELECT
                t.id,
                t.title,
                t.created_at,
                COUNT(CASE WHEN e.type IN ('user_message', 'assistant_message') THEN 1 END) AS message_count,
                COALESCE(MAX(e.created_at), t.created_at) AS last_activity
            FROM threads t
            LEFT JOIN worldlines w ON w.thread_id = t.id
            LEFT JOIN events e ON e.worldline_id = w.id
            WHERE t.id = ?
            GROUP BY t.id, t.title, t.created_at
            """,
            (thread_id,),
        ).fetchone()
        if thread_row is None:
            raise HTTPException(status_code=404, detail="thread not found")

        worldline_rows = conn.execute(
            """
            WITH event_stats AS (
                SELECT
                    worldline_id,
                    COUNT(
                        CASE
                            WHEN type IN ('user_message', 'assistant_message') THEN 1
                        END
                    ) AS message_count,
                    MAX(created_at) AS last_event_at
                FROM events
                GROUP BY worldline_id
            ),
            job_stats AS (
                SELECT
                    worldline_id,
                    SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END) AS queued_jobs,
                    SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS running_jobs,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed_jobs,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_jobs,
                    SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled_jobs,
                    MAX(COALESCE(finished_at, started_at, created_at)) AS last_job_activity
                FROM chat_turn_jobs
                GROUP BY worldline_id
            )
            SELECT
                w.id,
                w.parent_worldline_id,
                w.forked_from_event_id,
                w.head_event_id,
                w.name,
                w.created_at,
                COALESCE(es.message_count, 0) AS message_count,
                es.last_event_at,
                COALESCE(js.queued_jobs, 0) AS queued_jobs,
                COALESCE(js.running_jobs, 0) AS running_jobs,
                COALESCE(js.completed_jobs, 0) AS completed_jobs,
                COALESCE(js.failed_jobs, 0) AS failed_jobs,
                COALESCE(js.cancelled_jobs, 0) AS cancelled_jobs,
                (
                    SELECT j.status
                    FROM chat_turn_jobs j
                    WHERE j.worldline_id = w.id
                    ORDER BY datetime(COALESCE(j.finished_at, j.started_at, j.created_at)) DESC,
                             datetime(j.created_at) DESC,
                             j.id DESC
                    LIMIT 1
                ) AS latest_job_status,
                COALESCE(
                    CASE
                        WHEN js.last_job_activity IS NOT NULL AND es.last_event_at IS NOT NULL THEN
                            CASE
                                WHEN datetime(js.last_job_activity) >= datetime(es.last_event_at)
                                    THEN js.last_job_activity
                                ELSE es.last_event_at
                            END
                        WHEN js.last_job_activity IS NOT NULL THEN js.last_job_activity
                        WHEN es.last_event_at IS NOT NULL THEN es.last_event_at
                        ELSE w.created_at
                    END,
                    w.created_at
                ) AS last_activity
            FROM worldlines w
            LEFT JOIN event_stats es ON es.worldline_id = w.id
            LEFT JOIN job_stats js ON js.worldline_id = w.id
            WHERE w.thread_id = ?
            ORDER BY w.rowid ASC
            """,
            (thread_id,),
        ).fetchall()

        job_rows = conn.execute(
            """
            SELECT
                id,
                thread_id,
                worldline_id,
                request_json,
                parent_job_id,
                fanout_group_id,
                task_label,
                parent_tool_call_id,
                status,
                error,
                result_worldline_id,
                result_summary_json,
                seen_at,
                created_at,
                started_at,
                finished_at
            FROM chat_turn_jobs
            WHERE thread_id = ?
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT 500
            """,
            (thread_id,),
        ).fetchall()

    worldlines: list[dict[str, Any]] = []
    for row in worldline_rows:
        worldlines.append(
            {
                "id": row["id"],
                "parent_worldline_id": row["parent_worldline_id"],
                "forked_from_event_id": row["forked_from_event_id"],
                "head_event_id": row["head_event_id"],
                "name": row["name"],
                "created_at": row["created_at"],
                "message_count": int(row["message_count"]),
                "last_event_at": row["last_event_at"],
                "last_activity": row["last_activity"],
                "jobs": {
                    "queued": int(row["queued_jobs"]),
                    "running": int(row["running_jobs"]),
                    "completed": int(row["completed_jobs"]),
                    "failed": int(row["failed_jobs"]),
                    "cancelled": int(row["cancelled_jobs"]),
                    "latest_status": row["latest_job_status"],
                },
            }
        )

    jobs = [_job_row_to_dict(row) for row in job_rows]
    preferred_worldline_id: str | None = None
    active_jobs = [
        job
        for job in jobs
        if job["status"] in {JOB_STATUS_QUEUED, JOB_STATUS_RUNNING}
    ]
    if active_jobs:
        active_jobs.sort(
            key=lambda job: (
                1 if job["status"] == JOB_STATUS_RUNNING else 0,
                str(job.get("started_at") or job.get("created_at") or ""),
                str(job.get("id") or ""),
            ),
            reverse=True,
        )
        preferred_worldline_id = str(active_jobs[0]["worldline_id"])
    elif worldlines:
        preferred_worldline_id = str(worldlines[0]["id"])

    return {
        "thread": {
            "id": thread_row["id"],
            "title": thread_row["title"],
            "created_at": thread_row["created_at"],
            "message_count": int(thread_row["message_count"]),
            "last_activity": thread_row["last_activity"],
        },
        "worldlines": worldlines,
        "jobs": jobs,
        "preferred_worldline_id": preferred_worldline_id,
    }


@router.get("/chat/runtime")
async def get_chat_runtime_snapshot():
    return {
        "capacity": await get_capacity_controller().snapshot(),
    }


@router.get("/chat/jobs/{job_id}")
async def get_chat_job(job_id: str):
    return _load_job(job_id)


@router.post("/chat/jobs/{job_id}/ack")
async def ack_chat_job(job_id: str, body: ChatJobAckRequest):
    _ = _load_job(job_id)
    with get_conn() as conn:
        if body.seen:
            conn.execute(
                """
                UPDATE chat_turn_jobs
                SET seen_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (job_id,),
            )
        else:
            conn.execute(
                """
                UPDATE chat_turn_jobs
                SET seen_at = NULL
                WHERE id = ?
                """,
                (job_id,),
            )
        conn.commit()

    return _load_job(job_id)
