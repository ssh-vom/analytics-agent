import unittest
from unittest.mock import patch

from chat.adapters.openai_adapter import OpenAiAdapter
from chat.adapters.openrouter_adapter import OpenRouterAdapter
from chat.factory import build_llm_client


class LlmFactoryTests(unittest.TestCase):
    def test_build_openai_adapter(self) -> None:
        client = build_llm_client(provider="openai", model="gpt-test", api_key="k")
        self.assertIsInstance(client, OpenAiAdapter)
        self.assertEqual(client.model, "gpt-test")

    def test_build_openrouter_adapter(self) -> None:
        client = build_llm_client(
            provider="openrouter",
            model="openrouter/auto",
            api_key="k3",
        )
        self.assertIsInstance(client, OpenRouterAdapter)
        self.assertEqual(client.model, "openrouter/auto")
        self.assertEqual(client.api_key, "k3")

    def test_build_invalid_provider_raises(self) -> None:
        with self.assertRaises(ValueError):
            _ = build_llm_client(provider="invalid")

    def test_build_gemini_provider_now_raises(self) -> None:
        with self.assertRaises(ValueError):
            _ = build_llm_client(provider="gemini")


if __name__ == "__main__":
    unittest.main()
