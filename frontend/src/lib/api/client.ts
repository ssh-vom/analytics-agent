import type {
  EventsResponse,
  SseDoneFrame,
  SseEventFrame,
  ThreadCreateResponse,
  TimelineEvent,
  WorldlineBranchResponse,
  WorldlineCreateResponse,
  WorldlinesResponse,
} from "$lib/types";

const JSON_HEADERS = {
  "Content-Type": "application/json",
};

interface StreamOptions {
  worldlineId: string;
  message: string;
  provider?: string;
  model?: string;
  maxIterations?: number;
  onEvent: (frame: SseEventFrame) => void;
  onDone?: (frame: SseDoneFrame) => void;
  onError?: (error: string) => void;
}

export async function createThread(title?: string): Promise<ThreadCreateResponse> {
  const body: { title?: string } = {};
  if (title) {
    body.title = title;
  }
  
  const response = await fetch("/api/threads", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`Failed to create thread (${response.status}): ${errorText}`);
  }
  return (await response.json()) as ThreadCreateResponse;
}

export async function createWorldline(
  threadId: string,
  name = "main",
): Promise<WorldlineCreateResponse> {
  const response = await fetch("/api/worldlines", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ thread_id: threadId, name }),
  });
  if (!response.ok) {
    throw new Error(`Failed to create worldline (${response.status})`);
  }
  return (await response.json()) as WorldlineCreateResponse;
}

export async function fetchThreadWorldlines(
  threadId: string,
): Promise<WorldlinesResponse> {
  const response = await fetch(`/api/threads/${threadId}/worldlines`);
  if (!response.ok) {
    throw new Error(`Failed to fetch worldlines (${response.status})`);
  }
  return (await response.json()) as WorldlinesResponse;
}

export async function fetchWorldlineEvents(
  worldlineId: string,
): Promise<TimelineEvent[]> {
  const response = await fetch(`/api/worldlines/${worldlineId}/events?limit=500`);
  if (!response.ok) {
    throw new Error(`Failed to fetch worldline events (${response.status})`);
  }
  const body = (await response.json()) as EventsResponse;
  return body.events;
}

export async function branchWorldline(
  sourceWorldlineId: string,
  fromEventId: string,
  name?: string,
): Promise<WorldlineBranchResponse> {
  const response = await fetch(`/api/worldlines/${sourceWorldlineId}/branch`, {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ from_event_id: fromEventId, name }),
  });
  if (!response.ok) {
    throw new Error(`Failed to branch worldline (${response.status})`);
  }
  return (await response.json()) as WorldlineBranchResponse;
}

export async function streamChatTurn(options: StreamOptions): Promise<void> {
  const response = await fetch("/api/chat/stream", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({
      worldline_id: options.worldlineId,
      message: options.message,
      provider: options.provider,
      model: options.model,
      max_iterations: options.maxIterations ?? 6,
    }),
  });

  if (!response.ok || !response.body) {
    throw new Error(`Failed to stream chat (${response.status})`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }

    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";

    for (const frame of frames) {
      let eventName = "event";
      let dataLine = "";
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) {
          eventName = line.slice(6).trim();
          continue;
        }
        if (line.startsWith("data:")) {
          dataLine = line.slice(5).trim();
        }
      }
      if (!dataLine) {
        continue;
      }

      const parsed = JSON.parse(dataLine) as Record<string, unknown>;
      if (eventName === "event") {
        options.onEvent(parsed as unknown as SseEventFrame);
        continue;
      }
      if (eventName === "done") {
        options.onDone?.(parsed as unknown as SseDoneFrame);
        continue;
      }
      if (eventName === "error") {
        const error =
          typeof parsed.error === "string"
            ? parsed.error
            : "Unknown stream error";
        options.onError?.(error);
      }
    }
  }
}
