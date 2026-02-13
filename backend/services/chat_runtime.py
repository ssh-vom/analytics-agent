"""
Chat runtime services - job scheduling and turn coordination.
"""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

from chat import build_llm_client
from chat.jobs import (
    ChatJobScheduler,
    WorldlineTurnCoordinator,
)

if TYPE_CHECKING:
    from chat.engine import ChatEngine


def _build_chat_engine_from_params(
    provider: str | None,
    model: str | None,
    max_iterations: int,
) -> ChatEngine:
    try:
        llm_client = build_llm_client(provider=provider, model=model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    from chat.engine import ChatEngine

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
