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
from chat.llm_client import LlmResponse, ToolCall
from fastapi.responses import StreamingResponse


class FakeLlmClient:
    def __init__(self, responses: list[LlmResponse]) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def generate(self, **kwargs) -> LlmResponse:
        self.calls += 1
        if not self._responses:
            raise AssertionError("No fake responses left for LLM generate()")
        return self._responses.pop(0)


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
            for line in frame.splitlines():
                if line.startswith("data: "):
                    payloads.append(json.loads(line[len("data: ") :]))
        return payloads

    def _create_thread(self, title: str = "chat-test-thread") -> str:
        response = self._run(threads.create_thread(threads.CreateThreadRequest(title=title)))
        return response["thread_id"]

    def _create_worldline(self, thread_id: str, name: str = "main") -> str:
        response = self._run(
            worldlines.create_worldline(
                worldlines.CreateWorldlineRequest(thread_id=thread_id, name=name)
            )
        )
        return response["worldline_id"]

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

        self.assertGreaterEqual(len(payloads), 3)
        self.assertEqual(payloads[0]["seq"], 1)
        self.assertEqual(payloads[0]["worldline_id"], worldline_id)
        self.assertEqual(payloads[0]["event"]["type"], "user_message")
        self.assertEqual(payloads[1]["seq"], 2)
        self.assertEqual(payloads[1]["event"]["type"], "assistant_message")
        self.assertTrue(payloads[-1]["done"])


if __name__ == "__main__":
    unittest.main()
