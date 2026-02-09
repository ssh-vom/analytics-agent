from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

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
