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
import { createStreamProcessor } from "./streamParser";

const JSON_HEADERS = {
  "Content-Type": "application/json",
};

async function buildRequestError(
  response: Response,
  message: string,
  includeBody = false,
): Promise<Error> {
  if (!includeBody) {
    return new Error(`${message} (${response.status})`);
  }
  const errorText = await response.text();
  return new Error(`${message} (${response.status}): ${errorText}`);
}

async function requestJson<T>(
  input: RequestInfo | URL,
  init: RequestInit | undefined,
  errorMessage: string,
  includeErrorBody = false,
): Promise<T> {
  const response = await fetch(input, init);
  if (!response.ok) {
    throw await buildRequestError(response, errorMessage, includeErrorBody);
  }
  return (await response.json()) as T;
}

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

  return requestJson<ThreadCreateResponse>(
    "/api/threads",
    {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(body),
    },
    "Failed to create thread",
    true,
  );
}

export async function fetchThreads(): Promise<ThreadsResponse> {
  return requestJson<ThreadsResponse>(
    "/api/threads",
    undefined,
    "Failed to fetch threads",
    true,
  );
}

export async function createWorldline(
  threadId: string,
  name = "main",
): Promise<WorldlineCreateResponse> {
  return requestJson<WorldlineCreateResponse>(
    "/api/worldlines",
    {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ thread_id: threadId, name }),
    },
    "Failed to create worldline",
  );
}

export async function fetchThreadWorldlines(
  threadId: string,
): Promise<WorldlinesResponse> {
  return requestJson<WorldlinesResponse>(
    `/api/threads/${threadId}/worldlines`,
    undefined,
    "Failed to fetch worldlines",
  );
}

export async function fetchWorldlineEvents(
  worldlineId: string,
): Promise<TimelineEvent[]> {
  const body = await requestJson<EventsResponse>(
    `/api/worldlines/${worldlineId}/events?limit=500`,
    undefined,
    "Failed to fetch worldline events",
  );
  return body.events;
}

export async function branchWorldline(
  sourceWorldlineId: string,
  fromEventId: string,
  name?: string,
): Promise<WorldlineBranchResponse> {
  return requestJson<WorldlineBranchResponse>(
    `/api/worldlines/${sourceWorldlineId}/branch`,
    {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ from_event_id: fromEventId, name }),
    },
    "Failed to branch worldline",
  );
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

  return requestJson<{
    success: boolean;
    table_name: string;
    row_count: number;
    columns: { name: string; type: string }[];
    import_time_ms: number;
    event_id: string;
  }>(
    `/api/seed-data/worldlines/${worldlineId}/import-csv`,
    {
      method: "POST",
      body: formData,
    },
    "Failed to import CSV",
    true,
  );
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
  return requestJson<{
    success: boolean;
    alias: string;
    db_path: string;
    attached_at: string;
    event_id: string;
  }>(
    `/api/seed-data/worldlines/${worldlineId}/attach-duckdb`,
    {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ db_path: dbPath, alias }),
    },
    "Failed to attach database",
    true,
  );
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
  return requestJson<{
    success: boolean;
    alias: string;
    status: string;
    event_id: string;
  }>(
    `/api/seed-data/worldlines/${worldlineId}/detach-duckdb`,
    {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ alias }),
    },
    "Failed to detach database",
    true,
  );
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
  return requestJson<{
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
  }>(
    `/api/seed-data/worldlines/${worldlineId}/schema`,
    undefined,
    "Failed to fetch schema",
  );
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

  return requestJson<{
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
  }>(url.toString(), undefined, "Failed to fetch tables");
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
  const processor = createStreamProcessor({
    onEvent: options.onEvent,
    onDelta: options.onDelta,
    onDone: options.onDone,
    onError: options.onError,
  });

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    processor.processChunk(value);
  }

  processor.flush();
}
