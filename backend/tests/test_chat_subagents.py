import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

import api.threads as threads
import api.worldlines as worldlines
import meta
from chat.jobs import WorldlineTurnCoordinator
from chat.llm_client import LlmResponse
from chat.subagents import spawn_subagents_blocking
from worldline_service import WorldlineService


class _FakeLlmClient:
    def __init__(self, text: str) -> None:
        self._text = text

    async def generate(self, **kwargs):
        return LlmResponse(text=self._text, tool_calls=[])

    async def generate_stream(self, **kwargs):
        raise RuntimeError("not used")


class ChatSubagentTests(unittest.TestCase):
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

    def _create_thread(self) -> str:
        response = self._run(
            threads.create_thread(threads.CreateThreadRequest(title="subagent-test"))
        )
        return response["thread_id"]

    def _create_worldline(self, thread_id: str) -> str:
        response = self._run(
            worldlines.create_worldline(
                worldlines.CreateWorldlineRequest(thread_id=thread_id, name="main")
            )
        )
        return response["worldline_id"]

    def _append_anchor_event(self, worldline_id: str) -> str:
        with meta.get_conn() as conn:
            event_id = meta.append_event_and_advance_head(
                conn,
                worldline_id=worldline_id,
                expected_head_event_id=None,
                event_type="assistant_message",
                payload={"text": "anchor"},
            )
            conn.commit()
        return event_id

    def test_spawn_subagents_blocking_fanout_and_lineage(self) -> None:
        thread_id = self._create_thread()
        source_worldline_id = self._create_worldline(thread_id)
        anchor_event_id = self._append_anchor_event(source_worldline_id)

        coordinator = WorldlineTurnCoordinator()

        async def _run_child_turn(
            child_worldline_id: str,
            child_message: str,
            child_max_iterations: int,
            allow_tools: bool = True,
        ):
            _ = child_max_iterations
            _ = allow_tools
            await asyncio.sleep(0.03)
            return child_worldline_id, [
                {
                    "type": "assistant_message",
                    "payload": {"text": f"child complete: {child_message[:20]}"},
                }
            ]

        result = self._run(
            spawn_subagents_blocking(
                source_worldline_id=source_worldline_id,
                from_event_id=anchor_event_id,
                tasks=[
                    {"message": "investigate state A", "label": "A"},
                    {"message": "investigate state B", "label": "B"},
                ],
                goal=None,
                tool_call_id="call_spawn_1",
                worldline_service=WorldlineService(),
                llm_client=_FakeLlmClient(text=""),
                turn_coordinator=coordinator,
                run_child_turn=_run_child_turn,
                timeout_s=1,
                max_iterations=5,
            )
        )

        self.assertEqual(result["task_count"], 2)
        self.assertEqual(result["requested_task_count"], 2)
        self.assertEqual(result["accepted_task_count"], 2)
        self.assertEqual(result["truncated_task_count"], 0)
        self.assertEqual(result["max_subagents"], 8)
        self.assertEqual(result["max_parallel_subagents"], 3)
        self.assertEqual(result["completed_count"], 2)
        self.assertEqual(result["failed_count"], 0)
        self.assertEqual(result["timed_out_count"], 0)
        self.assertEqual(result["loop_limit_failure_count"], 0)
        self.assertEqual(result["retried_task_count"], 0)
        self.assertEqual(result["recovered_task_count"], 0)
        self.assertEqual(result["failure_summary"], {})
        self.assertTrue(result["all_completed"])
        self.assertFalse(result["partial_failure"])
        self.assertEqual(len(result["tasks"]), 2)
        for task in result["tasks"]:
            self.assertEqual(task["status"], "completed")
            self.assertIsNone(task["failure_code"])
            self.assertEqual(task["retry_count"], 0)
            self.assertFalse(task["recovered"])
            self.assertIn("terminal_reason", task)
            self.assertTrue(str(task["assistant_preview"]).startswith("child complete:"))
            self.assertTrue(str(task["assistant_text"]).startswith("child complete:"))

        with meta.get_conn() as conn:
            worldline_rows = conn.execute(
                """
                SELECT id, parent_worldline_id, forked_from_event_id
                FROM worldlines
                WHERE parent_worldline_id = ?
                """,
                (source_worldline_id,),
            ).fetchall()
        self.assertEqual(len(worldline_rows), 2)
        for row in worldline_rows:
            self.assertEqual(row["parent_worldline_id"], source_worldline_id)
            self.assertEqual(row["forked_from_event_id"], anchor_event_id)

    def test_spawn_subagents_blocking_handles_failures_and_timeouts(self) -> None:
        thread_id = self._create_thread()
        source_worldline_id = self._create_worldline(thread_id)
        anchor_event_id = self._append_anchor_event(source_worldline_id)

        coordinator = WorldlineTurnCoordinator()

        async def _run_child_turn(
            child_worldline_id: str,
            child_message: str,
            child_max_iterations: int,
            allow_tools: bool = True,
        ):
            _ = child_worldline_id
            _ = child_max_iterations
            _ = allow_tools
            if "broken" in child_message:
                raise RuntimeError("simulated failure")
            if "slow" in child_message:
                await asyncio.sleep(2.0)
                return "worldline_unused", []
            await asyncio.sleep(0.03)
            return "worldline_ok", [
                {
                    "type": "assistant_message",
                    "payload": {"text": f"ok: {child_message}"},
                }
            ]

        result = self._run(
            spawn_subagents_blocking(
                source_worldline_id=source_worldline_id,
                from_event_id=anchor_event_id,
                tasks=[
                    {"message": "normal", "label": "ok-me"},
                    {"message": "broken", "label": "fail-me"},
                    {"message": "slow", "label": "hang-me"},
                ],
                goal=None,
                tool_call_id="call_spawn_2",
                worldline_service=WorldlineService(),
                llm_client=_FakeLlmClient(text=""),
                turn_coordinator=coordinator,
                run_child_turn=_run_child_turn,
                timeout_s=1,
                max_iterations=4,
            )
        )

        self.assertEqual(result["task_count"], 3)
        self.assertEqual(result["completed_count"], 1)
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(result["timed_out_count"], 1)
        self.assertFalse(result["all_completed"])
        self.assertTrue(result["partial_failure"])

        by_label = {str(task["task_label"]): task for task in result["tasks"]}
        self.assertEqual(by_label["ok-me"]["status"], "completed")
        self.assertEqual(by_label["fail-me"]["status"], "failed")
        self.assertEqual(by_label["hang-me"]["status"], "timeout")
        self.assertIn("timed out", str(by_label["hang-me"]["error"]))

    def test_spawn_subagents_blocking_derives_tasks_from_goal(self) -> None:
        thread_id = self._create_thread()
        source_worldline_id = self._create_worldline(thread_id)
        anchor_event_id = self._append_anchor_event(source_worldline_id)
        coordinator = WorldlineTurnCoordinator()

        async def _run_child_turn(
            child_worldline_id: str,
            child_message: str,
            child_max_iterations: int,
            allow_tools: bool = True,
        ):
            _ = child_worldline_id
            _ = child_max_iterations
            _ = allow_tools
            return "worldline_ok", [
                {
                    "type": "assistant_message",
                    "payload": {"text": f"ok: {child_message}"},
                }
            ]

        llm_text = (
            '{"tasks":[{"label":"one","message":"investigate segment one"},'
            '{"label":"two","message":"investigate segment two"}]}'
        )
        result = self._run(
            spawn_subagents_blocking(
                source_worldline_id=source_worldline_id,
                from_event_id=anchor_event_id,
                tasks=None,
                goal="analyze all regions",
                tool_call_id="call_spawn_3",
                worldline_service=WorldlineService(),
                llm_client=_FakeLlmClient(text=llm_text),
                turn_coordinator=coordinator,
                run_child_turn=_run_child_turn,
                timeout_s=1,
                max_iterations=4,
            )
        )
        self.assertEqual(result["task_count"], 2)
        self.assertEqual(result["completed_count"], 2)
        self.assertFalse(result["partial_failure"])

    def test_spawn_subagents_blocking_cancellation_maps_to_timeout(self) -> None:
        thread_id = self._create_thread()
        source_worldline_id = self._create_worldline(thread_id)
        anchor_event_id = self._append_anchor_event(source_worldline_id)
        coordinator = WorldlineTurnCoordinator()

        async def _run_child_turn(
            child_worldline_id: str,
            child_message: str,
            child_max_iterations: int,
            allow_tools: bool = True,
        ):
            _ = child_worldline_id
            _ = child_max_iterations
            _ = allow_tools
            if "cancel" in child_message:
                raise asyncio.CancelledError()
            return "worldline_ok", [
                {
                    "type": "assistant_message",
                    "payload": {"text": f"ok: {child_message}"},
                }
            ]

        result = self._run(
            spawn_subagents_blocking(
                source_worldline_id=source_worldline_id,
                from_event_id=anchor_event_id,
                tasks=[
                    {"message": "cancel me", "label": "cancelled"},
                    {"message": "normal", "label": "ok"},
                ],
                goal=None,
                tool_call_id="call_spawn_cancel",
                worldline_service=WorldlineService(),
                llm_client=_FakeLlmClient(text=""),
                turn_coordinator=coordinator,
                run_child_turn=_run_child_turn,
                timeout_s=1,
                max_iterations=4,
            )
        )

        self.assertEqual(result["task_count"], 2)
        self.assertEqual(result["completed_count"], 1)
        self.assertEqual(result["failed_count"], 0)
        self.assertEqual(result["timed_out_count"], 1)
        self.assertTrue(result["partial_failure"])
        by_label = {str(task["task_label"]): task for task in result["tasks"]}
        self.assertEqual(by_label["cancelled"]["status"], "timeout")
        self.assertEqual(by_label["ok"]["status"], "completed")

    def test_spawn_subagents_blocking_reports_task_truncation_and_limits(self) -> None:
        thread_id = self._create_thread()
        source_worldline_id = self._create_worldline(thread_id)
        anchor_event_id = self._append_anchor_event(source_worldline_id)
        coordinator = WorldlineTurnCoordinator()

        async def _run_child_turn(
            child_worldline_id: str,
            child_message: str,
            child_max_iterations: int,
            allow_tools: bool = True,
        ):
            _ = child_worldline_id
            _ = child_max_iterations
            _ = allow_tools
            return "worldline_ok", [
                {
                    "type": "assistant_message",
                    "payload": {"text": f"ok: {child_message}"},
                }
            ]

        tasks = [{"message": f"task-{idx}", "label": f"t-{idx}"} for idx in range(12)]
        result = self._run(
            spawn_subagents_blocking(
                source_worldline_id=source_worldline_id,
                from_event_id=anchor_event_id,
                tasks=tasks,
                goal=None,
                tool_call_id="call_spawn_limits",
                worldline_service=WorldlineService(),
                llm_client=_FakeLlmClient(text=""),
                turn_coordinator=coordinator,
                run_child_turn=_run_child_turn,
                timeout_s=10,
                max_iterations=4,
                max_subagents=10,
                max_parallel_subagents=4,
            )
        )

        self.assertEqual(result["requested_task_count"], 12)
        self.assertEqual(result["accepted_task_count"], 10)
        self.assertEqual(result["truncated_task_count"], 2)
        self.assertEqual(result["task_count"], 10)
        self.assertEqual(result["max_subagents"], 10)
        self.assertEqual(result["max_parallel_subagents"], 4)
        self.assertEqual(len(result["tasks"]), 10)

    def test_spawn_subagents_blocking_retries_loop_limit_and_recovers(self) -> None:
        thread_id = self._create_thread()
        source_worldline_id = self._create_worldline(thread_id)
        anchor_event_id = self._append_anchor_event(source_worldline_id)
        coordinator = WorldlineTurnCoordinator()
        attempts: list[tuple[str, bool]] = []
        progress_updates: list[dict[str, object]] = []

        async def _run_child_turn(
            child_worldline_id: str,
            child_message: str,
            child_max_iterations: int,
            allow_tools: bool = True,
        ):
            _ = child_message
            _ = child_max_iterations
            attempts.append((child_worldline_id, allow_tools))
            if allow_tools:
                return child_worldline_id, [
                    {
                        "type": "assistant_message",
                        "payload": {
                            "text": "I reached the tool-loop limit before producing a final answer.",
                            "state_trace": [
                                {
                                    "from_state": "planning",
                                    "to_state": "presenting",
                                    "reason": "max_iterations_reached",
                                }
                            ],
                        },
                    }
                ]
            return child_worldline_id, [
                {
                    "type": "assistant_message",
                    "payload": {
                        "text": "Recovered with synthesis-only pass.",
                        "state_trace": [
                            {
                                "from_state": "planning",
                                "to_state": "presenting",
                                "reason": "assistant_text_ready",
                            }
                        ],
                    },
                }
            ]

        async def _on_progress(payload: dict[str, object]) -> None:
            progress_updates.append(payload)

        result = self._run(
            spawn_subagents_blocking(
                source_worldline_id=source_worldline_id,
                from_event_id=anchor_event_id,
                tasks=[{"message": "loop then recover", "label": "recover"}],
                goal=None,
                tool_call_id="call_spawn_loop_recover",
                worldline_service=WorldlineService(),
                llm_client=_FakeLlmClient(text=""),
                turn_coordinator=coordinator,
                run_child_turn=_run_child_turn,
                on_progress=_on_progress,
                timeout_s=3,
                max_iterations=4,
            )
        )

        self.assertEqual(attempts[0][0], attempts[1][0])
        self.assertEqual([allow_tools for _, allow_tools in attempts], [True, False])
        self.assertEqual(result["completed_count"], 1)
        self.assertEqual(result["failed_count"], 0)
        self.assertEqual(result["timed_out_count"], 0)
        self.assertEqual(result["loop_limit_failure_count"], 0)
        self.assertEqual(result["retried_task_count"], 1)
        self.assertEqual(result["recovered_task_count"], 1)
        self.assertEqual(result["failure_summary"], {})

        task = result["tasks"][0]
        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["retry_count"], 1)
        self.assertTrue(task["recovered"])
        self.assertIsNone(task["failure_code"])
        self.assertEqual(task["terminal_reason"], "assistant_text_ready")

        retrying_updates = [
            update for update in progress_updates if update.get("phase") == "retrying"
        ]
        self.assertEqual(len(retrying_updates), 1)
        self.assertEqual(retrying_updates[0]["retry_count"], 1)

    def test_spawn_subagents_blocking_marks_unrecovered_loop_limit_as_failed(self) -> None:
        thread_id = self._create_thread()
        source_worldline_id = self._create_worldline(thread_id)
        anchor_event_id = self._append_anchor_event(source_worldline_id)
        coordinator = WorldlineTurnCoordinator()
        attempts: list[tuple[str, bool]] = []
        progress_updates: list[dict[str, object]] = []

        async def _run_child_turn(
            child_worldline_id: str,
            child_message: str,
            child_max_iterations: int,
            allow_tools: bool = True,
        ):
            _ = child_message
            _ = child_max_iterations
            attempts.append((child_worldline_id, allow_tools))
            if allow_tools:
                return child_worldline_id, [
                    {
                        "type": "assistant_message",
                        "payload": {
                            "text": "No final answer generated.",
                            "state_trace": [
                                {
                                    "from_state": "planning",
                                    "to_state": "presenting",
                                    "reason": "max_iterations_reached",
                                }
                            ],
                        },
                    }
                ]
            return child_worldline_id, [
                {
                    "type": "assistant_message",
                    "payload": {
                        "text": "I reached the tool-loop limit before producing a final answer.",
                    },
                }
            ]

        async def _on_progress(payload: dict[str, object]) -> None:
            progress_updates.append(payload)

        result = self._run(
            spawn_subagents_blocking(
                source_worldline_id=source_worldline_id,
                from_event_id=anchor_event_id,
                tasks=[{"message": "loop forever", "label": "loop"}],
                goal=None,
                tool_call_id="call_spawn_loop_fail",
                worldline_service=WorldlineService(),
                llm_client=_FakeLlmClient(text=""),
                turn_coordinator=coordinator,
                run_child_turn=_run_child_turn,
                on_progress=_on_progress,
                timeout_s=3,
                max_iterations=4,
            )
        )

        self.assertEqual(attempts[0][0], attempts[1][0])
        self.assertEqual([allow_tools for _, allow_tools in attempts], [True, False])
        self.assertEqual(result["completed_count"], 0)
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(result["timed_out_count"], 0)
        self.assertTrue(result["partial_failure"])
        self.assertEqual(result["loop_limit_failure_count"], 1)
        self.assertEqual(result["retried_task_count"], 1)
        self.assertEqual(result["recovered_task_count"], 0)
        self.assertEqual(result["failure_summary"], {"subagent_loop_limit": 1})

        task = result["tasks"][0]
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["failure_code"], "subagent_loop_limit")
        self.assertEqual(task["retry_count"], 1)
        self.assertFalse(task["recovered"])
        self.assertEqual(task["terminal_reason"], "max_iterations_reached")

        retrying_updates = [
            update for update in progress_updates if update.get("phase") == "retrying"
        ]
        self.assertEqual(len(retrying_updates), 1)
        self.assertEqual(retrying_updates[0]["retry_count"], 1)

    def test_spawn_subagents_blocking_emits_progress_updates(self) -> None:
        thread_id = self._create_thread()
        source_worldline_id = self._create_worldline(thread_id)
        anchor_event_id = self._append_anchor_event(source_worldline_id)
        coordinator = WorldlineTurnCoordinator()
        progress_updates: list[dict] = []

        async def _run_child_turn(
            child_worldline_id: str,
            child_message: str,
            child_max_iterations: int,
            allow_tools: bool = True,
        ):
            _ = child_worldline_id
            _ = child_message
            _ = child_max_iterations
            _ = allow_tools
            await asyncio.sleep(0.02)
            return "worldline_ok", [
                {
                    "type": "assistant_message",
                    "payload": {"text": "done"},
                }
            ]

        async def _on_progress(payload: dict) -> None:
            progress_updates.append(payload)

        result = self._run(
            spawn_subagents_blocking(
                source_worldline_id=source_worldline_id,
                from_event_id=anchor_event_id,
                tasks=[
                    {"message": "alpha", "label": "a"},
                    {"message": "beta", "label": "b"},
                ],
                goal=None,
                tool_call_id="call_spawn_progress",
                worldline_service=WorldlineService(),
                llm_client=_FakeLlmClient(text=""),
                turn_coordinator=coordinator,
                run_child_turn=_run_child_turn,
                on_progress=_on_progress,
                timeout_s=3,
                max_iterations=4,
                max_parallel_subagents=2,
            )
        )

        self.assertEqual(result["completed_count"], 2)
        self.assertGreaterEqual(len(progress_updates), 4)
        self.assertTrue(
            any(update.get("task_status") == "running" for update in progress_updates)
        )
        self.assertTrue(
            any(update.get("task_status") == "completed" for update in progress_updates)
        )
        final_progress = progress_updates[-1]
        self.assertEqual(final_progress.get("task_count"), 2)
        self.assertEqual(final_progress.get("completed_count"), 2)


if __name__ == "__main__":
    unittest.main()
