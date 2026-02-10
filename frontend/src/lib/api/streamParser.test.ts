import { describe, it, expect, vi } from "vitest";
import { createStreamProcessor } from "./streamParser";
import type {
  SseEventFrame,
  SseDeltaFrame,
  SseDoneFrame,
} from "$lib/types";

describe("createStreamProcessor", () => {
  function encode(text: string): Uint8Array {
    return new TextEncoder().encode(text);
  }

  it("processes single complete event frame", () => {
    const onEvent = vi.fn();
    const processor = createStreamProcessor({ onEvent });

    const frame =
      'event: event\ndata: {"seq":1,"worldline_id":"wl_1","event":{"id":"e1","type":"user_message","payload":{"text":"hi"},"parent_event_id":null,"created_at":"2024-01-01T00:00:00Z"}}\n\n';
    processor.processChunk(encode(frame));
    processor.flush();

    expect(onEvent).toHaveBeenCalledTimes(1);
    const call = onEvent.mock.calls[0][0] as SseEventFrame;
    expect(call.seq).toBe(1);
    expect(call.worldline_id).toBe("wl_1");
    expect(call.event.type).toBe("user_message");
  });

  it("processes multiple frames in single chunk", () => {
    const onEvent = vi.fn();
    const onDelta = vi.fn();
    const processor = createStreamProcessor({ onEvent, onDelta });

    const frames =
      'event: event\ndata: {"seq":1,"worldline_id":"wl_1","event":{"id":"e1","type":"user_message","payload":{},"parent_event_id":null,"created_at":"2024-01-01T00:00:00Z"}}\n\n' +
      'event: delta\ndata: {"seq":2,"worldline_id":"wl_1","delta":{"type":"assistant_text","delta":"hello"}}\n\n' +
      'event: done\ndata: {"seq":3,"worldline_id":"wl_1","done":true}\n\n';

    processor.processChunk(encode(frames));
    processor.flush();

    expect(onEvent).toHaveBeenCalledTimes(1);
    expect(onDelta).toHaveBeenCalledTimes(1);
    expect(onDelta.mock.calls[0][0].delta.type).toBe("assistant_text");
  });

  it("handles split frames across multiple chunks", () => {
    const onEvent = vi.fn();
    const processor = createStreamProcessor({ onEvent });

    const part1 = 'event: event\ndata: {"seq":1,"worldline_id":"wl_1","event":{"id":"e1","type":"user_message","payload":{},"parent_event_id":null,"created_at":"';
    const part2 = '2024-01-01T00:00:00Z"}}\n\n';

    processor.processChunk(encode(part1));
    expect(onEvent).not.toHaveBeenCalled();

    processor.processChunk(encode(part2));
    processor.flush();

    expect(onEvent).toHaveBeenCalledTimes(1);
  });

  it("processes buffered tail frame on flush", () => {
    const onDone = vi.fn();
    const processor = createStreamProcessor({
      onEvent: vi.fn(),
      onDone,
    });

    const frame =
      'event: done\ndata: {"seq":5,"worldline_id":"wl_1","done":true}';
    processor.processChunk(encode(frame));

    expect(onDone).not.toHaveBeenCalled();

    processor.flush();

    expect(onDone).toHaveBeenCalledTimes(1);
    const call = onDone.mock.calls[0][0] as SseDoneFrame;
    expect(call.seq).toBe(5);
    expect(call.done).toBe(true);
  });

  it("handles empty data lines gracefully", () => {
    const onEvent = vi.fn();
    const processor = createStreamProcessor({ onEvent });

    const frame = "event: event\n\n";
    processor.processChunk(encode(frame));
    processor.flush();

    expect(onEvent).not.toHaveBeenCalled();
  });

  it("handles malformed JSON by calling onError", () => {
    const onError = vi.fn();
    const processor = createStreamProcessor({
      onEvent: vi.fn(),
      onError,
    });

    const frame =
      'event: event\ndata: {invalid json}\n\nevent: event\ndata: {"seq":2,"worldline_id":"wl_1","event":{"id":"e2","type":"assistant_message","payload":{},"parent_event_id":null,"created_at":"2024-01-01T00:00:00Z"}}\n\n';

    processor.processChunk(encode(frame));
    processor.flush();

    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError).toHaveBeenCalledWith("Failed to parse stream frame");
  });

  it("continues processing after malformed frame", () => {
    const onEvent = vi.fn();
    const onError = vi.fn();
    const processor = createStreamProcessor({ onEvent, onError });

    const frame =
      'event: event\ndata: {invalid}\n\nevent: event\ndata: {"seq":2,"worldline_id":"wl_1","event":{"id":"e2","type":"assistant_message","payload":{},"parent_event_id":null,"created_at":"2024-01-01T00:00:00Z"}}\n\n';

    processor.processChunk(encode(frame));
    processor.flush();

    expect(onError).toHaveBeenCalledTimes(1);
    expect(onEvent).toHaveBeenCalledTimes(1);
    const call = onEvent.mock.calls[0][0] as SseEventFrame;
    expect(call.seq).toBe(2);
  });

  it("routes error events to onError callback", () => {
    const onError = vi.fn();
    const processor = createStreamProcessor({
      onEvent: vi.fn(),
      onError,
    });

    const frame =
      'event: error\ndata: {"seq":1,"error":"Something went wrong"}\n\n';
    processor.processChunk(encode(frame));
    processor.flush();

    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError).toHaveBeenCalledWith("Something went wrong");
  });

  it("handles error event without error field", () => {
    const onError = vi.fn();
    const processor = createStreamProcessor({
      onEvent: vi.fn(),
      onError,
    });

    const frame = 'event: error\ndata: {"seq":1}\n\n';
    processor.processChunk(encode(frame));
    processor.flush();

    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError).toHaveBeenCalledWith("Unknown stream error");
  });

  it("handles multiline data fields", () => {
    const onEvent = vi.fn();
    const processor = createStreamProcessor({ onEvent });

    const frame =
      'event: event\ndata: {"seq":1,"worldline_id":"wl_1","event":{"id":"e1","type":"assistant_message","payload":{"text":"line1\\nline2"},"parent_event_id":null,"created_at":"2024-01-01T00:00:00Z"}}\n\n';
    processor.processChunk(encode(frame));
    processor.flush();

    expect(onEvent).toHaveBeenCalledTimes(1);
    const call = onEvent.mock.calls[0][0] as SseEventFrame;
    expect(call.event.payload.text).toBe("line1\nline2");
  });

  it("maintains frame ordering within chunks", () => {
    const onEvent = vi.fn();
    const processor = createStreamProcessor({ onEvent });

    const frames =
      'event: event\ndata: {"seq":1,"worldline_id":"wl_1","event":{"id":"e1","type":"user_message","payload":{},"parent_event_id":null,"created_at":"2024-01-01T00:00:00Z"}}\n\n' +
      'event: event\ndata: {"seq":2,"worldline_id":"wl_1","event":{"id":"e2","type":"assistant_message","payload":{},"parent_event_id":null,"created_at":"2024-01-01T00:00:01Z"}}\n\n' +
      'event: event\ndata: {"seq":3,"worldline_id":"wl_1","event":{"id":"e3","type":"user_message","payload":{},"parent_event_id":null,"created_at":"2024-01-01T00:00:02Z"}}\n\n';

    processor.processChunk(encode(frames));
    processor.flush();

    expect(onEvent).toHaveBeenCalledTimes(3);
    const seqs = onEvent.mock.calls.map((call) => (call[0] as SseEventFrame).seq);
    expect(seqs).toEqual([1, 2, 3]);
  });
});
