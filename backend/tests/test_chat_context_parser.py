import unittest

from chat.engine import _extract_output_type, _extract_selected_external_aliases


class ChatContextParserTests(unittest.TestCase):
    def test_returns_none_when_context_missing(self) -> None:
        self.assertIsNone(_extract_selected_external_aliases("hello"))

    def test_parses_selected_connector_aliases(self) -> None:
        message = """analyze revenue

<context>
- output_type=report
- connectors=warehouse, archive, warehouse
</context>
"""
        self.assertEqual(
            _extract_selected_external_aliases(message),
            ["warehouse", "archive"],
        )

    def test_parses_connectors_none_as_empty_list(self) -> None:
        message = """show summary

<context>
- connectors=none
</context>
"""
        self.assertEqual(_extract_selected_external_aliases(message), [])

    def test_parses_output_type_report(self) -> None:
        message = """please analyze

<context>
- output_type=report
</context>
"""
        self.assertEqual(_extract_output_type(message), "report")

    def test_parses_output_type_dashboard(self) -> None:
        message = """please analyze

<context>
- output_type=dashboard
</context>
"""
        self.assertEqual(_extract_output_type(message), "dashboard")

    def test_parses_output_type_none_as_unset(self) -> None:
        message = """please analyze

<context>
- output_type=none
</context>
"""
        self.assertIsNone(_extract_output_type(message))


if __name__ == "__main__":
    unittest.main()
