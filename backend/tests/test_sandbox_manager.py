import asyncio
import time
import unittest

from sandbox.manager import SandboxManager


class FakeSessionRunner:
    def __init__(self, delay: float = 0.05) -> None:
        self.delay = delay
        self.start_calls: list[str] = []
        self.stop_calls: list[str] = []
        self.execute_calls: list[tuple[str, str, str, int, float, float]] = []

    async def start(self, worldline_id: str) -> str:
        self.start_calls.append(worldline_id)
        await asyncio.sleep(0)
        return f"sb_{worldline_id}_{len(self.start_calls)}"

    async def stop(self, sandbox_id: str) -> None:
        self.stop_calls.append(sandbox_id)
        await asyncio.sleep(0)

    async def execute(
        self,
        sandbox_id: str,
        worldline_id: str,
        code: str,
        timeout_s: int,
    ):
        start = time.monotonic()
        await asyncio.sleep(self.delay)
        end = time.monotonic()
        self.execute_calls.append((sandbox_id, worldline_id, code, timeout_s, start, end))
        return {
            "stdout": code,
            "stderr": "",
            "error": None,
            "artifacts": [],
            "previews": {},
        }


class SandboxManagerTests(unittest.TestCase):
    def _run(self, coro):
        return asyncio.run(coro)

    def test_worldline_sticky_handle(self):
        async def case():
            runner = FakeSessionRunner()
            manager = SandboxManager(runner)
            h1 = await manager.get_or_create("w1")
            h2 = await manager.get_or_create("w1")
            self.assertIs(h1, h2)
            self.assertEqual(runner.start_calls, ["w1"])

        self._run(case())

    def test_same_worldline_is_serialized(self):
        async def case():
            runner = FakeSessionRunner(delay=0.08)
            manager = SandboxManager(runner)

            await asyncio.gather(
                manager.execute("w1", "a", 30),
                manager.execute("w1", "b", 30),
            )

            self.assertEqual(len(runner.start_calls), 1)
            self.assertEqual(len(runner.execute_calls), 2)
            _, _, _, _, s1, e1 = runner.execute_calls[0]
            _, _, _, _, s2, _ = runner.execute_calls[1]
            self.assertGreaterEqual(s2, e1)

        self._run(case())

    def test_different_worldlines_can_run_parallel(self):
        async def case():
            runner = FakeSessionRunner(delay=0.1)
            manager = SandboxManager(runner)

            await asyncio.gather(
                manager.execute("w1", "a", 30),
                manager.execute("w2", "b", 30),
            )

            self.assertEqual(len(runner.start_calls), 2)
            self.assertEqual(len(runner.execute_calls), 2)
            _, _, _, _, s1, e1 = runner.execute_calls[0]
            _, _, _, _, s2, e2 = runner.execute_calls[1]
            overlap = min(e1, e2) - max(s1, s2)
            self.assertGreater(overlap, 0)

        self._run(case())

    def test_reap_idle(self):
        async def case():
            runner = FakeSessionRunner()
            manager = SandboxManager(runner)
            await manager.get_or_create("w1")
            await manager.get_or_create("w2")

            handle_w1 = manager._handles["w1"]  # test-only access
            handle_w1.last_used_monotonic -= 3600

            evicted = await manager.reap_idle(ttl_seconds=60)
            self.assertIn("w1", evicted)
            self.assertNotIn("w2", evicted)
            self.assertEqual(runner.stop_calls, [handle_w1.sandbox_id])

        self._run(case())

    def test_shutdown_all_stops_everything(self):
        async def case():
            runner = FakeSessionRunner()
            manager = SandboxManager(runner)
            h1 = await manager.get_or_create("w1")
            h2 = await manager.get_or_create("w2")

            stopped_worldlines = await manager.shutdown_all()
            self.assertEqual(set(stopped_worldlines), {"w1", "w2"})
            self.assertEqual(set(manager.active_worldlines()), set())
            self.assertEqual(set(runner.stop_calls), {h1.sandbox_id, h2.sandbox_id})

        self._run(case())


if __name__ == "__main__":
    unittest.main()
