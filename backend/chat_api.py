from __future__ import annotations

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
    max_iterations: int = Field(default=6, ge=1, le=20)


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
        "data: " + json.dumps(payload, ensure_ascii=True, default=str, separators=(",", ":"))
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
        seq = 0
        try:
            active_worldline_id, events = await engine.run_turn(
                worldline_id=body.worldline_id,
                message=body.message,
            )
            for event in events:
                seq += 1
                yield _encode_sse_frame(
                    {
                        "seq": seq,
                        "worldline_id": active_worldline_id,
                        "event": event,
                    },
                    event="event",
                    event_id=seq,
                )

            seq += 1
            yield _encode_sse_frame(
                {
                    "seq": seq,
                    "worldline_id": active_worldline_id,
                    "done": True,
                },
                event="done",
                event_id=seq,
            )
        except Exception as exc:
            seq += 1
            yield _encode_sse_frame(
                {"seq": seq, "error": str(exc)},
                event="error",
                event_id=seq,
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
