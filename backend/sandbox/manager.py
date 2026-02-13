from __future__ import annotations

from typing import Protocol, Any
import time
import asyncio
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class SandboxRunner(Protocol):
    async def start(self, worldline_id: str) -> str: ...
    async def execute(
        self,
        sandbox_id: str,
        worldline_id: str,
        code: str,
        timeout_s: int,
    ) -> dict[str, Any]: ...
    async def stop(self, sandbox_id: str) -> None: ...


@dataclass
class SandboxHandle:
    worldline_id: str
    sandbox_id: str
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_used_monotonic: float = field(default_factory=time.monotonic)


class SandboxManager:
    def __init__(self, runner: SandboxRunner) -> None:
        self._runner = runner
        self._handles: dict[str, SandboxHandle] = {}
        self._creating: dict[str, asyncio.Future[SandboxHandle]] = {}
        self._manager_lock = asyncio.Lock()

    async def get_or_create(self, worldline_id: str) -> SandboxHandle:
        creator = False
        async with self._manager_lock:
            handle = self._handles.get(worldline_id)
            if handle is not None:
                return handle

            creating = self._creating.get(worldline_id)
            if creating is None:
                creating = asyncio.get_running_loop().create_future()
                self._creating[worldline_id] = creating
                creator = True

        if not creator:
            return await creating

        try:
            sandbox_id = await self._runner.start(worldline_id)
            handle = SandboxHandle(worldline_id=worldline_id, sandbox_id=sandbox_id)
            async with self._manager_lock:
                self._handles[worldline_id] = handle
                self._creating.pop(worldline_id, None)
            creating.set_result(handle)
            return handle
        except Exception as exc:
            async with self._manager_lock:
                self._creating.pop(worldline_id, None)
            creating.set_exception(exc)
            raise

    async def execute(
        self,
        worldline_id: str,
        code: str,
        timeout_s: int = 30,
    ) -> dict[str, Any]:
        handle = await self.get_or_create(worldline_id)
        async with handle.lock:
            result = await self._runner.execute(
                sandbox_id=handle.sandbox_id,
                worldline_id=worldline_id,
                code=code,
                timeout_s=timeout_s,
            )
            handle.last_used_monotonic = time.monotonic()

            # Invalidate sandbox on timeout or hard errors to ensure clean state next time
            error = result.get("error") if isinstance(result, dict) else None
            if error and self._should_invalidate_sandbox(error):
                logger.warning(
                    "Invalidating sandbox for worldline %s due to error: %s",
                    worldline_id,
                    str(error)[:200],
                )
                await self._invalidate_handle(worldline_id, handle.sandbox_id)

            return result

    def _should_invalidate_sandbox(self, error: str) -> bool:
        """Check if an error warrants sandbox invalidation."""
        if not error:
            return False
        error_lower = error.lower()
        # Invalidate on timeouts, container errors, or resource exhaustion
        indicators = [
            "timed out",
            "timeout",
            "container",
            "docker",
            "resource",
            "memory",
            "killed",
            "signal",
        ]
        return any(ind in error_lower for ind in indicators)

    async def _invalidate_handle(self, worldline_id: str, sandbox_id: str) -> None:
        """Remove a sandbox handle and stop the container."""
        async with self._manager_lock:
            self._handles.pop(worldline_id, None)
        try:
            await self._runner.stop(sandbox_id)
        except Exception:
            # Best effort - ignore cleanup errors
            pass

    async def reap_idle(self, ttl_seconds: int) -> list[str]:
        now = time.monotonic()
        to_stop: list[tuple[str, str]] = []
        evicted: list[str] = []

        async with self._manager_lock:
            for worldline_id, handle in list(self._handles.items()):
                idle_for = now - handle.last_used_monotonic
                if idle_for >= ttl_seconds and not handle.lock.locked():
                    del self._handles[worldline_id]
                    to_stop.append((worldline_id, handle.sandbox_id))

        for worldline_id, sandbox_id in to_stop:
            await self._runner.stop(sandbox_id)
            evicted.append(worldline_id)

        return evicted

    async def shutdown_all(self) -> list[str]:
        async with self._manager_lock:
            handles = list(self._handles.values())
            self._handles.clear()
            creating = list(self._creating.values())
            self._creating.clear()

        for future in creating:
            if not future.done():
                future.cancel()

        for handle in handles:
            await self._runner.stop(handle.sandbox_id)

        return [handle.worldline_id for handle in handles]

    def active_worldlines(self) -> list[str]:
        return list(self._handles.keys())
