import json
import unittest

from chat.message_builder import build_llm_messages_from_events


class MessageBuilderTests(unittest.TestCase):
    def test_includes_artifact_inventory_memory_message(self) -> None:
        events = [
            {
                "id": "event_user_1",
                "parent_event_id": None,
                "type": "user_message",
                "payload": {"text": "analyze duplicates"},
                "created_at": "2026-02-12 10:00:00",
            },
            {
                "id": "event_call_py_1",
                "parent_event_id": "event_user_1",
                "type": "tool_call_python",
                "payload": {
                    "code": "print('ok')",
                    "timeout": 30,
                    "call_id": "call_py_1",
                },
                "created_at": "2026-02-12 10:00:01",
            },
            {
                "id": "event_result_py_1",
                "parent_event_id": "event_call_py_1",
                "type": "tool_result_python",
                "payload": {
                    "stdout": "x" * 8_000,
                    "stderr": "",
                    "error": None,
                    "artifacts": [
                        {
                            "type": "csv",
                            "name": "top_by_amount.csv",
                            "artifact_id": "artifact_top_by_amount",
                        }
                    ],
                    "execution_ms": 17,
                },
                "created_at": "2026-02-12 10:00:02",
            },
        ]

        messages = build_llm_messages_from_events(events)

        self.assertGreaterEqual(len(messages), 4)
        self.assertEqual(messages[0].role, "system")
        self.assertEqual(messages[1].role, "system")
        self.assertIn("Artifact inventory for this worldline", messages[1].content)
        self.assertIn("top_by_amount.csv", messages[1].content)

        tool_messages = [message for message in messages if message.role == "tool"]
        self.assertEqual(len(tool_messages), 1)
        payload = json.loads(tool_messages[0].content)
        self.assertEqual(payload["artifact_count"], 1)
        self.assertEqual(payload["artifacts"][0]["name"], "top_by_amount.csv")
        self.assertEqual(
            payload["artifacts"][0]["artifact_id"], "artifact_top_by_amount"
        )
        self.assertIn("stdout_tail", payload)


if __name__ == "__main__":
    unittest.main()
