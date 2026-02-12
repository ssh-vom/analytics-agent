import unittest

from chat.engine import ChatEngine


class _DummyLlmClient:
    async def generate(self, **kwargs):  # pragma: no cover - unused in these tests
        raise AssertionError("generate should not be called")

    async def generate_stream(
        self, **kwargs
    ):  # pragma: no cover - unused in these tests
        if False:
            yield None
        raise AssertionError("generate_stream should not be called")


class StateMachineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = ChatEngine(llm_client=_DummyLlmClient())

    def test_valid_transition_is_recorded(self) -> None:
        transitions = [{"from": None, "to": "planning", "reason": "turn_started"}]

        next_state = self.engine._transition_state(
            current_state="planning",
            to_state="data_fetching",
            reason="tool_call:run_sql",
            transitions=transitions,
            worldline_id="worldline_test",
        )

        self.assertEqual(next_state, "data_fetching")
        self.assertEqual(
            transitions[-1],
            {
                "from": "planning",
                "to": "data_fetching",
                "reason": "tool_call:run_sql",
            },
        )

    def test_invalid_transition_moves_to_error(self) -> None:
        transitions = [{"from": None, "to": "planning", "reason": "turn_started"}]

        next_state = self.engine._transition_state(
            current_state="completed",
            to_state="analyzing",
            reason="invalid_test",
            transitions=transitions,
            worldline_id="worldline_test",
        )

        self.assertEqual(next_state, "error")
        self.assertTrue(
            str(transitions[-1]["reason"]).startswith("invalid_transition:")
        )

    def test_validate_python_payload_rejects_comments_only_code(self) -> None:
        payload_error = self.engine._validate_tool_payload(
            tool_name="run_python",
            arguments={"code": "# only comment\n   \n# still comment", "timeout": 30},
        )

        self.assertIsNotNone(payload_error)
        self.assertIn("comments/whitespace", str(payload_error))

    def test_build_data_intent_summary_from_sql_result(self) -> None:
        summary = self.engine._build_data_intent_summary(
            sql="SELECT month, revenue FROM sales LIMIT 5",
            sql_result={
                "columns": [
                    {"name": "month", "type": "VARCHAR"},
                    {"name": "revenue", "type": "DOUBLE"},
                ],
                "rows": [["2025-01", 100.0]],
                "row_count": 1,
                "preview_count": 1,
            },
        )

        self.assertIsNotNone(summary)
        if summary is None:
            self.fail("summary should not be None")
        self.assertEqual(summary["row_count"], 1)
        self.assertIn("month", summary["dimensions"])
        self.assertIn("revenue", summary["measures"])
        self.assertIn("SELECT month, revenue", summary["sql_preview"])


if __name__ == "__main__":
    unittest.main()
