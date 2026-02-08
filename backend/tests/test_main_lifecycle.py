import asyncio
import unittest
from unittest.mock import Mock, patch

import main


class FakeSandboxManager:
    def __init__(self) -> None:
        self.reap_calls = 0
        self.shutdown_calls = 0
        self.reap_seen = asyncio.Event()

    async def reap_idle(self, ttl_seconds: int):
        self.reap_calls += 1
        self.reap_seen.set()
        return []

    async def shutdown_all(self):
        self.shutdown_calls += 1
        return []


class MainLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_and_shutdown_manage_reaper_task(self) -> None:
        manager = FakeSandboxManager()
        old_interval = main.REAPER_INTERVAL_SECONDS
        old_ttl = main.IDLE_TTL_SECONDS
        task = None

        with (
            patch.object(main, "get_sandbox_manager", return_value=manager),
            patch.object(main, "init_meta_db", Mock()),
        ):
            main.REAPER_INTERVAL_SECONDS = 0.01
            main.IDLE_TTL_SECONDS = 1

            await main.startup()
            try:
                self.assertTrue(hasattr(main.app.state, "sandbox_reaper_task"))
                task = main.app.state.sandbox_reaper_task
                self.assertIsInstance(task, asyncio.Task)

                await asyncio.wait_for(manager.reap_seen.wait(), timeout=0.2)
                self.assertGreaterEqual(manager.reap_calls, 1)
            finally:
                await main.shutdown()
                main.REAPER_INTERVAL_SECONDS = old_interval
                main.IDLE_TTL_SECONDS = old_ttl

        self.assertEqual(manager.shutdown_calls, 1)
        self.assertIsNotNone(task)
        self.assertTrue(task.done())


if __name__ == "__main__":
    unittest.main()
