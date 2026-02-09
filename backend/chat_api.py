from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from fastapi.responses import StreamingResponse

try:
    from backend.chat import build_llm_client
    from backend.chat.engine import ChatEngine
except ModuleNotFoundError:
    from chat import build_llm_client
    from chat.engine import ChatEngine


router = APIRouter(prefix="/api", tags=["chat"])


class ChatRequest(BaseModel):
    worldline_id: str
    message: str
    provider: str | None = None
    model: str | None = None
    max_iterations: int = Field(default=6, ge=1, le=100)


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


@router.post("/chat")
async def chat(body: ChatRequest):
    try:
        llm_client = build_llm_client(provider=body.provider, model=body.model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    engine = ChatEngine(
        llm_client=llm_client,
        max_iterations=body.max_iterations,
    )
    active_worldline_id, events = await engine.run_turn(
        worldline_id=body.worldline_id,
        message=body.message,
    )
    return {"worldline_id": active_worldline_id, "events": events}


@router.post("/chat/stream")
async def chat_stream(body: ChatRequest):
    try:
        llm_client = build_llm_client(provider=body.provider, model=body.model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    engine = ChatEngine(
        llm_client=llm_client,
        max_iterations=body.max_iterations,
    )

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
                active_worldline_id, _ = await engine.run_turn(
                    worldline_id=body.worldline_id,
                    message=body.message,
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
