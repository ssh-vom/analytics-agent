import unittest

from chat.adapters.gemini_adapter import _sanitize_schema, _to_gemini_role


class GeminiAdapterTests(unittest.TestCase):
    def test_to_gemini_role_maps_assistant_to_model(self) -> None:
        self.assertEqual(_to_gemini_role("assistant"), "model")
        self.assertEqual(_to_gemini_role("user"), "user")
        self.assertEqual(_to_gemini_role("model"), "model")
        self.assertEqual(_to_gemini_role("tool"), "user")

    def test_sanitize_schema_removes_additional_properties_keys(self) -> None:
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "sql": {"type": "string", "additional_properties": False},
                "filters": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {"name": {"type": "string"}},
                    },
                },
            },
        }

        sanitized = _sanitize_schema(schema)
        self.assertNotIn("additionalProperties", sanitized)
        self.assertNotIn(
            "additional_properties",
            sanitized["properties"]["sql"],
        )
        self.assertNotIn(
            "additionalProperties",
            sanitized["properties"]["filters"]["items"],
        )
        self.assertEqual(sanitized["type"], "object")
        self.assertEqual(
            sanitized["properties"]["filters"]["items"]["properties"]["name"]["type"],
            "string",
        )


if __name__ == "__main__":
    unittest.main()
