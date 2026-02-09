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


if __name__ == "__main__":
    unittest.main()
