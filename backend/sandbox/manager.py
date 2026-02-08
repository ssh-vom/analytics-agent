from __future__ import annotations

from typing import Protocol, Any
import time
import asyncio
from dataclasses import dataclass, field


class SandboxRunner(Protocol):
    async def execute(
        self,
        worldline_id: str,
        code: str,
        timeout_s: int,
    ) -> dict[str, Any]: ...


@dataclass
class SandboxHandle:
    worldline_id: str
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_used_monotonic: float = field(default_factory=time.monotonic)


class SandboxManager:
    def __init__(self, runner: SandboxRunner) -> None:
        self._runner = runner
        self._handles: dict[str, SandboxHandle] = {}
        self._manager_lock = asyncio.Lock()

    async def get_or_create(self, worldline_id: str) -> SandboxHandle:
        async with self._manager_lock:
            handle = self._handles.get(worldline_id)
            if handle is None:
                handle = SandboxHandle(worldline_id=worldline_id)
                self._handles[worldline_id] = handle

            return handle

    async def execute(
        self,
        worldline_id: str,
        code: str,
        timeout_s: int = 30,
    ) -> dict[str, Any]:
        handle = await self.get_or_create(worldline_id)
        async with handle.lock:
            result = await self._runner.execute(
                worldline_id=worldline_id,
                code=code,
                timeout_s=timeout_s,
            )
            handle.last_used_monotonic = time.monotonic()
            return result

    async def reap_idle(self, ttl_seconds: int) -> list[str]:
        now = time.monotonic()
        evicted: list[str] = []

        async with self._manager_lock:
            for worldline_id, handle in list(self._handles.items()):
                idle_for = now - handle.last_used_monotonic
                if idle_for >= ttl_seconds and not handle.lock.locked():
                    del self._handles[worldline_id]
                    evicted.append(worldline_id)

        return evicted

    async def shutdown_all(self) -> list[str]:
        async with self._manager_lock:
            ids = list(self._handles.keys())
            self._handles.clear()
            return ids

    def active_worldlines(self) -> list[str]:
        return list(self._handles.keys())
