import unittest

from chat.adapters.gemini_adapter import GeminiAdapter
from chat.adapters.openai_adapter import OpenAiAdapter
from chat.factory import build_llm_client


class LlmFactoryTests(unittest.TestCase):
    def test_build_openai_adapter(self) -> None:
        client = build_llm_client(provider="openai", model="gpt-test", api_key="k")
        self.assertIsInstance(client, OpenAiAdapter)
        self.assertEqual(client.model, "gpt-test")

    def test_build_gemini_adapter(self) -> None:
        client = build_llm_client(provider="gemini", model="gemini-test", api_key="k")
        self.assertIsInstance(client, GeminiAdapter)
        self.assertEqual(client.model, "gemini-test")

    def test_build_invalid_provider_raises(self) -> None:
        with self.assertRaises(ValueError):
            _ = build_llm_client(provider="invalid")


if __name__ == "__main__":
    unittest.main()
