import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import chat_api
import meta
import threads
import worldlines
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
                        provider="gemini",
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
        self.assertEqual(result["events"][1]["payload"], {"text": "Hello back!"})

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

    def test_chat_stops_repeated_identical_tool_calls_in_turn(self) -> None:
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
            ]
        )

        with patch.object(chat_api, "build_llm_client", return_value=fake_client):
            result = self._run(
                chat_api.chat(
                    chat_api.ChatRequest(
                        worldline_id=worldline_id,
                        message="run this once",
                        provider="gemini",
                    )
                )
            )

        self.assertEqual(fake_client.calls, 2)
        self.assertEqual(
            [event["type"] for event in result["events"]],
            ["user_message", "tool_call_sql", "tool_result_sql", "assistant_message"],
        )
        self.assertIn(
            "repeated the same tool call",
            result["events"][-1]["payload"]["text"],
        )

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

    def test_chat_allows_only_one_successful_python_run_per_turn(self) -> None:
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
            ]
        )

        async def fake_execute_python_tool(*args, **kwargs):
            return {
                "stdout": "ok\n",
                "stderr": "",
                "error": None,
                "artifacts": [
                    {
                        "type": "image",
                        "name": "line_2x.png",
                        "artifact_id": "artifact_fake",
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

        self.assertEqual(fake_client.calls, 2)
        self.assertEqual(
            [event["type"] for event in result["events"]],
            ["user_message", "assistant_message"],
        )
        self.assertIn(
            "Python already ran successfully in this turn",
            result["events"][-1]["payload"]["text"],
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
                        provider="gemini",
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
                        provider="gemini",
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


if __name__ == "__main__":
    unittest.main()
