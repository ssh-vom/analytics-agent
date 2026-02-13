import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
import time

import api.chat as chat_api
import chat.engine as chat_engine
import meta
import api.threads as threads
import api.worldlines as worldlines
import services.chat_runtime as chat_runtime
from chat.llm_client import LlmResponse, StreamChunk, ToolCall
from fastapi.responses import StreamingResponse


class FakeLlmClient:
    """Test double that supports both ``generate()`` and ``generate_stream()``."""

    def __init__(self, responses: list[LlmResponse]) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def generate(self, **kwargs) -> LlmResponse:
        self.calls += 1
        if not self._responses:
            raise AssertionError("No fake responses left for LLM generate()")
        return self._responses.pop(0)

    async def generate_stream(self, **kwargs):
        """Yield ``StreamChunk`` objects that reconstruct the next response.

        This simulates a real streaming adapter by breaking the pre-built
        ``LlmResponse`` into the chunk protocol the engine expects:
          - text -> individual character StreamChunks (type="text")
          - tool_calls -> start / delta (full args JSON) / done sequence
        """
        self.calls += 1
        if not self._responses:
            raise AssertionError("No fake responses left for LLM generate_stream()")
        response = self._responses.pop(0)

        # Stream text tokens (one per char for fine-grained fidelity)
        if response.text:
            for ch in response.text:
                yield StreamChunk(type="text", text=ch)

        # Stream tool calls
        for tc in response.tool_calls:
            yield StreamChunk(
                type="tool_call_start",
                tool_call_id=tc.id,
                tool_name=tc.name,
            )
            args_json = json.dumps(tc.arguments, ensure_ascii=True)
            yield StreamChunk(
                type="tool_call_delta",
                tool_call_id=tc.id,
                arguments_delta=args_json,
            )
            yield StreamChunk(
                type="tool_call_done",
                tool_call_id=tc.id,
            )


