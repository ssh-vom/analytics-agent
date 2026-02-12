import unittest

from chat.tooling import normalize_tool_arguments


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


if __name__ == "__main__":
    unittest.main()
