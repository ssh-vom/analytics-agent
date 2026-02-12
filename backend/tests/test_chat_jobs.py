import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path

import meta
import threads
import worldlines
from chat.jobs import JOB_STATUS_RUNNING, ChatJobScheduler, WorldlineTurnCoordinator
from chat.jobs import enqueue_chat_turn_job


class _FakeEngine:
    def __init__(
        self,
        *,
        output_text: str,
        calls: list[str],
        delay_s: float = 0.0,
        timeline: list[tuple[str, str, float]] | None = None,
    ) -> None:
        self._output_text = output_text
        self._calls = calls
        self._delay_s = delay_s
        self._timeline = timeline

    async def run_turn(self, *, worldline_id: str, message: str):
        self._calls.append(message)
        if self._timeline is not None:
            self._timeline.append((worldline_id, "start", time.monotonic()))
        if self._delay_s > 0:
            await asyncio.sleep(self._delay_s)
        if self._timeline is not None:
            self._timeline.append((worldline_id, "end", time.monotonic()))
        return worldline_id, [
            {
                "type": "assistant_message",
                "payload": {"text": self._output_text},
            }
        ]


class ChatJobSchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        temp_root = Path(self.temp_dir.name)

        meta.DB_DIR = temp_root / "data"
        meta.DB_PATH = meta.DB_DIR / "meta.db"
        meta.init_meta_db()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _run(self, coro):
        return asyncio.run(coro)

    def _create_thread(self, title: str = "chat-job-test-thread") -> str:
        response = self._run(
            threads.create_thread(threads.CreateThreadRequest(title=title))
        )
        return response["thread_id"]

    def _create_worldline(self, thread_id: str, name: str = "main") -> str:
        response = self._run(
            worldlines.create_worldline(
                worldlines.CreateWorldlineRequest(thread_id=thread_id, name=name)
            )
        )
        return response["worldline_id"]

    async def _wait_for_status(
        self,
        job_id: str,
        *,
        expected: set[str],
        timeout_s: float = 2.0,
    ):
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            with meta.get_conn() as conn:
                row = conn.execute(
                    """
                    SELECT status, result_summary_json, started_at, finished_at
                    FROM chat_turn_jobs
                    WHERE id = ?
                    """,
                    (job_id,),
                ).fetchone()

            if row is not None and row["status"] in expected:
                return row
            await asyncio.sleep(0.03)

        raise AssertionError(f"job {job_id} did not reach expected statuses {expected}")

    def test_scheduler_requeues_running_jobs_on_startup(self) -> None:
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)

        job_id = enqueue_chat_turn_job(
            thread_id=thread_id,
            worldline_id=worldline_id,
            message="resume this",
            provider="openai",
            model="model-a",
            max_iterations=9,
        )

        with meta.get_conn() as conn:
            conn.execute(
                """
                UPDATE chat_turn_jobs
                SET status = ?, started_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (JOB_STATUS_RUNNING, job_id),
            )
            conn.commit()

        run_calls: list[str] = []

        def engine_factory(
            provider: str | None, model: str | None, max_iterations: int
        ):
            self.assertEqual(provider, "openai")
            self.assertEqual(model, "model-a")
            self.assertEqual(max_iterations, 9)
            return _FakeEngine(output_text="Recovered after restart.", calls=run_calls)

        coordinator = WorldlineTurnCoordinator()
        scheduler = ChatJobScheduler(
            turn_coordinator=coordinator,
            engine_factory=engine_factory,
        )

        async def scenario():
            try:
                await scheduler.start()
                return await self._wait_for_status(
                    job_id,
                    expected={"completed", "failed"},
                    timeout_s=3.0,
                )
            finally:
                await scheduler.shutdown()
                await coordinator.shutdown()

        done_row = self._run(scenario())

        self.assertEqual(done_row["status"], "completed")
        self.assertEqual(run_calls, ["resume this"])
        self.assertIsNotNone(done_row["started_at"])
        self.assertIsNotNone(done_row["finished_at"])

        summary = json.loads(done_row["result_summary_json"])
        self.assertEqual(summary.get("event_count"), 1)
        self.assertIn("Recovered after restart.", summary.get("assistant_preview", ""))

    def test_scheduler_start_is_idempotent_for_queued_jobs(self) -> None:
        thread_id = self._create_thread(title="chat-job-idempotent-thread")
        worldline_id = self._create_worldline(thread_id)

        job_id = enqueue_chat_turn_job(
            thread_id=thread_id,
            worldline_id=worldline_id,
            message="execute once",
            provider="openrouter",
            model=None,
            max_iterations=6,
        )

        run_calls: list[str] = []

        def engine_factory(
            provider: str | None, model: str | None, max_iterations: int
        ):
            self.assertEqual(provider, "openrouter")
            self.assertIsNone(model)
            self.assertEqual(max_iterations, 6)
            return _FakeEngine(output_text="Single execution.", calls=run_calls)

        coordinator = WorldlineTurnCoordinator()
        scheduler = ChatJobScheduler(
            turn_coordinator=coordinator,
            engine_factory=engine_factory,
        )

        async def scenario():
            try:
                await scheduler.start()
                await scheduler.start()
                return await self._wait_for_status(
                    job_id,
                    expected={"completed", "failed"},
                    timeout_s=3.0,
                )
            finally:
                await scheduler.shutdown()
                await coordinator.shutdown()

        done_row = self._run(scenario())

        self.assertEqual(done_row["status"], "completed")
        self.assertEqual(run_calls, ["execute once"])

    def test_scheduler_runs_different_worldlines_in_parallel(self) -> None:
        thread_id = self._create_thread(title="chat-job-parallel-thread")
        worldline_a = self._create_worldline(thread_id, "a")
        worldline_b = self._create_worldline(thread_id, "b")

        job_a = enqueue_chat_turn_job(
            thread_id=thread_id,
            worldline_id=worldline_a,
            message="job-a",
            provider="openai",
            model=None,
            max_iterations=6,
        )
        job_b = enqueue_chat_turn_job(
            thread_id=thread_id,
            worldline_id=worldline_b,
            message="job-b",
            provider="openai",
            model=None,
            max_iterations=6,
        )

        run_calls: list[str] = []
        timeline: list[tuple[str, str, float]] = []

        def engine_factory(
            provider: str | None, model: str | None, max_iterations: int
        ):
            self.assertEqual(provider, "openai")
            self.assertIsNone(model)
            self.assertEqual(max_iterations, 6)
            return _FakeEngine(
                output_text="parallel",
                calls=run_calls,
                delay_s=0.2,
                timeline=timeline,
            )

        coordinator = WorldlineTurnCoordinator()
        scheduler = ChatJobScheduler(
            turn_coordinator=coordinator,
            engine_factory=engine_factory,
        )

        async def scenario():
            try:
                await scheduler.start()
                await asyncio.gather(
                    self._wait_for_status(
                        job_a,
                        expected={"completed", "failed"},
                        timeout_s=3.0,
                    ),
                    self._wait_for_status(
                        job_b,
                        expected={"completed", "failed"},
                        timeout_s=3.0,
                    ),
                )
            finally:
                await scheduler.shutdown()
                await coordinator.shutdown()

        self._run(scenario())

        starts = [entry for entry in timeline if entry[1] == "start"]
        self.assertEqual(len(starts), 2)
        start_spread = abs(starts[0][2] - starts[1][2])
        self.assertLess(start_spread, 0.15)


if __name__ == "__main__":
    unittest.main()
