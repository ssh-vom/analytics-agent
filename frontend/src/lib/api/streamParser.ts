import type {
  SseDeltaFrame,
  SseDoneFrame,
  SseEventFrame,
} from "$lib/types";

export interface StreamCallbacks {
  onEvent: (frame: SseEventFrame) => void;
  onDelta?: (frame: SseDeltaFrame) => void;
  onDone?: (frame: SseDoneFrame) => void;
  onError?: (error: string) => void;
}

export function createStreamProcessor(callbacks: StreamCallbacks) {
  let buffer = "";
  const decoder = new TextDecoder();

  const processFrame = (frame: string): void => {
    if (!frame.trim()) {
      return;
    }

    let eventName = "event";
    const dataLines: string[] = [];
    for (const line of frame.split("\n")) {
      if (line.startsWith("event:")) {
        eventName = line.slice(6).trim();
        continue;
      }
      if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trimStart());
      }
    }

    if (dataLines.length === 0) {
      return;
    }

    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(dataLines.join("\n")) as Record<string, unknown>;
    } catch {
      callbacks.onError?.("Failed to parse stream frame");
      return;
    }
    if (eventName === "event") {
      callbacks.onEvent(parsed as unknown as SseEventFrame);
      return;
    }
    if (eventName === "delta") {
      callbacks.onDelta?.(parsed as unknown as SseDeltaFrame);
      return;
    }
    if (eventName === "done") {
      callbacks.onDone?.(parsed as unknown as SseDoneFrame);
      return;
    }
    if (eventName === "error") {
      const error =
        typeof parsed.error === "string"
          ? parsed.error
          : "Unknown stream error";
      callbacks.onError?.(error);
    }
  };

  const processChunk = (chunk: Uint8Array): void => {
    buffer += decoder.decode(chunk, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";

    for (const frame of frames) {
      processFrame(frame);
    }
  };

  const flush = (): void => {
    if (buffer.trim()) {
      processFrame(buffer);
      buffer = "";
    }
  };

  return {
    processChunk,
    flush,
    processFrame,
  };
}
