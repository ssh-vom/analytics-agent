import unittest
from unittest.mock import patch

from chat.adapters.gemini_adapter import GeminiAdapter
from chat.adapters.openai_adapter import OpenAiAdapter
from chat.adapters.openrouter_adapter import OpenRouterAdapter
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

    def test_build_gemini_adapter_from_google_api_key_env(self) -> None:
        with patch.dict(
            "os.environ",
            {"GEMINI_API_KEY": "", "GOOGLE_API_KEY": "k2"},
            clear=False,
        ):
            client = build_llm_client(provider="gemini", model="gemini-test")
            self.assertIsInstance(client, GeminiAdapter)
            self.assertEqual(client.api_key, "k2")

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


if __name__ == "__main__":
    unittest.main()
