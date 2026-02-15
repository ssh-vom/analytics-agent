from __future__ import annotations

from typing import Protocol, Any
import time
import asyncio
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Conservative defaults for demo - prevents resource exhaustion
DEFAULT_MAX_SANDBOXES = 3
DEFAULT_MAX_QUEUE = 16


class SandboxCapacityError(RuntimeError):
    """Raised when sandbox pool is at capacity and queue is full."""

    pass


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
    """Manages a pool of sandboxes with global capacity limiting.

    Key features:
    - Hard limit on concurrent sandboxes (default: 3) to prevent resource exhaustion
    - Queues requests when at capacity (up to max_queue)
    - One sandbox per worldline, reused for subsequent executions
    - Idle sandbox reaping with proper locking to avoid races
    """

    def __init__(
        self,
        runner: SandboxRunner,
        *,
        max_sandboxes: int = DEFAULT_MAX_SANDBOXES,
        max_queue: int = DEFAULT_MAX_QUEUE,
    ) -> None:
        self._runner = runner
        self._handles: dict[str, SandboxHandle] = {}
        self._creating: dict[str, asyncio.Future[SandboxHandle]] = {}
        self._manager_lock = asyncio.Lock()

        # Global sandbox pool limiting
        self._max_sandboxes = max(1, int(max_sandboxes))
        self._max_queue = max(0, int(max_queue))
        self._sandbox_semaphore = asyncio.Semaphore(self._max_sandboxes)
        self._queue_lock = asyncio.Lock()
        self._queued_count = 0

    async def get_or_create(self, worldline_id: str) -> SandboxHandle:
        """Get existing sandbox or create new one, respecting global capacity.

        Raises SandboxCapacityError if at capacity and queue is full.
        """
        creator = False
        needs_semaphore = False

        async with self._manager_lock:
            # Fast path: sandbox already exists
            handle = self._handles.get(worldline_id)
            if handle is not None:
                return handle

            # Check if creation already in progress
            creating = self._creating.get(worldline_id)
            if creating is None:
                creating = asyncio.get_running_loop().create_future()
                self._creating[worldline_id] = creating
                creator = True
                needs_semaphore = True

        if not creator:
            # Wait for the existing creation to complete
            return await creating

        # We're the creator - need to acquire a sandbox slot
        try:
            # Check queue limit before waiting
            async with self._queue_lock:
                if self._queued_count >= self._max_queue:
                    raise SandboxCapacityError(
                        f"Sandbox queue full ({self._max_queue} waiting). "
                        "Try again later."
                    )
                self._queued_count += 1

            try:
                # Wait for a sandbox slot (blocks if at capacity)
                await self._sandbox_semaphore.acquire()
            finally:
                async with self._queue_lock:
                    self._queued_count = max(0, self._queued_count - 1)

            # Create the sandbox
            sandbox_id = await self._runner.start(worldline_id)
            handle = SandboxHandle(worldline_id=worldline_id, sandbox_id=sandbox_id)

            async with self._manager_lock:
                self._handles[worldline_id] = handle
                self._creating.pop(worldline_id, None)

            creating.set_result(handle)
            logger.info(
                "Created sandbox %s for worldline %s (pool: %d/%d)",
                sandbox_id[:16],
                worldline_id[:8],
                len(self._handles),
                self._max_sandboxes,
            )
            return handle

        except SandboxCapacityError:
            # Clean up and propagate capacity error
            async with self._manager_lock:
                self._creating.pop(worldline_id, None)
            creating.set_exception(SandboxCapacityError("Sandbox pool at capacity"))
            raise

        except Exception as exc:
            # Release semaphore on failure (if we acquired it)
            if needs_semaphore:
                self._sandbox_semaphore.release()
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
        """Remove a sandbox handle and stop the container, releasing the pool slot."""
        removed = False
        async with self._manager_lock:
            if worldline_id in self._handles:
                del self._handles[worldline_id]
                removed = True
        try:
            await self._runner.stop(sandbox_id)
        except Exception:
            # Best effort - ignore cleanup errors
            pass
        # Release semaphore slot after stopping
        if removed:
            self._sandbox_semaphore.release()
            logger.info(
                "Invalidated sandbox %s (pool: %d/%d)",
                sandbox_id[:16],
                len(self._handles),
                self._max_sandboxes,
            )

    async def reap_idle(self, ttl_seconds: int) -> list[str]:
        """Reap idle sandboxes, releasing pool slots.

        Fixed race condition: lock check is done while holding manager lock.
        """
        now = time.monotonic()
        to_stop: list[tuple[str, str]] = []
        evicted: list[str] = []

        async with self._manager_lock:
            for worldline_id, handle in list(self._handles.items()):
                idle_for = now - handle.last_used_monotonic
                # Only reap if idle AND not currently locked (checked under manager lock)
                if idle_for >= ttl_seconds and not handle.lock.locked():
                    del self._handles[worldline_id]
                    to_stop.append((worldline_id, handle.sandbox_id))

        for worldline_id, sandbox_id in to_stop:
            try:
                await self._runner.stop(sandbox_id)
            except Exception:
                pass  # Best effort cleanup
            # Release semaphore slot
            self._sandbox_semaphore.release()
            evicted.append(worldline_id)
            logger.info(
                "Reaped idle sandbox for worldline %s (pool: %d/%d)",
                worldline_id[:8],
                len(self._handles),
                self._max_sandboxes,
            )

        return evicted

    async def shutdown_all(self) -> list[str]:
        """Shutdown all sandboxes and release all pool slots."""
        async with self._manager_lock:
            handles = list(self._handles.values())
            self._handles.clear()
            creating = list(self._creating.values())
            self._creating.clear()

        for future in creating:
            if not future.done():
                future.cancel()

        for handle in handles:
            try:
                await self._runner.stop(handle.sandbox_id)
            except Exception:
                pass  # Best effort cleanup
            # Release semaphore slot
            self._sandbox_semaphore.release()

        logger.info("Shutdown %d sandboxes", len(handles))
        return [handle.worldline_id for handle in handles]

    def active_worldlines(self) -> list[str]:
        return list(self._handles.keys())

    def pool_status(self) -> dict[str, int]:
        """Return current pool status for observability."""
        return {
            "active": len(self._handles),
            "max": self._max_sandboxes,
            "available": self._sandbox_semaphore._value,
            "queued": self._queued_count,
            "max_queue": self._max_queue,
        }
