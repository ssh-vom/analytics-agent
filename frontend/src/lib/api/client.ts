import type {
  EventsResponse,
  SseDeltaFrame,
  SseDoneFrame,
  SseEventFrame,
  ThreadCreateResponse,
  ThreadsResponse,
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
  onDelta?: (frame: SseDeltaFrame) => void;
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

export async function fetchThreads(): Promise<ThreadsResponse> {
  const response = await fetch("/api/threads");
  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`Failed to fetch threads (${response.status}): ${errorText}`);
  }
  return (await response.json()) as ThreadsResponse;
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

// Seed data API functions
export async function importCSV(
  worldlineId: string,
  file: File,
  tableName?: string,
  ifExists: "fail" | "replace" | "append" = "fail",
): Promise<{
  success: boolean;
  table_name: string;
  row_count: number;
  columns: { name: string; type: string }[];
  import_time_ms: number;
  event_id: string;
}> {
  const formData = new FormData();
  formData.append("file", file);
  if (tableName) {
    formData.append("table_name", tableName);
  }
  formData.append("if_exists", ifExists);

  const response = await fetch(`/api/seed-data/worldlines/${worldlineId}/import-csv`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`Failed to import CSV (${response.status}): ${errorText}`);
  }

  return await response.json();
}

export async function attachExternalDuckDB(
  worldlineId: string,
  dbPath: string,
  alias?: string,
): Promise<{
  success: boolean;
  alias: string;
  db_path: string;
  attached_at: string;
  event_id: string;
}> {
  const response = await fetch(`/api/seed-data/worldlines/${worldlineId}/attach-duckdb`, {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ db_path: dbPath, alias }),
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`Failed to attach database (${response.status}): ${errorText}`);
  }

  return await response.json();
}

export async function detachExternalDuckDB(
  worldlineId: string,
  alias: string,
): Promise<{
  success: boolean;
  alias: string;
  status: string;
  event_id: string;
}> {
  const response = await fetch(`/api/seed-data/worldlines/${worldlineId}/detach-duckdb`, {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ alias }),
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`Failed to detach database (${response.status}): ${errorText}`);
  }

  return await response.json();
}

export async function fetchWorldlineSchema(worldlineId: string): Promise<{
  native_tables: {
    schema: string;
    name: string;
    columns: { name: string; type: string }[];
  }[];
  imported_tables: {
    table_name: string;
    source_filename: string;
    row_count: number;
    imported_at: string;
  }[];
  attached_databases: {
    alias: string;
    db_path: string;
    db_type: string;
    attached_at: string;
    tables: string[];
  }[];
}> {
  const response = await fetch(`/api/seed-data/worldlines/${worldlineId}/schema`);
  if (!response.ok) {
    throw new Error(`Failed to fetch schema (${response.status})`);
  }
  return await response.json();
}

export async function fetchWorldlineTables(
  worldlineId: string,
  includeSystem = false,
): Promise<{
  tables: {
    name: string;
    schema: string;
    type: "native" | "imported_csv" | "external";
    columns?: { name: string; type: string }[];
    source_filename?: string;
    row_count?: number;
    source_db?: string;
  }[];
  count: number;
}> {
  const url = new URL(`/api/seed-data/worldlines/${worldlineId}/tables`, window.location.origin);
  if (includeSystem) {
    url.searchParams.set("include_system", "true");
  }

  const response = await fetch(url.toString());
  if (!response.ok) {
    throw new Error(`Failed to fetch tables (${response.status})`);
  }
  return await response.json();
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
      max_iterations: options.maxIterations ?? 20,
    }),
  });

  if (!response.ok || !response.body) {
    throw new Error(`Failed to stream chat (${response.status})`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

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
      options.onError?.("Failed to parse stream frame");
      return;
    }
    if (eventName === "event") {
      options.onEvent(parsed as unknown as SseEventFrame);
      return;
    }
    if (eventName === "delta") {
      options.onDelta?.(parsed as unknown as SseDeltaFrame);
      return;
    }
    if (eventName === "done") {
      options.onDone?.(parsed as unknown as SseDoneFrame);
      return;
    }
    if (eventName === "error") {
      const error =
        typeof parsed.error === "string"
          ? parsed.error
          : "Unknown stream error";
      options.onError?.(error);
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }

    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";

    for (const frame of frames) {
      processFrame(frame);
    }
  }

  if (buffer.trim()) {
    processFrame(buffer);
  }
}