class ChatApiTests(unittest.TestCase):
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

    async def _consume_stream(self, response: StreamingResponse) -> str:
        chunks: list[str] = []
        async for chunk in response.body_iterator:
            if isinstance(chunk, bytes):
                chunks.append(chunk.decode("utf-8"))
            else:
                chunks.append(str(chunk))
        return "".join(chunks)

    def _extract_sse_payloads(self, raw_stream: str) -> list[dict]:
        payloads: list[dict] = []
        for frame in raw_stream.split("\n\n"):
            frame = frame.strip()
            if not frame:
                continue
            data_lines: list[str] = []
            for line in frame.splitlines():
                if line.startswith("data: "):
                    data_lines.append(line[len("data: ") :])
            if data_lines:
                payloads.append(json.loads("\n".join(data_lines)))
        return payloads

    async def _consume_stream_first_n(self, response: StreamingResponse, n: int) -> str:
        chunks: list[str] = []
        count = 0
        async for chunk in response.body_iterator:
            if isinstance(chunk, bytes):
                chunks.append(chunk.decode("utf-8"))
            else:
                chunks.append(str(chunk))
            count += 1
            if count >= n:
                break
        return "".join(chunks)

    def _create_thread(self, title: str = "chat-test-thread") -> str:
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

    async def _wait_for_job_status(
        self,
        job_id: str,
        *,
        expected: set[str],
        timeout_s: float = 2.0,
    ) -> dict:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            job = await chat_api.get_chat_job(job_id)
            if job["status"] in expected:
                return job
            await asyncio.sleep(0.03)
        raise AssertionError(f"job {job_id} did not reach expected statuses {expected}")

    # ---- non-streaming endpoint tests (unchanged logic) ---------------------

    def test_chat_appends_user_and_assistant_events(self) -> None:
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)
        fake_client = FakeLlmClient(
            responses=[LlmResponse(text="Hello back!", tool_calls=[])]
        )

        with patch.object(chat_api, "build_llm_client", return_value=fake_client):
            result = self._run(
                chat_api.chat(
                    chat_api.ChatRequest(
                        worldline_id=worldline_id,
                        message="hello",
                        provider="openrouter",
                    )
                )
            )

        self.assertEqual(fake_client.calls, 1)
        self.assertEqual(result["worldline_id"], worldline_id)
        self.assertEqual(
            [event["type"] for event in result["events"]],
            ["user_message", "assistant_message"],
        )
        self.assertEqual(result["events"][0]["payload"], {"text": "hello"})
        self.assertEqual(result["events"][1]["payload"]["text"], "Hello back!")
        self.assertIn("state_trace", result["events"][1]["payload"])

        with meta.get_conn() as conn:
            worldline_row = conn.execute(
                "SELECT head_event_id FROM worldlines WHERE id = ?",
                (worldline_id,),
            ).fetchone()
            head_event = conn.execute(
                "SELECT type, payload_json FROM events WHERE id = ?",
                (worldline_row["head_event_id"],),
            ).fetchone()

        self.assertEqual(head_event["type"], "assistant_message")
        self.assertEqual(json.loads(head_event["payload_json"])["text"], "Hello back!")

    def test_chat_report_mode_auto_generates_pdf_artifact(self) -> None:
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)
        fake_client = FakeLlmClient(
            responses=[LlmResponse(text="Analysis complete.", tool_calls=[])]
        )
        fake_execute_python_tool = AsyncMock(
            return_value={
                "stdout": "Generated report.pdf\n",
                "stderr": "",
                "error": None,
                "artifacts": [
                    {
                        "type": "pdf",
                        "name": "report.pdf",
                        "artifact_id": "artifact_report",
                    }
                ],
                "previews": {"dataframes": []},
                "execution_ms": 18,
            }
        )

        with (
            patch.object(chat_api, "build_llm_client", return_value=fake_client),
            patch(
                "chat.engine.execute_python_tool",
                fake_execute_python_tool,
            ),
        ):
            result = self._run(
                chat_api.chat(
                    chat_api.ChatRequest(
                        worldline_id=worldline_id,
                        message=(
                            "summarize revenue\n\n"
                            "<context>\n"
                            "- output_type=report\n"
                            "</context>"
                        ),
                        provider="openai",
                    )
                )
            )

        self.assertEqual(fake_client.calls, 1)
        self.assertEqual(fake_execute_python_tool.await_count, 1)

        call_args = fake_execute_python_tool.await_args
        self.assertIsNotNone(call_args)
        if call_args is None:
            self.fail("auto report call args missing")
        call_request = call_args.args[0]
        self.assertEqual(call_request.call_id, "auto_report_pdf")
        self.assertEqual(call_request.worldline_id, worldline_id)
        self.assertIn("report.pdf", call_request.code)
        compile(call_request.code, "<auto_report_from_engine>", "exec")

        assistant_event = result["events"][-1]
        self.assertEqual(assistant_event["type"], "assistant_message")
        self.assertIn(
            "Generated downloadable report artifact: report.pdf",
            assistant_event["payload"]["text"],
        )

    def test_chat_dashboard_mode_does_not_auto_generate_pdf(self) -> None:
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)
        fake_client = FakeLlmClient(
            responses=[LlmResponse(text="Dashboard complete.", tool_calls=[])]
        )
        fake_execute_python_tool = AsyncMock(
            return_value={
                "stdout": "Generated report.pdf\n",
                "stderr": "",
                "error": None,
                "artifacts": [
                    {
                        "type": "pdf",
                        "name": "report.pdf",
                        "artifact_id": "artifact_report",
                    }
                ],
                "previews": {"dataframes": []},
                "execution_ms": 18,
            }
        )

        with (
            patch.object(chat_api, "build_llm_client", return_value=fake_client),
            patch(
                "chat.engine.execute_python_tool",
                fake_execute_python_tool,
            ),
        ):
            result = self._run(
                chat_api.chat(
                    chat_api.ChatRequest(
                        worldline_id=worldline_id,
                        message=(
                            "summarize revenue\n\n"
                            "<context>\n"
                            "- output_type=dashboard\n"
                            "</context>"
                        ),
                        provider="openai",
                    )
                )
            )

        self.assertEqual(fake_client.calls, 1)
        self.assertEqual(fake_execute_python_tool.await_count, 0)
        assistant_event = result["events"][-1]
        self.assertEqual(assistant_event["type"], "assistant_message")
        self.assertNotIn(
            "Generated downloadable report artifact", assistant_event["payload"]["text"]
        )

    def test_chat_tool_loop_calls_sql_then_returns_final_message(self) -> None:
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)
        fake_client = FakeLlmClient(
            responses=[
                LlmResponse(
                    text=None,
                    tool_calls=[
                        ToolCall(
                            id="call_1",
                            name="run_sql",
                            arguments={"sql": "SELECT 1 AS x", "limit": 10},
                        )
                    ],
                ),
                LlmResponse(text="The query returned one row.", tool_calls=[]),
            ]
        )

        with patch.object(chat_api, "build_llm_client", return_value=fake_client):
            result = self._run(
                chat_api.chat(
                    chat_api.ChatRequest(
                        worldline_id=worldline_id,
                        message="run a quick check",
                        provider="openai",
                    )
                )
            )

        self.assertEqual(fake_client.calls, 2)
        self.assertEqual(result["worldline_id"], worldline_id)
        event_types = [event["type"] for event in result["events"]]
        self.assertEqual(
            event_types,
            [
                "user_message",
                "tool_call_sql",
                "tool_result_sql",
                "assistant_message",
            ],
        )

        sql_result = next(
            event for event in result["events"] if event["type"] == "tool_result_sql"
        )
        self.assertEqual(sql_result["payload"]["rows"], [[1]])
        self.assertEqual(
            result["events"][-1]["payload"]["text"],
            "The query returned one row.",
        )

    def test_chat_skips_repeated_identical_tool_calls_in_turn_and_continues(self) -> None:
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)
        fake_client = FakeLlmClient(
            responses=[
                LlmResponse(
                    text=None,
                    tool_calls=[
                        ToolCall(
                            id="call_repeat_1",
                            name="run_sql",
                            arguments={"sql": "SELECT 1 AS x", "limit": 10},
                        )
                    ],
                ),
                LlmResponse(
                    text=None,
                    tool_calls=[
                        ToolCall(
                            id="call_repeat_2",
                            name="run_sql",
                            arguments={"sql": "SELECT 1 AS x", "limit": 10},
                        )
                    ],
                ),
                LlmResponse(text="Used prior result without rerunning.", tool_calls=[]),
            ]
        )

        with patch.object(chat_api, "build_llm_client", return_value=fake_client):
            result = self._run(
                chat_api.chat(
                    chat_api.ChatRequest(
                        worldline_id=worldline_id,
                        message="run this once",
                        provider="openrouter",
                    )
                )
            )

        self.assertEqual(fake_client.calls, 3)
        self.assertEqual(
            [event["type"] for event in result["events"]],
            ["user_message", "tool_call_sql", "tool_result_sql", "assistant_message"],
        )
        self.assertEqual(
            result["events"][-1]["payload"]["text"],
            "Used prior result without rerunning.",
        )

    def test_chat_skips_recent_identical_successful_tool_call_across_turns(
        self,
    ) -> None:
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)

        first_client = FakeLlmClient(
            responses=[
                LlmResponse(
                    text=None,
                    tool_calls=[
                        ToolCall(
                            id="call_seed_sql",
                            name="run_sql",
                            arguments={"sql": "SELECT 1 AS x", "limit": 10},
                        )
                    ],
                ),
                LlmResponse(text="seeded", tool_calls=[]),
            ]
        )

        with patch.object(chat_api, "build_llm_client", return_value=first_client):
            first_result = self._run(
                chat_api.chat(
                    chat_api.ChatRequest(
                        worldline_id=worldline_id,
                        message="run once",
                        provider="openrouter",
                    )
                )
            )

        self.assertEqual(first_client.calls, 2)
        self.assertEqual(
            [event["type"] for event in first_result["events"]],
            ["user_message", "tool_call_sql", "tool_result_sql", "assistant_message"],
        )

        second_client = FakeLlmClient(
            responses=[
                LlmResponse(
                    text=None,
                    tool_calls=[
                        ToolCall(
                            id="call_repeat_sql",
                            name="run_sql",
                            arguments={"sql": "SELECT 1 AS x", "limit": 10},
                        )
                    ],
                )
            ]
        )

        with patch.object(chat_api, "build_llm_client", return_value=second_client):
            second_result = self._run(
                chat_api.chat(
                    chat_api.ChatRequest(
                        worldline_id=worldline_id,
                        message="continue analysis",
                        provider="openrouter",
                    )
                )
            )

        self.assertEqual(second_client.calls, 1)
        self.assertEqual(
            [event["type"] for event in second_result["events"]],
            ["user_message", "assistant_message"],
        )
        self.assertIn(
            "rerun",
            second_result["events"][-1]["payload"]["text"],
        )

    def test_chat_allows_recent_identical_call_when_user_requests_retry(self) -> None:
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)

        first_client = FakeLlmClient(
            responses=[
                LlmResponse(
                    text=None,
                    tool_calls=[
                        ToolCall(
                            id="call_seed_retry_sql",
                            name="run_sql",
                            arguments={"sql": "SELECT 1 AS x", "limit": 10},
                        )
                    ],
                ),
                LlmResponse(text="seeded", tool_calls=[]),
            ]
        )
        with patch.object(chat_api, "build_llm_client", return_value=first_client):
            self._run(
                chat_api.chat(
                    chat_api.ChatRequest(
                        worldline_id=worldline_id,
                        message="run once",
                        provider="openrouter",
                    )
                )
            )

        second_client = FakeLlmClient(
            responses=[
                LlmResponse(
                    text=None,
                    tool_calls=[
                        ToolCall(
                            id="call_retry_sql",
                            name="run_sql",
                            arguments={"sql": "SELECT 1 AS x", "limit": 10},
                        )
                    ],
                ),
                LlmResponse(text="rerun done", tool_calls=[]),
            ]
        )
        with patch.object(chat_api, "build_llm_client", return_value=second_client):
            second_result = self._run(
                chat_api.chat(
                    chat_api.ChatRequest(
                        worldline_id=worldline_id,
                        message="try again",
                        provider="openrouter",
                    )
                )
            )

        self.assertEqual(second_client.calls, 2)
        self.assertEqual(
            [event["type"] for event in second_result["events"]],
            ["user_message", "tool_call_sql", "tool_result_sql", "assistant_message"],
        )

    def test_chat_report_mode_skips_fallback_after_guard_stop(self) -> None:
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)
        fake_client = FakeLlmClient(
            responses=[
                LlmResponse(
                    text=None,
                    tool_calls=[
                        ToolCall(
                            id="call_guard_1",
                            name="run_sql",
                            arguments={"sql": "SELECT 1 AS x", "limit": 1},
                        )
                    ],
                ),
                LlmResponse(
                    text=None,
                    tool_calls=[
                        ToolCall(
                            id="call_guard_2",
                            name="run_sql",
                            arguments={"sql": "SELECT 1 AS x", "limit": 1},
                        )
                    ],
                ),
            ]
        )
        fake_execute_python_tool = AsyncMock(
            return_value={
                "stdout": "Generated report.pdf\n",
                "stderr": "",
                "error": None,
                "artifacts": [
                    {
                        "type": "pdf",
                        "name": "report.pdf",
                        "artifact_id": "artifact_report",
                    }
                ],
                "previews": {"dataframes": []},
                "execution_ms": 10,
            }
        )

        with (
            patch.object(chat_api, "build_llm_client", return_value=fake_client),
            patch("chat.engine.execute_python_tool", fake_execute_python_tool),
        ):
            result = self._run(
                chat_api.chat(
                    chat_api.ChatRequest(
                        worldline_id=worldline_id,
                        message=(
                            "analyze the table\n\n"
                            "<context>\n"
                            "- output_type=report\n"
                            "</context>"
                        ),
                        provider="openai",
                    )
                )
            )

        self.assertEqual(fake_client.calls, 2)
        self.assertEqual(fake_execute_python_tool.await_count, 0)
        self.assertIn(
            "repeated the same tool call",
            result["events"][-1]["payload"]["text"],
        )
        self.assertNotIn(
            "Generated downloadable report artifact",
            result["events"][-1]["payload"]["text"],
        )

    def test_chat_prevents_duplicate_artifact_names_from_python(self) -> None:
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)

        with meta.get_conn() as conn:
            conn.execute(
                """
                INSERT INTO events (id, worldline_id, parent_event_id, type, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "event_prev_py_call",
                    worldline_id,
                    None,
                    "tool_call_python",
                    json.dumps(
                        {
                            "code": "print('seed')",
                            "timeout": 30,
                            "call_id": "call_prev_py",
                        }
                    ),
                ),
            )
            conn.execute(
                """
                INSERT INTO events (id, worldline_id, parent_event_id, type, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "event_prev_py_result",
                    worldline_id,
                    "event_prev_py_call",
                    "tool_result_python",
                    json.dumps(
                        {
                            "error": None,
                            "artifacts": [
                                {
                                    "type": "csv",
                                    "name": "top_by_amount.csv",
                                    "artifact_id": "artifact_prev_top_by_amount",
                                }
                            ],
                        }
                    ),
                ),
            )
            conn.execute(
                "UPDATE worldlines SET head_event_id = ? WHERE id = ?",
                ("event_prev_py_result", worldline_id),
            )
            conn.commit()

        fake_client = FakeLlmClient(
            responses=[
                LlmResponse(
                    text=None,
                    tool_calls=[
                        ToolCall(
                            id="call_new_py",
                            name="run_python",
                            arguments={
                                "code": "import pandas as pd\nLATEST_SQL_DF.to_csv('top_by_amount.csv', index=False)",
                                "timeout": 30,
                            },
                        )
                    ],
                )
            ]
        )
        fake_execute_python_tool = AsyncMock(
            return_value={
                "stdout": "should_not_run\n",
                "stderr": "",
                "error": None,
                "artifacts": [],
                "previews": {"dataframes": []},
                "execution_ms": 1,
            }
        )

        with (
            patch.object(chat_api, "build_llm_client", return_value=fake_client),
            patch("chat.engine.execute_python_tool", fake_execute_python_tool),
        ):
            result = self._run(
                chat_api.chat(
                    chat_api.ChatRequest(
                        worldline_id=worldline_id,
                        message="continue the duplicate analysis",
                        provider="openai",
                    )
                )
            )

        self.assertEqual(fake_client.calls, 1)
        self.assertEqual(fake_execute_python_tool.await_count, 0)
        self.assertEqual(
            [event["type"] for event in result["events"]],
            ["user_message", "assistant_message"],
        )
        self.assertIn(
            "would recreate existing artifacts",
            result["events"][-1]["payload"]["text"],
        )

    def test_chat_retries_once_after_empty_python_payload(self) -> None:
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)
        fake_client = FakeLlmClient(
            responses=[
                LlmResponse(
                    text=None,
                    tool_calls=[
                        ToolCall(
                            id="call_empty_py",
                            name="run_python",
                            arguments={},
                        )
                    ],
                ),
                LlmResponse(
                    text=None,
                    tool_calls=[
                        ToolCall(
                            id="call_valid_py",
                            name="run_python",
                            arguments={
                                "code": "print('ok')",
                                "timeout": 10,
                            },
                        )
                    ],
                ),
                LlmResponse(text="Completed after correction.", tool_calls=[]),
            ]
        )
        fake_execute_python_tool = AsyncMock(
            return_value={
                "stdout": "ok\n",
                "stderr": "",
                "error": None,
                "artifacts": [],
                "previews": {"dataframes": []},
                "execution_ms": 12,
            }
        )

        with (
            patch.object(chat_api, "build_llm_client", return_value=fake_client),
            patch("chat.engine.execute_python_tool", fake_execute_python_tool),
        ):
            result = self._run(
                chat_api.chat(
                    chat_api.ChatRequest(
                        worldline_id=worldline_id,
                        message="run python",
                        provider="openai",
                    )
                )
            )

        self.assertEqual(fake_client.calls, 3)
        self.assertEqual(fake_execute_python_tool.await_count, 1)
        self.assertEqual(
            [event["type"] for event in result["events"]],
            ["user_message", "assistant_message"],
        )
        self.assertEqual(
            result["events"][-1]["payload"]["text"],
            "Completed after correction.",
        )

    def test_chat_unwraps_json_wrapped_python_code_argument(self) -> None:
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)
        fake_client = FakeLlmClient(
            responses=[
                LlmResponse(
                    text=None,
                    tool_calls=[
                        ToolCall(
                            id="call_wrapped_py",
                            name="run_python",
                            arguments={
                                "code": '{"code":"print(7)","timeout":30}',
                                "timeout": 8,
                            },
                        )
                    ],
                ),
                LlmResponse(text="done", tool_calls=[]),
            ]
        )
        fake_execute_python_tool = AsyncMock(
            return_value={
                "stdout": "7\n",
                "stderr": "",
                "error": None,
                "artifacts": [],
                "previews": {"dataframes": []},
                "execution_ms": 10,
            }
        )

        with (
            patch.object(chat_api, "build_llm_client", return_value=fake_client),
            patch("chat.engine.execute_python_tool", fake_execute_python_tool),
        ):
            result = self._run(
                chat_api.chat(
                    chat_api.ChatRequest(
                        worldline_id=worldline_id,
                        message="run wrapped python",
                        provider="openai",
                    )
                )
            )

        self.assertEqual(fake_client.calls, 2)
        self.assertEqual(fake_execute_python_tool.await_count, 1)
        call_args = fake_execute_python_tool.await_args
        self.assertIsNotNone(call_args)
        if call_args is None:
            self.fail("python tool call args missing")
        call_request = call_args.args[0]
        self.assertEqual(call_request.code, "print(7)")
        self.assertEqual(call_request.timeout, 8)
        self.assertEqual(result["events"][-1]["payload"]["text"], "done")

    def test_chat_allows_many_sql_calls_per_turn(self) -> None:
        """No per-tool-call limit; multiple SQL runs complete and finalize."""
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)
        fake_client = FakeLlmClient(
            responses=[
                LlmResponse(
                    text=None,
                    tool_calls=[
                        ToolCall(
                            id="call_many_1",
                            name="run_sql",
                            arguments={"sql": "SELECT 1 AS x", "limit": 1},
                        )
                    ],
                ),
                LlmResponse(
                    text=None,
                    tool_calls=[
                        ToolCall(
                            id="call_many_2",
                            name="run_sql",
                            arguments={"sql": "SELECT 2 AS x", "limit": 2},
                        )
                    ],
                ),
                LlmResponse(
                    text=None,
                    tool_calls=[
                        ToolCall(
                            id="call_many_3",
                            name="run_sql",
                            arguments={"sql": "SELECT 3 AS x", "limit": 3},
                        )
                    ],
                ),
                LlmResponse(
                    text=None,
                    tool_calls=[
                        ToolCall(
                            id="call_many_4",
                            name="run_sql",
                            arguments={"sql": "SELECT 4 AS x", "limit": 4},
                        )
                    ],
                ),
                LlmResponse(
                    text="Here are the results from all four queries.",
                    tool_calls=[],
                ),
            ]
        )

        with patch.object(chat_api, "build_llm_client", return_value=fake_client):
            result = self._run(
                chat_api.chat(
                    chat_api.ChatRequest(
                        worldline_id=worldline_id,
                        message="keep running sql",
                        provider="openai",
                    )
                )
            )

        self.assertEqual(fake_client.calls, 5)
        self.assertEqual(
            [event["type"] for event in result["events"]],
            [
                "user_message",
                "tool_call_sql",
                "tool_result_sql",
                "tool_call_sql",
                "tool_result_sql",
                "tool_call_sql",
                "tool_result_sql",
                "tool_call_sql",
                "tool_result_sql",
                "assistant_message",
            ],
        )

    def test_chat_allows_multiple_python_runs_per_turn(self) -> None:
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)
        fake_client = FakeLlmClient(
            responses=[
                LlmResponse(
                    text=None,
                    tool_calls=[
                        ToolCall(
                            id="call_py_once",
                            name="run_python",
                            arguments={"code": "print('ok')", "timeout": 10},
                        )
                    ],
                ),
                LlmResponse(
                    text=None,
                    tool_calls=[
                        ToolCall(
                            id="call_py_again",
                            name="run_python",
                            arguments={"code": "print('again')", "timeout": 10},
                        )
                    ],
                ),
                LlmResponse(text="Both python steps completed.", tool_calls=[]),
            ]
        )

        run_count = 0

        async def fake_execute_python_tool(*args, **kwargs):
            nonlocal run_count
            run_count += 1
            return {
                "stdout": f"ok_{run_count}\n",
                "stderr": "",
                "error": None,
                "artifacts": [
                    {
                        "type": "image",
                        "name": f"line_step_{run_count}.png",
                        "artifact_id": f"artifact_fake_{run_count}",
                    }
                ],
                "previews": {"dataframes": []},
                "execution_ms": 10,
            }

        with (
            patch.object(chat_api, "build_llm_client", return_value=fake_client),
            patch(
                "chat.engine.execute_python_tool",
                side_effect=fake_execute_python_tool,
            ),
        ):
            result = self._run(
                chat_api.chat(
                    chat_api.ChatRequest(
                        worldline_id=worldline_id,
                        message="plot a line",
                        provider="openrouter",
                    )
                )
            )

        self.assertEqual(fake_client.calls, 3)
        self.assertEqual(run_count, 2)
        self.assertEqual(
            [event["type"] for event in result["events"]],
            ["user_message", "assistant_message"],
        )
        self.assertEqual(
            result["events"][-1]["payload"]["text"], "Both python steps completed."
        )

    def test_chat_time_travel_branches_and_continues_on_new_worldline(self) -> None:
        thread_id = self._create_thread()
        source_worldline_id = self._create_worldline(thread_id)

        with meta.get_conn() as conn:
            conn.execute(
                """
                INSERT INTO events (id, worldline_id, parent_event_id, type, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "event_seed_branch",
                    source_worldline_id,
                    None,
                    "assistant_message",
                    json.dumps({"text": "seed"}),
                ),
            )
            conn.execute(
                "UPDATE worldlines SET head_event_id = ? WHERE id = ?",
                ("event_seed_branch", source_worldline_id),
            )
            conn.commit()

        fake_client = FakeLlmClient(
            responses=[
                LlmResponse(
                    text=None,
                    tool_calls=[
                        ToolCall(
                            id="call_tt_1",
                            name="time_travel",
                            arguments={
                                "from_event_id": "event_seed_branch",
                                "name": "alt-path",
                            },
                        )
                    ],
                ),
                LlmResponse(
                    text="Now continuing in the branched worldline.",
                    tool_calls=[],
                ),
            ]
        )

        with patch.object(chat_api, "build_llm_client", return_value=fake_client):
            result = self._run(
                chat_api.chat(
                    chat_api.ChatRequest(
                        worldline_id=source_worldline_id,
                        message="please branch and continue",
                        provider="openrouter",
                    )
                )
            )

        self.assertEqual(fake_client.calls, 2)
        event_types = [event["type"] for event in result["events"]]
        self.assertEqual(
            event_types,
            [
                "worldline_created",
                "time_travel",
                "user_message",
                "assistant_message",
            ],
        )

        created = result["events"][0]
        new_worldline_id = created["payload"]["new_worldline_id"]
        self.assertEqual(result["worldline_id"], new_worldline_id)
        self.assertTrue(new_worldline_id.startswith("worldline_"))
        self.assertNotEqual(new_worldline_id, source_worldline_id)
        self.assertEqual(created["payload"]["name"], "alt-path")

        with meta.get_conn() as conn:
            new_worldline = conn.execute(
                """
                SELECT id, thread_id, parent_worldline_id, forked_from_event_id, head_event_id, name
                FROM worldlines
                WHERE id = ?
                """,
                (new_worldline_id,),
            ).fetchone()
            head_event = conn.execute(
                "SELECT type, payload_json FROM events WHERE id = ?",
                (new_worldline["head_event_id"],),
            ).fetchone()

        self.assertEqual(new_worldline["thread_id"], thread_id)
        self.assertEqual(new_worldline["parent_worldline_id"], source_worldline_id)
        self.assertEqual(new_worldline["forked_from_event_id"], "event_seed_branch")
        self.assertEqual(new_worldline["name"], "alt-path")
        self.assertEqual(head_event["type"], "assistant_message")
        self.assertEqual(
            json.loads(head_event["payload_json"])["text"],
            "Now continuing in the branched worldline.",
        )

    def test_chat_spawn_subagents_tool_blocks_and_returns_aggregate(self) -> None:
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)
        with meta.get_conn() as conn:
            conn.execute(
                """
                INSERT INTO events (id, worldline_id, parent_event_id, type, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "event_seed_spawn",
                    worldline_id,
                    None,
                    "assistant_message",
                    json.dumps({"text": "seed"}),
                ),
            )
            conn.execute(
                "UPDATE worldlines SET head_event_id = ? WHERE id = ?",
                ("event_seed_spawn", worldline_id),
            )
            conn.commit()

        fake_client = FakeLlmClient(
            responses=[
                LlmResponse(
                    text=None,
                    tool_calls=[
                        ToolCall(
                            id="call_spawn_tool",
                            name="spawn_subagents",
                            arguments={
                                "from_event_id": "event_seed_spawn",
                                "tasks": [
                                    {"message": "task one", "label": "one"},
                                    {"message": "task two", "label": "two"},
                                ],
                                "timeout_s": 1,
                                "max_iterations": 4,
                                "max_subagents": 2,
                                "max_parallel_subagents": 1,
                            },
                        )
                    ],
                ),
                LlmResponse(text="Aggregated child runs.", tool_calls=[]),
            ]
        )
        fake_spawn_result = {
            "fanout_group_id": "fanout_1",
            "task_count": 2,
            "requested_task_count": 2,
            "accepted_task_count": 2,
            "truncated_task_count": 0,
            "max_subagents": 2,
            "max_parallel_subagents": 1,
            "completed_count": 2,
            "failed_count": 0,
            "timed_out_count": 0,
            "loop_limit_failure_count": 0,
            "retried_task_count": 0,
            "recovered_task_count": 0,
            "failure_summary": {},
            "all_completed": True,
            "partial_failure": False,
            "tasks": [
                {
                    "task_label": "one",
                    "status": "completed",
                    "failure_code": None,
                    "retry_count": 0,
                    "recovered": False,
                    "terminal_reason": "assistant_text_ready",
                },
                {
                    "task_label": "two",
                    "status": "completed",
                    "failure_code": None,
                    "retry_count": 0,
                    "recovered": False,
                    "terminal_reason": "assistant_text_ready",
                },
            ],
        }

        class _DummyScheduler:
            async def start(self) -> None:
                return None

        with (
            patch.object(chat_api, "build_llm_client", return_value=fake_client),
            patch(
                "chat.engine.spawn_subagents_blocking",
                AsyncMock(return_value=fake_spawn_result),
            ) as mocked_spawn,
            patch(
                "services.chat_runtime.get_chat_job_scheduler",
                return_value=_DummyScheduler(),
            ),
        ):
            result = self._run(
                chat_api.chat(
                    chat_api.ChatRequest(
                        worldline_id=worldline_id,
                        message="fan out",
                        provider="openai",
                    )
                )
            )

        self.assertEqual(fake_client.calls, 2)
        self.assertEqual(mocked_spawn.await_count, 1)
        self.assertEqual(
            [event["type"] for event in result["events"]],
            [
                "user_message",
                "tool_call_subagents",
                "tool_result_subagents",
                "assistant_message",
            ],
        )
        call_event = result["events"][1]
        self.assertEqual(call_event["payload"]["max_subagents"], 2)
        self.assertEqual(call_event["payload"]["max_parallel_subagents"], 1)
        result_event = result["events"][2]
        self.assertEqual(result_event["payload"]["requested_task_count"], 2)
        self.assertEqual(result_event["payload"]["accepted_task_count"], 2)
        self.assertEqual(result_event["payload"]["truncated_task_count"], 0)
        self.assertEqual(result_event["payload"]["loop_limit_failure_count"], 0)
        self.assertEqual(result_event["payload"]["retried_task_count"], 0)
        self.assertEqual(result_event["payload"]["recovered_task_count"], 0)
        self.assertEqual(result_event["payload"]["failure_summary"], {})
        self.assertFalse(result_event["payload"]["partial_failure"])
        spawn_kwargs = mocked_spawn.await_args.kwargs
        self.assertEqual(spawn_kwargs["max_subagents"], 2)
        self.assertEqual(spawn_kwargs["max_parallel_subagents"], 1)
        self.assertEqual(result["events"][-1]["payload"]["text"], "Aggregated child runs.")

    def test_chat_spawn_subagents_persists_error_result_event(self) -> None:
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)
        with meta.get_conn() as conn:
            conn.execute(
                """
                INSERT INTO events (id, worldline_id, parent_event_id, type, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "event_seed_spawn_error",
                    worldline_id,
                    None,
                    "assistant_message",
                    json.dumps({"text": "seed"}),
                ),
            )
            conn.execute(
                "UPDATE worldlines SET head_event_id = ? WHERE id = ?",
                ("event_seed_spawn_error", worldline_id),
            )
            conn.commit()

        fake_client = FakeLlmClient(
            responses=[
                LlmResponse(
                    text=None,
                    tool_calls=[
                        ToolCall(
                            id="call_spawn_error",
                            name="spawn_subagents",
                            arguments={
                                "from_event_id": "event_seed_spawn_error",
                                "goal": "parallelize this",
                                "timeout_s": 1,
                                "max_iterations": 4,
                            },
                        )
                    ],
                ),
                LlmResponse(text="Recovered after subagent failure.", tool_calls=[]),
            ]
        )

        class _DummyCoordinator:
            async def run(self, worldline_id, factory):
                _ = worldline_id
                return await factory()

        with (
            patch.object(chat_api, "build_llm_client", return_value=fake_client),
            patch(
                "chat.engine.spawn_subagents_blocking",
                AsyncMock(side_effect=RuntimeError("simulated fanout failure")),
            ),
            patch(
                "services.chat_runtime.get_turn_coordinator",
                return_value=_DummyCoordinator(),
            ),
        ):
            result = self._run(
                chat_api.chat(
                    chat_api.ChatRequest(
                        worldline_id=worldline_id,
                        message="fan out and recover",
                        provider="openai",
                    )
                )
            )

        self.assertEqual(
            [event["type"] for event in result["events"]],
            [
                "user_message",
                "tool_call_subagents",
                "tool_result_subagents",
                "assistant_message",
            ],
        )
        tool_result = next(
            event for event in result["events"] if event["type"] == "tool_result_subagents"
        )
        self.assertIn("error", tool_result["payload"])

    def test_chat_spawn_subagents_partial_failure_adds_parent_synthesis_nudge(self) -> None:
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)
        with meta.get_conn() as conn:
            conn.execute(
                """
                INSERT INTO events (id, worldline_id, parent_event_id, type, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "event_seed_spawn_partial",
                    worldline_id,
                    None,
                    "assistant_message",
                    json.dumps({"text": "seed"}),
                ),
            )
            conn.execute(
                "UPDATE worldlines SET head_event_id = ? WHERE id = ?",
                ("event_seed_spawn_partial", worldline_id),
            )
            conn.commit()

        class CaptureClient(FakeLlmClient):
            def __init__(self, responses: list[LlmResponse]) -> None:
                super().__init__(responses)
                self.generate_call_history: list[dict[str, object]] = []

            async def generate(self, **kwargs) -> LlmResponse:
                self.generate_call_history.append(kwargs)
                return await super().generate(**kwargs)

        fake_client = CaptureClient(
            responses=[
                LlmResponse(
                    text=None,
                    tool_calls=[
                        ToolCall(
                            id="call_spawn_partial",
                            name="spawn_subagents",
                            arguments={
                                "from_event_id": "event_seed_spawn_partial",
                                "tasks": [
                                    {"message": "task one", "label": "one"},
                                    {"message": "task two", "label": "two"},
                                ],
                                "timeout_s": 1,
                                "max_iterations": 4,
                            },
                        )
                    ],
                ),
                LlmResponse(text="Parent synthesis with explicit gaps.", tool_calls=[]),
            ]
        )
        fake_spawn_result = {
            "fanout_group_id": "fanout_partial",
            "task_count": 2,
            "requested_task_count": 2,
            "accepted_task_count": 2,
            "truncated_task_count": 0,
            "completed_count": 1,
            "failed_count": 1,
            "timed_out_count": 0,
            "loop_limit_failure_count": 0,
            "retried_task_count": 0,
            "recovered_task_count": 0,
            "failure_summary": {"subagent_error": 1},
            "all_completed": False,
            "partial_failure": True,
            "tasks": [
                {
                    "task_label": "one",
                    "status": "completed",
                    "failure_code": None,
                    "retry_count": 0,
                    "recovered": False,
                    "terminal_reason": "assistant_text_ready",
                    "assistant_preview": "segment one findings",
                },
                {
                    "task_label": "two",
                    "status": "failed",
                    "failure_code": "subagent_error",
                    "retry_count": 0,
                    "recovered": False,
                    "terminal_reason": "error",
                    "error": "timeout in downstream dependency",
                },
            ],
        }

        class _DummyScheduler:
            async def start(self) -> None:
                return None

        with (
            patch.object(chat_api, "build_llm_client", return_value=fake_client),
            patch(
                "chat.engine.spawn_subagents_blocking",
                AsyncMock(return_value=fake_spawn_result),
            ),
            patch(
                "services.chat_runtime.get_chat_job_scheduler",
                return_value=_DummyScheduler(),
            ),
        ):
            result = self._run(
                chat_api.chat(
                    chat_api.ChatRequest(
                        worldline_id=worldline_id,
                        message="fan out and synthesize despite failures",
                        provider="openai",
                    )
                )
            )

        self.assertEqual(fake_client.calls, 2)
        self.assertGreaterEqual(len(fake_client.generate_call_history), 2)
        second_call = fake_client.generate_call_history[1]
        second_messages = second_call["messages"]
        system_messages = [
            str(message.content)
            for message in second_messages
            if message.role == "system" and isinstance(message.content, str)
        ]
        system_text = "\n".join(system_messages)
        self.assertIn("Subagent fan-out completed with partial failures.", system_text)
        self.assertIn(
            "Synthesize conclusions from successful task outputs only.",
            system_text,
        )
        self.assertIn(
            "Explicitly list missing or failed slices and what remains unknown.",
            system_text,
        )
        self.assertIn("Successful slices:", system_text)
        self.assertIn("Missing/failed slices:", system_text)
        self.assertEqual(
            result["events"][-1]["payload"]["text"],
            "Parent synthesis with explicit gaps.",
        )

    def test_chat_spawn_subagents_invalid_from_event_falls_back_to_head(self) -> None:
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)
        with meta.get_conn() as conn:
            conn.execute(
                """
                INSERT INTO events (id, worldline_id, parent_event_id, type, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "event_seed_spawn_fallback",
                    worldline_id,
                    None,
                    "assistant_message",
                    json.dumps({"text": "seed"}),
                ),
            )
            conn.execute(
                "UPDATE worldlines SET head_event_id = ? WHERE id = ?",
                ("event_seed_spawn_fallback", worldline_id),
            )
            conn.commit()

        fake_client = FakeLlmClient(
            responses=[
                LlmResponse(
                    text=None,
                    tool_calls=[
                        ToolCall(
                            id="call_spawn_fallback",
                            name="spawn_subagents",
                            arguments={
                                "from_event_id": "event_missing_abc",
                                "goal": "analyze everything",
                                "timeout_s": 1,
                                "max_iterations": 4,
                            },
                        )
                    ],
                ),
                LlmResponse(text="Fallback worked.", tool_calls=[]),
            ]
        )
        fake_spawn_result = {
            "fanout_group_id": "fanout_fallback",
            "task_count": 1,
            "completed_count": 1,
            "failed_count": 0,
            "timed_out_count": 0,
            "loop_limit_failure_count": 0,
            "retried_task_count": 0,
            "recovered_task_count": 0,
            "failure_summary": {},
            "all_completed": True,
            "tasks": [
                {
                    "task_label": "one",
                    "status": "completed",
                    "failure_code": None,
                    "retry_count": 0,
                    "recovered": False,
                    "terminal_reason": "assistant_text_ready",
                }
            ],
        }

        class _DummyCoordinator:
            async def run(self, worldline_id, factory):
                _ = worldline_id
                return await factory()

        with (
            patch.object(chat_api, "build_llm_client", return_value=fake_client),
            patch(
                "chat.engine.spawn_subagents_blocking",
                AsyncMock(return_value=fake_spawn_result),
            ),
            patch(
                "services.chat_runtime.get_turn_coordinator",
                return_value=_DummyCoordinator(),
            ),
        ):
            result = self._run(
                chat_api.chat(
                    chat_api.ChatRequest(
                        worldline_id=worldline_id,
                        message="fan out with stale event",
                        provider="openai",
                    )
                )
            )

        self.assertEqual(result["events"][-1]["type"], "assistant_message")
        self.assertEqual(result["events"][-1]["payload"]["text"], "Fallback worked.")

        sub_call = next(
            event for event in result["events"] if event["type"] == "tool_call_subagents"
        )
        user_event = next(
            event for event in result["events"] if event["type"] == "user_message"
        )
        self.assertEqual(sub_call["payload"]["requested_from_event_id"], "event_missing_abc")
        self.assertEqual(sub_call["payload"]["from_event_id"], user_event["id"])
        self.assertEqual(
            sub_call["payload"]["from_event_resolution"],
            "requested_from_event_id_not_found_fell_back_to_head",
        )

    def test_spawn_subagents_tool_is_blocked_in_subagent_child_turn(self) -> None:
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)
        with meta.get_conn() as conn:
            conn.execute(
                """
                INSERT INTO events (id, worldline_id, parent_event_id, type, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "event_seed_nested_guard",
                    worldline_id,
                    None,
                    "assistant_message",
                    json.dumps({"text": "seed"}),
                ),
            )
            conn.execute(
                "UPDATE worldlines SET head_event_id = ? WHERE id = ?",
                ("event_seed_nested_guard", worldline_id),
            )
            conn.commit()

        engine = chat_engine.ChatEngine(
            llm_client=FakeLlmClient(
                responses=[LlmResponse(text="unused", tool_calls=[])]
            )
        )
        tool_call = ToolCall(
            id="call_nested",
            name="spawn_subagents",
            arguments={
                "goal": "child should not fan out further",
                "from_event_id": "event_seed_nested_guard",
            },
        )

        result, switched_worldline = self._run(
            engine._execute_tool_call(
                worldline_id=worldline_id,
                tool_call=tool_call,
                carried_user_message="nested fanout",
                allowed_external_aliases=None,
                on_event=None,
                on_delta=None,
                subagent_depth=1,
            )
        )

        self.assertIsNone(switched_worldline)
        self.assertEqual(
            result.get("error_code"),
            "spawn_subagents_nested_not_allowed",
        )

    def test_chat_stream_emits_subagent_progress_deltas(self) -> None:
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)
        with meta.get_conn() as conn:
            conn.execute(
                """
                INSERT INTO events (id, worldline_id, parent_event_id, type, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "event_seed_progress",
                    worldline_id,
                    None,
                    "assistant_message",
                    json.dumps({"text": "seed"}),
                ),
            )
            conn.execute(
                "UPDATE worldlines SET head_event_id = ? WHERE id = ?",
                ("event_seed_progress", worldline_id),
            )
            conn.commit()

        fake_client = FakeLlmClient(
            responses=[
                LlmResponse(
                    text=None,
                    tool_calls=[
                        ToolCall(
                            id="call_spawn_progress",
                            name="spawn_subagents",
                            arguments={
                                "from_event_id": "event_seed_progress",
                                "tasks": [
                                    {"message": "task one", "label": "one"},
                                    {"message": "task two", "label": "two"},
                                ],
                            },
                        )
                    ],
                ),
                LlmResponse(text="done", tool_calls=[]),
            ]
        )

        async def fake_spawn(**kwargs):
            on_progress = kwargs.get("on_progress")
            if on_progress is not None:
                await on_progress(
                    {
                        "fanout_group_id": "fanout_1",
                        "parent_tool_call_id": "call_spawn_progress",
                        "task_index": 0,
                        "task_label": "one",
                        "task_status": "running",
                        "task_count": 2,
                        "queued_count": 1,
                        "running_count": 1,
                        "completed_count": 0,
                        "failed_count": 0,
                        "timed_out_count": 0,
                    }
                )
                await on_progress(
                    {
                        "fanout_group_id": "fanout_1",
                        "parent_tool_call_id": "call_spawn_progress",
                        "task_index": 0,
                        "task_label": "one",
                        "task_status": "completed",
                        "task_count": 2,
                        "queued_count": 1,
                        "running_count": 0,
                        "completed_count": 1,
                        "failed_count": 0,
                        "timed_out_count": 0,
                    }
                )
            return {
                "fanout_group_id": "fanout_1",
                "task_count": 2,
                "requested_task_count": 2,
                "accepted_task_count": 2,
                "truncated_task_count": 0,
                "max_subagents": 8,
                "max_parallel_subagents": 3,
                "completed_count": 2,
                "failed_count": 0,
                "timed_out_count": 0,
                "loop_limit_failure_count": 0,
                "retried_task_count": 0,
                "recovered_task_count": 0,
                "failure_summary": {},
                "all_completed": True,
                "partial_failure": False,
                "tasks": [
                    {
                        "task_label": "one",
                        "status": "completed",
                        "failure_code": None,
                        "retry_count": 0,
                        "recovered": False,
                        "terminal_reason": "assistant_text_ready",
                    },
                    {
                        "task_label": "two",
                        "status": "completed",
                        "failure_code": None,
                        "retry_count": 0,
                        "recovered": False,
                        "terminal_reason": "assistant_text_ready",
                    },
                ],
            }

        with (
            patch.object(chat_api, "build_llm_client", return_value=fake_client),
            patch(
                "chat.engine.spawn_subagents_blocking",
                AsyncMock(side_effect=fake_spawn),
            ),
        ):
            response = self._run(
                chat_api.chat_stream(
                    chat_api.ChatRequest(
                        worldline_id=worldline_id,
                        message="fan out with progress",
                        provider="openai",
                    )
                )
            )
            raw_stream = self._run(self._consume_stream(response))
            payloads = self._extract_sse_payloads(raw_stream)

        progress_deltas = [
            payload["delta"]
            for payload in payloads
            if "delta" in payload and payload["delta"]["type"] == "subagent_progress"
        ]
        self.assertGreaterEqual(len(progress_deltas), 2)
        self.assertEqual(progress_deltas[0]["task_status"], "running")
        self.assertEqual(progress_deltas[-1]["task_status"], "completed")
        self.assertEqual(progress_deltas[-1]["completed_count"], 1)

    # ---- SSE streaming endpoint tests (updated for real streaming) ----------

    def test_chat_stream_returns_sse_event_frames(self) -> None:
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)
        fake_client = FakeLlmClient(
            responses=[LlmResponse(text="Streaming hello.", tool_calls=[])]
        )

        with patch.object(chat_api, "build_llm_client", return_value=fake_client):
            response = self._run(
                chat_api.chat_stream(
                    chat_api.ChatRequest(
                        worldline_id=worldline_id,
                        message="stream this",
                        provider="openrouter",
                    )
                )
            )
            self.assertIsInstance(response, StreamingResponse)
            self.assertEqual(response.media_type, "text/event-stream")

            raw_stream = self._run(self._consume_stream(response))
            payloads = self._extract_sse_payloads(raw_stream)

        event_payloads = [payload for payload in payloads if "event" in payload]
        delta_payloads = [payload for payload in payloads if "delta" in payload]

        self.assertGreaterEqual(len(event_payloads), 2)
        self.assertEqual(event_payloads[0]["seq"], 1)
        self.assertEqual(event_payloads[0]["worldline_id"], worldline_id)
        self.assertEqual(event_payloads[0]["event"]["type"], "user_message")
        self.assertEqual(event_payloads[-1]["event"]["type"], "assistant_message")
        self.assertTrue(
            any(
                payload["delta"]["type"] == "assistant_text"
                for payload in delta_payloads
            )
        )
        self.assertTrue(payloads[-1]["done"])

    def test_chat_stream_emits_tool_call_and_tool_result(self) -> None:
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)
        fake_client = FakeLlmClient(
            responses=[
                LlmResponse(
                    text=None,
                    tool_calls=[
                        ToolCall(
                            id="call_sse_sql_1",
                            name="run_sql",
                            arguments={"sql": "SELECT 1 AS x", "limit": 10},
                        )
                    ],
                ),
                LlmResponse(text="done", tool_calls=[]),
            ]
        )

        with patch.object(chat_api, "build_llm_client", return_value=fake_client):
            response = self._run(
                chat_api.chat_stream(
                    chat_api.ChatRequest(
                        worldline_id=worldline_id,
                        message="stream sql tool",
                        provider="openai",
                    )
                )
            )
            raw_stream = self._run(self._consume_stream(response))
            payloads = self._extract_sse_payloads(raw_stream)

        event_types = [
            payload["event"]["type"] for payload in payloads if "event" in payload
        ]
        self.assertEqual(
            event_types,
            ["user_message", "tool_call_sql", "tool_result_sql", "assistant_message"],
        )
        self.assertTrue(payloads[-1]["done"])

    def test_chat_stream_emits_tool_call_skipped_for_repeated_call(self) -> None:
        """Repeated-call guard emits skipped delta and keeps turn alive."""
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)
        fake_client = FakeLlmClient(
            responses=[
                LlmResponse(
                    text=None,
                    tool_calls=[
                        ToolCall(
                            id="call_repeat_1",
                            name="run_sql",
                            arguments={"sql": "SELECT 1 AS x", "limit": 10},
                        )
                    ],
                ),
                LlmResponse(
                    text=None,
                    tool_calls=[
                        ToolCall(
                            id="call_repeat_2",
                            name="run_sql",
                            arguments={"sql": "SELECT 1 AS x", "limit": 10},
                        )
                    ],
                ),
                LlmResponse(text="used previous result", tool_calls=[]),
            ]
        )

        with patch.object(chat_api, "build_llm_client", return_value=fake_client):
            response = self._run(
                chat_api.chat_stream(
                    chat_api.ChatRequest(
                        worldline_id=worldline_id,
                        message="repeat this sql",
                        provider="openai",
                    )
                )
            )
            raw_stream = self._run(self._consume_stream(response))
            payloads = self._extract_sse_payloads(raw_stream)

        event_types = [
            payload["event"]["type"] for payload in payloads if "event" in payload
        ]
        self.assertEqual(
            event_types,
            ["user_message", "tool_call_sql", "tool_result_sql", "assistant_message"],
        )

        skipped_deltas = [
            payload["delta"]
            for payload in payloads
            if "delta" in payload
            and payload["delta"].get("type") == "tool_call_sql"
            and payload["delta"].get("skipped") is True
        ]
        self.assertEqual(len(skipped_deltas), 1)
        self.assertEqual(skipped_deltas[0].get("call_id"), "call_repeat_2")
        self.assertEqual(
            skipped_deltas[0].get("reason"),
            "repeated_identical_tool_call",
        )
        self.assertTrue(payloads[-1]["done"])

    def test_chat_stream_emits_tool_call_deltas(self) -> None:
        """Verify that tool-call argument deltas are streamed as SSE frames.

        With real streaming, the delta content is raw JSON argument fragments
        (not pre-extracted code), so we verify the deltas reconstruct to the
        full arguments JSON.
        """
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)
        fake_client = FakeLlmClient(
            responses=[
                LlmResponse(
                    text=None,
                    tool_calls=[
                        ToolCall(
                            id="call_stream_delta_sql",
                            name="run_sql",
                            arguments={
                                "sql": "SELECT 123 AS value\nFROM (SELECT 1) t",
                                "limit": 5,
                            },
                        )
                    ],
                ),
                LlmResponse(text="done", tool_calls=[]),
            ]
        )

        with patch.object(chat_api, "build_llm_client", return_value=fake_client):
            response = self._run(
                chat_api.chat_stream(
                    chat_api.ChatRequest(
                        worldline_id=worldline_id,
                        message="stream sql deltas",
                        provider="openai",
                    )
                )
            )
            raw_stream = self._run(self._consume_stream(response))
            payloads = self._extract_sse_payloads(raw_stream)

        sql_deltas = [
            payload["delta"]
            for payload in payloads
            if "delta" in payload and payload["delta"]["type"] == "tool_call_sql"
        ]
        # There should be at least one delta with content and one with done=True
        self.assertTrue(any(delta.get("delta") for delta in sql_deltas))
        self.assertTrue(any(delta.get("done") for delta in sql_deltas))

        # The concatenated delta text should be valid JSON that reconstructs
        # the original arguments.
        raw_chunks = [
            delta["delta"]
            for delta in sql_deltas
            if isinstance(delta.get("delta"), str)
        ]
        reconstructed = json.loads("".join(raw_chunks))
        self.assertEqual(
            reconstructed,
            {"sql": "SELECT 123 AS value\nFROM (SELECT 1) t", "limit": 5},
        )

    def test_chat_stream_emits_state_transition_deltas(self) -> None:
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)
        fake_client = FakeLlmClient(
            responses=[
                LlmResponse(
                    text=None,
                    tool_calls=[
                        ToolCall(
                            id="call_state_sql",
                            name="run_sql",
                            arguments={"sql": "SELECT 1 AS x", "limit": 1},
                        )
                    ],
                ),
                LlmResponse(text="done", tool_calls=[]),
            ]
        )

        with patch.object(chat_api, "build_llm_client", return_value=fake_client):
            response = self._run(
                chat_api.chat_stream(
                    chat_api.ChatRequest(
                        worldline_id=worldline_id,
                        message="stream state transitions",
                        provider="openai",
                    )
                )
            )
            raw_stream = self._run(self._consume_stream(response))
            payloads = self._extract_sse_payloads(raw_stream)

        state_deltas = [
            payload["delta"]
            for payload in payloads
            if "delta" in payload and payload["delta"].get("type") == "state_transition"
        ]
        self.assertGreaterEqual(len(state_deltas), 4)
        reasons = [delta.get("reason") for delta in state_deltas]
        self.assertIn("turn_started", reasons)
        self.assertIn("tool_call:run_sql", reasons)
        self.assertIn("tool_result:run_sql_success", reasons)
        self.assertIn("assistant_message_persisted", reasons)
        self.assertEqual(state_deltas[0].get("to_state"), "planning")

    def test_chat_stream_retries_invalid_python_payload_without_persisting_error_cells(
        self,
    ) -> None:
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)
        fake_client = FakeLlmClient(
            responses=[
                LlmResponse(
                    text=None,
                    tool_calls=[
                        ToolCall(
                            id="call_py_invalid",
                            name="run_python",
                            arguments={},
                        )
                    ],
                ),
                LlmResponse(
                    text=None,
                    tool_calls=[
                        ToolCall(
                            id="call_py_valid",
                            name="run_python",
                            arguments={"code": "print('ok')", "timeout": 10},
                        )
                    ],
                ),
                LlmResponse(text="python finished", tool_calls=[]),
            ]
        )
        fake_execute_python_tool = AsyncMock(
            return_value={
                "stdout": "ok\n",
                "stderr": "",
                "error": None,
                "artifacts": [],
                "previews": {"dataframes": []},
                "execution_ms": 9,
            }
        )

        with (
            patch.object(chat_api, "build_llm_client", return_value=fake_client),
            patch("chat.engine.execute_python_tool", fake_execute_python_tool),
        ):
            response = self._run(
                chat_api.chat_stream(
                    chat_api.ChatRequest(
                        worldline_id=worldline_id,
                        message="continue with python",
                        provider="openrouter",
                    )
                )
            )
            raw_stream = self._run(self._consume_stream(response))
            payloads = self._extract_sse_payloads(raw_stream)

        self.assertEqual(fake_execute_python_tool.await_count, 1)

        event_payloads = [
            payload["event"] for payload in payloads if "event" in payload
        ]
        event_types = [event["type"] for event in event_payloads]
        self.assertEqual(
            event_types,
            ["user_message", "assistant_message"],
        )

        invalid_skips = [
            payload["delta"]
            for payload in payloads
            if "delta" in payload
            and payload["delta"].get("type") == "tool_call_python"
            and payload["delta"].get("skipped") is True
            and payload["delta"].get("reason") == "invalid_tool_payload"
        ]
        self.assertEqual(len(invalid_skips), 1)
        self.assertEqual(invalid_skips[0].get("call_id"), "call_py_invalid")

        self.assertFalse(
            any(event["type"] == "tool_result_python" for event in event_payloads)
        )

    def test_chat_includes_sql_data_checkpoint_in_followup_llm_context(self) -> None:
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)

        class CaptureClient(FakeLlmClient):
            def __init__(self, responses: list[LlmResponse]) -> None:
                super().__init__(responses)
                self.generate_kwargs: list[dict] = []

            async def generate(self, **kwargs) -> LlmResponse:
                self.generate_kwargs.append(kwargs)
                return await super().generate(**kwargs)

        fake_client = CaptureClient(
            responses=[
                LlmResponse(
                    text=None,
                    tool_calls=[
                        ToolCall(
                            id="call_data_intent_sql",
                            name="run_sql",
                            arguments={"sql": "SELECT 1 AS x", "limit": 5},
                        )
                    ],
                ),
                LlmResponse(text="done", tool_calls=[]),
            ]
        )

        with patch.object(chat_api, "build_llm_client", return_value=fake_client):
            result = self._run(
                chat_api.chat(
                    chat_api.ChatRequest(
                        worldline_id=worldline_id,
                        message="query and continue",
                        provider="openai",
                    )
                )
            )

        self.assertEqual(fake_client.calls, 2)
        self.assertEqual(result["events"][-1]["type"], "assistant_message")

        second_call_messages = fake_client.generate_kwargs[1]["messages"]
        checkpoint_messages = [
            msg
            for msg in second_call_messages
            if msg.role == "system"
            and isinstance(msg.content, str)
            and "SQL-to-Python data checkpoint" in msg.content
        ]
        self.assertEqual(len(checkpoint_messages), 1)
        self.assertIn('"row_count": 1', checkpoint_messages[0].content)
        self.assertIn("SELECT 1 AS x", checkpoint_messages[0].content)

    def test_chat_stream_enforces_python_tool_before_finalizing_python_intent(
        self,
    ) -> None:
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)
        fake_client = FakeLlmClient(
            responses=[
                LlmResponse(text="I can do that.", tool_calls=[]),
                LlmResponse(
                    text=None,
                    tool_calls=[
                        ToolCall(
                            id="call_stream_required_py",
                            name="run_python",
                            arguments={"code": "print('stream')", "timeout": 10},
                        )
                    ],
                ),
                LlmResponse(text="stream done", tool_calls=[]),
            ]
        )
        fake_execute_python_tool = AsyncMock(
            return_value={
                "stdout": "stream\n",
                "stderr": "",
                "error": None,
                "artifacts": [],
                "previews": {"dataframes": []},
                "execution_ms": 8,
            }
        )

        with (
            patch.object(chat_api, "build_llm_client", return_value=fake_client),
            patch("chat.engine.execute_python_tool", fake_execute_python_tool),
        ):
            response = self._run(
                chat_api.chat_stream(
                    chat_api.ChatRequest(
                        worldline_id=worldline_id,
                        message="continue with python chart",
                        provider="openrouter",
                    )
                )
            )
            raw_stream = self._run(self._consume_stream(response))
            payloads = self._extract_sse_payloads(raw_stream)

        self.assertEqual(fake_client.calls, 3)
        self.assertEqual(fake_execute_python_tool.await_count, 1)

        event_types = [
            payload["event"]["type"] for payload in payloads if "event" in payload
        ]
        self.assertEqual(
            event_types,
            ["user_message", "assistant_message"],
        )

        state_deltas = [
            payload["delta"]
            for payload in payloads
            if "delta" in payload and payload["delta"].get("type") == "state_transition"
        ]
        reasons = [delta.get("reason") for delta in state_deltas]
        self.assertIn("required_tool_missing:run_python", reasons)
        self.assertIn("retry_after_missing_required_tool", reasons)

    def test_chat_enforces_python_tool_before_finalizing_python_intent(self) -> None:
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)
        fake_client = FakeLlmClient(
            responses=[
                LlmResponse(text="I can summarize that for you.", tool_calls=[]),
                LlmResponse(
                    text=None,
                    tool_calls=[
                        ToolCall(
                            id="call_required_py",
                            name="run_python",
                            arguments={"code": "print('ok')", "timeout": 10},
                        )
                    ],
                ),
                LlmResponse(text="Python analysis complete.", tool_calls=[]),
            ]
        )
        fake_execute_python_tool = AsyncMock(
            return_value={
                "stdout": "ok\n",
                "stderr": "",
                "error": None,
                "artifacts": [],
                "previews": {"dataframes": []},
                "execution_ms": 14,
            }
        )

        with (
            patch.object(chat_api, "build_llm_client", return_value=fake_client),
            patch("chat.engine.execute_python_tool", fake_execute_python_tool),
        ):
            result = self._run(
                chat_api.chat(
                    chat_api.ChatRequest(
                        worldline_id=worldline_id,
                        message="Please continue with python analysis and make a chart",
                        provider="openai",
                    )
                )
            )

        self.assertEqual(fake_client.calls, 3)
        self.assertEqual(fake_execute_python_tool.await_count, 1)
        self.assertEqual(
            [event["type"] for event in result["events"]],
            ["user_message", "assistant_message"],
        )

        assistant_payload = result["events"][-1]["payload"]
        self.assertEqual(assistant_payload["text"], "Python analysis complete.")
        self.assertEqual(
            assistant_payload["turn_stats"]["required_tools"], ["run_python"]
        )
        self.assertEqual(assistant_payload["turn_stats"]["python_success_count"], 1)
        transition_reasons = [
            str(transition.get("reason"))
            for transition in assistant_payload.get("state_trace", [])
            if isinstance(transition, dict)
        ]
        self.assertIn("required_tool_missing:run_python", transition_reasons)
        self.assertIn("retry_after_missing_required_tool", transition_reasons)

    def test_engine_run_turn_allow_tools_false_disables_tools_and_still_persists_assistant(
        self,
    ) -> None:
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)

        class CaptureClient(FakeLlmClient):
            def __init__(self, responses: list[LlmResponse]) -> None:
                super().__init__(responses)
                self.generate_kwargs: list[dict[str, object]] = []

            async def generate(self, **kwargs) -> LlmResponse:
                self.generate_kwargs.append(kwargs)
                return await super().generate(**kwargs)

        fake_client = CaptureClient(
            responses=[LlmResponse(text="Synthesis complete without tools.", tool_calls=[])]
        )
        engine = chat_engine.ChatEngine(llm_client=fake_client, max_iterations=4)

        active_worldline_id, events = self._run(
            engine.run_turn(
                worldline_id=worldline_id,
                message="Please continue with python analysis and make a chart",
                allow_tools=False,
            )
        )

        self.assertEqual(active_worldline_id, worldline_id)
        self.assertEqual(fake_client.calls, 1)
        self.assertEqual(len(fake_client.generate_kwargs), 1)
        self.assertEqual(fake_client.generate_kwargs[0]["tools"], [])
        self.assertEqual(
            [event["type"] for event in events],
            ["user_message", "assistant_message"],
        )

        assistant_payload = events[-1]["payload"]
        self.assertEqual(assistant_payload["text"], "Synthesis complete without tools.")
        self.assertEqual(assistant_payload["turn_stats"]["required_tools"], [])

        transition_reasons = [
            str(transition.get("reason"))
            for transition in assistant_payload.get("state_trace", [])
            if isinstance(transition, dict)
        ]
        self.assertNotIn("required_tool_missing:run_python", transition_reasons)
        self.assertNotIn("retry_after_missing_required_tool", transition_reasons)

    # ---- new test: assistant_plan when text accompanies tool calls -----------

    def test_chat_stream_emits_assistant_plan_when_text_with_tool_calls(self) -> None:
        """When the LLM returns text AND tool calls, the text should be
        persisted as an ``assistant_plan`` event (not lost).
        """
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)
        fake_client = FakeLlmClient(
            responses=[
                LlmResponse(
                    text="Let me run a query to check.",
                    tool_calls=[
                        ToolCall(
                            id="call_plan_sql",
                            name="run_sql",
                            arguments={"sql": "SELECT 42 AS answer", "limit": 1},
                        )
                    ],
                ),
                LlmResponse(text="The answer is 42.", tool_calls=[]),
            ]
        )

        with patch.object(chat_api, "build_llm_client", return_value=fake_client):
            # Test via the non-streaming endpoint (assistant_plan is persisted)
            result = self._run(
                chat_api.chat(
                    chat_api.ChatRequest(
                        worldline_id=worldline_id,
                        message="what is the answer?",
                        provider="openai",
                    )
                )
            )

        event_types = [event["type"] for event in result["events"]]
        self.assertEqual(
            event_types,
            [
                "user_message",
                "assistant_plan",
                "tool_call_sql",
                "tool_result_sql",
                "assistant_message",
            ],
        )
        plan_event = next(e for e in result["events"] if e["type"] == "assistant_plan")
        self.assertEqual(
            plan_event["payload"]["text"],
            "Let me run a query to check.",
        )
        self.assertEqual(
            result["events"][-1]["payload"]["text"],
            "The answer is 42.",
        )

    def test_chat_stream_emits_assistant_plan_via_sse(self) -> None:
        """Via the SSE endpoint, assistant_plan should appear as both:
        - streaming text deltas (assistant_text)
        - a persisted event (assistant_plan)
        """
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)
        fake_client = FakeLlmClient(
            responses=[
                LlmResponse(
                    text="Thinking aloud here.",
                    tool_calls=[
                        ToolCall(
                            id="call_plan_sse",
                            name="run_sql",
                            arguments={"sql": "SELECT 1", "limit": 1},
                        )
                    ],
                ),
                LlmResponse(text="All done.", tool_calls=[]),
            ]
        )

        with patch.object(chat_api, "build_llm_client", return_value=fake_client):
            response = self._run(
                chat_api.chat_stream(
                    chat_api.ChatRequest(
                        worldline_id=worldline_id,
                        message="think and act",
                        provider="openai",
                    )
                )
            )
            raw_stream = self._run(self._consume_stream(response))
            payloads = self._extract_sse_payloads(raw_stream)

        # Check that we got assistant_text deltas (the thinking text streamed)
        text_deltas = [
            p["delta"]
            for p in payloads
            if "delta" in p and p["delta"]["type"] == "assistant_text"
        ]
        self.assertTrue(len(text_deltas) > 0, "Expected assistant_text deltas")

        # Check that we got an assistant_plan persisted event
        event_types = [p["event"]["type"] for p in payloads if "event" in p]
        self.assertIn("assistant_plan", event_types)
        self.assertIn("tool_call_sql", event_types)
        self.assertIn("assistant_message", event_types)

        # The final payload should be done
        self.assertTrue(payloads[-1]["done"])

    def test_chat_stream_disconnect_does_not_cancel_subagent_terminal_result(self) -> None:
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)
        with meta.get_conn() as conn:
            conn.execute(
                """
                INSERT INTO events (id, worldline_id, parent_event_id, type, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "event_seed_stream_disconnect",
                    worldline_id,
                    None,
                    "assistant_message",
                    json.dumps({"text": "seed"}),
                ),
            )
            conn.execute(
                "UPDATE worldlines SET head_event_id = ? WHERE id = ?",
                ("event_seed_stream_disconnect", worldline_id),
            )
            conn.commit()

        fake_client = FakeLlmClient(
            responses=[
                LlmResponse(
                    text=None,
                    tool_calls=[
                        ToolCall(
                            id="call_stream_disconnect_spawn",
                            name="spawn_subagents",
                            arguments={
                                "from_event_id": "event_seed_stream_disconnect",
                                "goal": "parallelize analysis",
                                "timeout_s": 2,
                                "max_iterations": 4,
                            },
                        )
                    ],
                ),
                LlmResponse(text="finalized", tool_calls=[]),
            ]
        )

        async def delayed_spawn(**kwargs):
            await asyncio.sleep(0.2)
            return {
                "fanout_group_id": "fanout_stream_disconnect",
                "task_count": 1,
                "completed_count": 1,
                "failed_count": 0,
                "timed_out_count": 0,
                "loop_limit_failure_count": 0,
                "retried_task_count": 0,
                "recovered_task_count": 0,
                "failure_summary": {},
                "all_completed": True,
                "tasks": [
                    {
                        "task_label": "one",
                        "status": "completed",
                        "failure_code": None,
                        "retry_count": 0,
                        "recovered": False,
                        "terminal_reason": "assistant_text_ready",
                    }
                ],
            }

        async def scenario() -> str:
            with (
                patch.object(chat_api, "build_llm_client", return_value=fake_client),
                patch(
                    "chat.engine.should_use_semantic_lane",
                    AsyncMock(return_value=(False, None)),
                ),
                patch(
                    "chat.engine.spawn_subagents_blocking",
                    AsyncMock(side_effect=delayed_spawn),
                ),
            ):
                response = await chat_api.chat_stream(
                    chat_api.ChatRequest(
                        worldline_id=worldline_id,
                        message="run fanout then disconnect",
                        provider="openai",
                    )
                )
                _ = await self._consume_stream_first_n(response, 1)
                await asyncio.sleep(1.2)
                events_payload = await worldlines.get_worldline_events(
                    worldline_id,
                    limit=100,
                )
                event_types = [event["type"] for event in events_payload["events"]]
                return ",".join(event_types)

        event_types_serialized = self._run(scenario())
        self.assertIn("tool_call_subagents", event_types_serialized)
        self.assertIn("tool_result_subagents", event_types_serialized)

    # ---- job queue endpoint tests --------------------------------------------

    def test_create_chat_job_processes_in_background(self) -> None:
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)
        fake_client = FakeLlmClient(
            responses=[LlmResponse(text="Background complete.", tool_calls=[])]
        )

        async def scenario() -> tuple[dict, dict]:
            with patch.object(
                chat_runtime, "build_llm_client", return_value=fake_client
            ):
                created = await chat_api.create_chat_job(
                    chat_api.ChatJobRequest(
                        worldline_id=worldline_id,
                        message="run this in background",
                        provider="openai",
                    )
                )
                done = await self._wait_for_job_status(
                    created["id"],
                    expected={"completed", "failed"},
                    timeout_s=3.0,
                )
                return created, done

        created, done = self._run(scenario())

        self.assertEqual(created["status"], "queued")
        self.assertIn(created["queue_position"], {1, 2})
        self.assertEqual(done["status"], "completed")
        self.assertEqual(done["result_worldline_id"], worldline_id)
        self.assertIn(
            "Background complete.", done["result_summary"]["assistant_preview"]
        )

    def test_chat_job_list_and_ack(self) -> None:
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)
        fake_client = FakeLlmClient(
            responses=[LlmResponse(text="Done for ack.", tool_calls=[])]
        )

        async def scenario() -> tuple[dict, dict, dict]:
            with patch.object(
                chat_runtime, "build_llm_client", return_value=fake_client
            ):
                created = await chat_api.create_chat_job(
                    chat_api.ChatJobRequest(
                        worldline_id=worldline_id,
                        message="job to ack",
                        provider="openrouter",
                    )
                )
                await self._wait_for_job_status(
                    created["id"],
                    expected={"completed", "failed"},
                    timeout_s=3.0,
                )
                listed = await chat_api.list_chat_jobs(
                    thread_id=thread_id,
                    status="completed",
                    limit=50,
                )
                acked = await chat_api.ack_chat_job(
                    created["id"],
                    chat_api.ChatJobAckRequest(seen=True),
                )
                return created, listed, acked

        created, listed, acked = self._run(scenario())
        self.assertTrue(any(job["id"] == created["id"] for job in listed["jobs"]))
        self.assertEqual(acked["status"], "completed")
        self.assertIsNotNone(acked["seen_at"])

    def test_chat_jobs_same_worldline_execute_fifo(self) -> None:
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)
        fake_client = FakeLlmClient(
            responses=[
                LlmResponse(text="first done", tool_calls=[]),
                LlmResponse(text="second done", tool_calls=[]),
            ]
        )

        async def scenario() -> list[str]:
            with patch.object(chat_api, "build_llm_client", return_value=fake_client):
                first = await chat_api.create_chat_job(
                    chat_api.ChatJobRequest(
                        worldline_id=worldline_id,
                        message="first message",
                        provider="openai",
                    )
                )
                second = await chat_api.create_chat_job(
                    chat_api.ChatJobRequest(
                        worldline_id=worldline_id,
                        message="second message",
                        provider="openai",
                    )
                )
                await self._wait_for_job_status(
                    first["id"],
                    expected={"completed", "failed"},
                    timeout_s=3.0,
                )
                await self._wait_for_job_status(
                    second["id"],
                    expected={"completed", "failed"},
                    timeout_s=3.0,
                )
                events_payload = await worldlines.get_worldline_events(
                    worldline_id, limit=50
                )
                messages = [
                    event["payload"].get("text")
                    for event in events_payload["events"]
                    if event["type"] == "user_message"
                ]
                return [text for text in messages if isinstance(text, str)]

        user_messages = self._run(scenario())
        self.assertEqual(user_messages[-2:], ["first message", "second message"])

    def test_chat_session_prefers_running_worldline_and_keeps_creation_order(self) -> None:
        thread_id = self._create_thread()
        main_worldline_id = self._create_worldline(thread_id, "main")
        branch_worldline_id = self._create_worldline(thread_id, "branch-a")

        with meta.get_conn() as conn:
            conn.execute(
                """
                INSERT INTO chat_turn_jobs (
                    id,
                    thread_id,
                    worldline_id,
                    request_json,
                    status,
                    started_at
                )
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    meta.new_id("job"),
                    thread_id,
                    branch_worldline_id,
                    json.dumps({"message": "running"}, ensure_ascii=True),
                    "running",
                ),
            )
            conn.commit()

        session = self._run(chat_api.get_chat_session(thread_id))
        worldline_ids = [row["id"] for row in session["worldlines"]]

        self.assertEqual(worldline_ids, [main_worldline_id, branch_worldline_id])
        self.assertEqual(session["preferred_worldline_id"], branch_worldline_id)
        self.assertTrue(
            any(job["worldline_id"] == branch_worldline_id for job in session["jobs"])
        )


if __name__ == "__main__":
    unittest.main()
