from __future__ import annotations

import asyncio
import os
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator


class CapacityLimitError(RuntimeError):
    pass


@dataclass(frozen=True)
class CapacityLease:
    wait_ms: int
    queue_reason: str | None


class _CapacityPool:
    def __init__(
        self,
        *,
        name: str,
        max_concurrency: int,
        max_queue: int,
    ) -> None:
        self.name = name
        self.max_concurrency = max(1, int(max_concurrency))
        self.max_queue = max(0, int(max_queue))
        self._semaphore = asyncio.Semaphore(self.max_concurrency)
        self._lock = asyncio.Lock()
        self._waiters = 0
        self._active = 0

    async def _acquire(self) -> CapacityLease:
        start = time.perf_counter()

        async with self._lock:
            if self._waiters >= self.max_queue:
                raise CapacityLimitError(
                    f"{self.name} queue limit reached ({self.max_queue})"
                )
            self._waiters += 1

        try:
            await self._semaphore.acquire()
        finally:
            async with self._lock:
                self._waiters = max(0, self._waiters - 1)

        wait_ms = int((time.perf_counter() - start) * 1000)
        async with self._lock:
            self._active += 1

        return CapacityLease(
            wait_ms=wait_ms,
            queue_reason="capacity_wait" if wait_ms > 0 else None,
        )

    async def _release(self) -> None:
        async with self._lock:
            self._active = max(0, self._active - 1)
        self._semaphore.release()

    @asynccontextmanager
    async def lease(self) -> AsyncIterator[CapacityLease]:
        lease = await self._acquire()
        try:
            yield lease
        finally:
            await self._release()

    async def snapshot(self) -> dict[str, int]:
        async with self._lock:
            available = getattr(self._semaphore, "_value", 0)
            return {
                "max": self.max_concurrency,
                "active": self._active,
                "queued": self._waiters,
                "available": int(available),
            }


class CapacityController:
    def __init__(
        self,
        *,
        max_turn_concurrency: int,
        max_turn_queue: int,
        max_subagent_concurrency: int,
        max_subagent_queue: int,
        max_python_concurrency: int,
        max_python_queue: int,
    ) -> None:
        self._turn_pool = _CapacityPool(
            name="turn",
            max_concurrency=max_turn_concurrency,
            max_queue=max_turn_queue,
        )
        self._subagent_pool = _CapacityPool(
            name="subagent",
            max_concurrency=max_subagent_concurrency,
            max_queue=max_subagent_queue,
        )
        self._python_pool = _CapacityPool(
            name="python",
            max_concurrency=max_python_concurrency,
            max_queue=max_python_queue,
        )

    @asynccontextmanager
    async def lease_turn(self) -> AsyncIterator[CapacityLease]:
        async with self._turn_pool.lease() as lease:
            yield lease

    @asynccontextmanager
    async def lease_subagent(self) -> AsyncIterator[CapacityLease]:
        async with self._subagent_pool.lease() as lease:
            yield lease

    @asynccontextmanager
    async def lease_python(self) -> AsyncIterator[CapacityLease]:
        async with self._python_pool.lease() as lease:
            yield lease

    async def snapshot(self) -> dict[str, dict[str, int]]:
        return {
            "turn": await self._turn_pool.snapshot(),
            "subagent": await self._subagent_pool.snapshot(),
            "python": await self._python_pool.snapshot(),
        }


def _from_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


_capacity_lock = threading.Lock()
_capacity_controller: CapacityController | None = None


def get_capacity_controller() -> CapacityController:
    global _capacity_controller

    with _capacity_lock:
        if _capacity_controller is not None:
            return _capacity_controller

        _capacity_controller = CapacityController(
            max_turn_concurrency=_from_env("CHAT_TURN_MAX_CONCURRENCY", 64),
            max_turn_queue=_from_env("CHAT_TURN_MAX_QUEUE", 512),
            max_subagent_concurrency=_from_env("CHAT_SUBAGENT_MAX_CONCURRENCY", 12),
            max_subagent_queue=_from_env("CHAT_SUBAGENT_MAX_QUEUE", 256),
            max_python_concurrency=_from_env("CHAT_PYTHON_MAX_CONCURRENCY", 16),
            max_python_queue=_from_env("CHAT_PYTHON_MAX_QUEUE", 256),
        )
        return _capacity_controller
