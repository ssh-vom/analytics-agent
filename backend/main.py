import contextlib
from fastapi import FastAPI
import os
import asyncio

from env_loader import load_env_once
from meta import init_meta_db
from api import (
    artifacts_router,
    chat_router,
    seed_data_router,
    threads_router,
    tools_router,
    worldlines_router,
)
from services.tool_executor import get_sandbox_manager
from services.chat_runtime import (
    shutdown_chat_runtime,
    start_chat_runtime,
)

REAPER_INTERVAL_SECONDS = int(os.getenv("SANDBOX_REAPER_INTERVAL_SECONDS", "60"))
IDLE_TTL_SECONDS = int(os.getenv("SANDBOX_IDLE_TTL_SECONDS", "900"))

app = FastAPI(title="Agent Core Backend")


async def _sandbox_reaper_loop(stop_event: asyncio.Event) -> None:
    manager = get_sandbox_manager()
    while not stop_event.is_set():
        try:
            await manager.reap_idle(ttl_seconds=IDLE_TTL_SECONDS)
        except Exception:
            pass

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=REAPER_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            continue


@app.on_event("startup")
async def startup() -> None:
    load_env_once()
    init_meta_db()
    stop_event = asyncio.Event()
    app.state.sandbox_reaper_stop_event = stop_event
    app.state.sandbox_reaper_task = asyncio.create_task(
        _sandbox_reaper_loop(stop_event)
    )
    await start_chat_runtime()


@app.on_event("shutdown")
async def shutdown() -> None:
    stop_event = getattr(app.state, "sandbox_reaper_stop_event", None)
    task = getattr(app.state, "sandbox_reaper_task", None)
    if stop_event is not None:
        stop_event.set()

    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    await shutdown_chat_runtime()
    await get_sandbox_manager().shutdown_all()


app.include_router(tools_router)
app.include_router(artifacts_router)
app.include_router(threads_router)
app.include_router(worldlines_router)
app.include_router(chat_router)
app.include_router(seed_data_router)


@app.get("/")
async def root():
    return {"message": "Hello World"}
