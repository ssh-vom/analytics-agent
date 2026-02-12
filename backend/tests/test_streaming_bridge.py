import asyncio
import unittest
from collections.abc import AsyncIterator

from chat.llm_client import ChatMessage, StreamChunk, ToolDefinition
from chat.streaming_bridge import stream_llm_response


class _FakeStreamingClient:
    def __init__(self, chunks: list[StreamChunk]) -> None:
        self._chunks = chunks

    async def generate(self, **kwargs):  # pragma: no cover - not used in these tests
        raise AssertionError("generate() should not be used in streaming bridge tests")

    async def generate_stream(self, **kwargs) -> AsyncIterator[StreamChunk]:
        for chunk in self._chunks:
            yield chunk


class StreamingBridgeTests(unittest.TestCase):
    def _run(self, coro):
        return asyncio.run(coro)

    def test_maps_mismatched_delta_call_id_to_started_tool_call(self) -> None:
        client = _FakeStreamingClient(
            [
                StreamChunk(
                    type="tool_call_start",
                    tool_call_id="call_py_1",
                    tool_name="run_python",
                ),
                StreamChunk(
                    type="tool_call_delta",
                    tool_call_id="item_abc",
                    arguments_delta='{"code":"print(1)","timeout":10}',
                ),
                StreamChunk(
                    type="tool_call_done",
                    tool_call_id="item_abc",
                ),
            ]
        )

        deltas: list[dict] = []

        async def on_delta(_worldline_id: str, payload: dict) -> None:
            deltas.append(payload)

        response = self._run(
            stream_llm_response(
                llm_client=client,
                worldline_id="worldline_test",
                messages=[ChatMessage(role="user", content="run python")],
                tools=[
                    ToolDefinition(
                        name="run_python",
                        description="run python",
                        input_schema={"type": "object"},
                    )
                ],
                max_output_tokens=500,
                on_delta=on_delta,
            )
        )

        self.assertEqual(len(response.tool_calls), 1)
        self.assertEqual(response.tool_calls[0].name, "run_python")
        self.assertEqual(response.tool_calls[0].arguments["code"], "print(1)")

        python_deltas = [
            payload for payload in deltas if payload.get("type") == "tool_call_python"
        ]
        self.assertTrue(any(payload.get("done") for payload in python_deltas))


if __name__ == "__main__":
    unittest.main()
