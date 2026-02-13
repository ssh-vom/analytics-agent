import unittest

from chat.tooling import (
    looks_like_complete_tool_args,
    normalize_tool_arguments,
    tool_definitions,
)


class ChatToolingNormalizationTests(unittest.TestCase):
    def test_normalize_python_arguments_from_nested_raw(self) -> None:
        raw = '{"name":"run_python","arguments":"{\\"code\\":\\"print(42)\\",\\"timeout\\":\\"15\\"}"}'
        normalized = normalize_tool_arguments("run_python", {"_raw": raw})

        self.assertEqual(normalized["code"], "print(42)")
        self.assertEqual(normalized["timeout"], 15)

    def test_normalize_python_arguments_from_script_field(self) -> None:
        normalized = normalize_tool_arguments(
            "run_python",
            {"script": "print('hello')", "timeout": "9"},
        )

        self.assertEqual(normalized["code"], "print('hello')")
        self.assertEqual(normalized["timeout"], 9)

    def test_normalize_sql_arguments_from_query_alias(self) -> None:
        normalized = normalize_tool_arguments(
            "run_sql",
            {"query": "SELECT 1", "limit": "50000"},
        )

        self.assertEqual(normalized["sql"], "SELECT 1")
        self.assertEqual(normalized["limit"], 10000)

    def test_unwraps_embedded_python_payload_string(self) -> None:
        normalized = normalize_tool_arguments(
            "run_python",
            {
                "code": '{"code":"print(42)","timeout":30}',
                "timeout": "12",
            },
        )

        self.assertEqual(normalized["code"], "print(42)")
        self.assertEqual(normalized["timeout"], 12)

    def test_extracts_code_from_incomplete_raw_via_regex(self) -> None:
        # Regex fallback can extract code from _raw even when JSON is incomplete
        normalized = normalize_tool_arguments(
            "run_python",
            {"_raw": '{"code":"print(42)"'},
        )

        self.assertEqual(normalized["code"], "print(42)")
        self.assertEqual(normalized["timeout"], 30)

    def test_normalize_python_arguments_from_query_alias(self) -> None:
        normalized = normalize_tool_arguments(
            "run_python",
            {"query": "print(LATEST_SQL_DF.head())", "timeout": 30},
        )

        self.assertEqual(normalized["code"], "print(LATEST_SQL_DF.head())")

    def test_unwraps_embedded_sql_payload_string(self) -> None:
        normalized = normalize_tool_arguments(
            "run_sql",
            {"sql": '{"sql":"SELECT 2 AS y","limit":5}'},
        )

        self.assertEqual(normalized["sql"], "SELECT 2 AS y")
        self.assertEqual(normalized["limit"], 100)

    def test_normalizes_spawn_subagents_limits_and_defaults(self) -> None:
        normalized = normalize_tool_arguments(
            "spawn_subagents",
            {
                "goal": "analyze",
                "max_subagents": "75",
                "max_parallel_subagents": "0",
                "timeout_s": "9999",
                "max_iterations": "-1",
            },
        )

        self.assertEqual(normalized["goal"], "analyze")
        self.assertEqual(normalized["max_subagents"], 50)
        self.assertEqual(normalized["max_parallel_subagents"], 1)
        self.assertEqual(normalized["timeout_s"], 1800)
        self.assertEqual(normalized["max_iterations"], 1)

    def test_spawn_subagents_tool_can_be_excluded_from_tool_definitions(self) -> None:
        with_spawn = [tool.name for tool in tool_definitions(include_python=True)]
        without_spawn = [
            tool.name
            for tool in tool_definitions(
                include_python=True,
                include_spawn_subagents=False,
            )
        ]

        self.assertIn("spawn_subagents", with_spawn)
        self.assertNotIn("spawn_subagents", without_spawn)

    def test_goal_only_spawn_payload_is_detected_as_complete(self) -> None:
        self.assertTrue(
            looks_like_complete_tool_args('{"goal":"investigate churn by cohort"}')
        )


if __name__ == "__main__":
    unittest.main()
