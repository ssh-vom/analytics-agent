import type {
  SseDeltaFrame,
  SseDoneFrame,
  SseEventFrame,
} from "$lib/types";

export interface StreamCallbacks {
  onEvent: (frame: SseEventFrame) => void | Promise<void>;
  onDelta?: (frame: SseDeltaFrame) => void | Promise<void>;
  onDone?: (frame: SseDoneFrame) => void | Promise<void>;
  onError?: (error: string) => void | Promise<void>;
}

export function createStreamProcessor(callbacks: StreamCallbacks) {
  let buffer = "";
  const decoder = new TextDecoder();

  const processFrame = async (frame: string): Promise<void> => {
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
      await callbacks.onError?.("Failed to parse stream frame");
      return;
    }
    if (eventName === "event") {
      await callbacks.onEvent(parsed as unknown as SseEventFrame);
      return;
    }
    if (eventName === "delta") {
      await callbacks.onDelta?.(parsed as unknown as SseDeltaFrame);
      return;
    }
    if (eventName === "done") {
      await callbacks.onDone?.(parsed as unknown as SseDoneFrame);
      return;
    }
    if (eventName === "error") {
      const error =
        typeof parsed.error === "string"
          ? parsed.error
          : "Unknown stream error";
      await callbacks.onError?.(error);
    }
  };

  const processChunk = async (chunk: Uint8Array): Promise<void> => {
    buffer += decoder.decode(chunk, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";

    for (const frame of frames) {
      await processFrame(frame);
    }
  };

  const flush = async (): Promise<void> => {
    if (buffer.trim()) {
      await processFrame(buffer);
      buffer = "";
    }
  };

  return {
    processChunk,
    flush,
    processFrame,
  };
}
