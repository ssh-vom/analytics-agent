from __future__ import annotations

import asyncio
import contextlib
import json
import threading
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from fastapi.responses import StreamingResponse

try:
    from backend.chat import build_llm_client
    from backend.chat.engine import ChatEngine
    from backend.chat.jobs import (
        JOB_STATUS_CANCELLED,
        JOB_STATUS_COMPLETED,
        JOB_STATUS_FAILED,
        JOB_STATUS_QUEUED,
        JOB_STATUS_RUNNING,
        ChatJobScheduler,
        WorldlineTurnCoordinator,
        enqueue_chat_turn_job,
    )
    from backend.meta import get_conn
except ModuleNotFoundError:
    from chat import build_llm_client
    from chat.engine import ChatEngine
    from chat.jobs import (
        JOB_STATUS_CANCELLED,
        JOB_STATUS_COMPLETED,
        JOB_STATUS_FAILED,
        JOB_STATUS_QUEUED,
        JOB_STATUS_RUNNING,
        ChatJobScheduler,
        WorldlineTurnCoordinator,
        enqueue_chat_turn_job,
    )
    from meta import get_conn


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


def _build_chat_engine_from_params(
    provider: str | None,
    model: str | None,
    max_iterations: int,
) -> ChatEngine:
    try:
        llm_client = build_llm_client(provider=provider, model=model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return ChatEngine(
        llm_client=llm_client,
        max_iterations=max_iterations,
    )


async def _run_chat_turn_from_params(
    worldline_id: str,
    message: str,
    provider: str | None,
    model: str | None,
    max_iterations: int,
) -> tuple[str, list[dict[str, Any]]]:
    engine = _build_chat_engine_from_params(provider, model, max_iterations)
    return await engine.run_turn(
        worldline_id=worldline_id,
        message=message,
    )


_runtime_lock = threading.Lock()
_runtime_loop: asyncio.AbstractEventLoop | None = None
_turn_coordinator: WorldlineTurnCoordinator | None = None
_chat_job_scheduler: ChatJobScheduler | None = None


def _ensure_chat_runtime() -> tuple[WorldlineTurnCoordinator, ChatJobScheduler]:
    global _runtime_loop, _turn_coordinator, _chat_job_scheduler

    loop = asyncio.get_running_loop()
    with _runtime_lock:
        if _runtime_loop is loop and _turn_coordinator and _chat_job_scheduler:
            return _turn_coordinator, _chat_job_scheduler

        _runtime_loop = loop
        coordinator = WorldlineTurnCoordinator()
        scheduler = ChatJobScheduler(
            turn_coordinator=coordinator,
            turn_runner=_run_chat_turn_from_params,
        )
        _turn_coordinator = coordinator
        _chat_job_scheduler = scheduler
        return coordinator, scheduler


def get_turn_coordinator() -> WorldlineTurnCoordinator:
    coordinator, _ = _ensure_chat_runtime()
    return coordinator


def get_chat_job_scheduler() -> ChatJobScheduler:
    _, scheduler = _ensure_chat_runtime()
    return scheduler


async def start_chat_runtime() -> None:
    _, scheduler = _ensure_chat_runtime()
    await scheduler.start()


async def shutdown_chat_runtime() -> None:
    coordinator, scheduler = _ensure_chat_runtime()
    await scheduler.shutdown()
    await coordinator.shutdown()


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
    turn_coordinator: WorldlineTurnCoordinator,
    body: ChatRequest,
    *,
    on_event=None,
    on_delta=None,
) -> tuple[str, list[dict[str, Any]]]:
    async def run_now() -> tuple[str, list[dict[str, Any]]]:
        return await _run_chat_turn(body, on_event=on_event, on_delta=on_delta)

    return await turn_coordinator.run(body.worldline_id, run_now)


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
                task.cancel()
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
