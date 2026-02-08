import contextlib
from fastapi import FastAPI
import os
import asyncio

try:
    from backend.meta import init_meta_db
    from backend.threads import router as threads_router
    from backend.worldlines import router as wordlines_router
    from backend.tools import router as tools_router, get_sandbox_manager
    from backend.artifacts import router as artifacts_router
except ModuleNotFoundError:
    from meta import init_meta_db
    from threads import router as threads_router
    from worldlines import router as wordlines_router
    from tools import router as tools_router, get_sandbox_manager
    from artifacts import router as artifacts_router

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
    init_meta_db()
    stop_event = asyncio.Event()
    app.state.sandbox_reaper_stop_event = stop_event
    app.state.sandbox_reaper_task = asyncio.create_task(
        _sandbox_reaper_loop(stop_event)
    )


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

    await get_sandbox_manager().shutdown_all()


app.include_router(tools_router)
app.include_router(artifacts_router)
app.include_router(threads_router)
app.include_router(wordlines_router)


@app.get("/")
async def root():
    return {"message": "Hello World"}
