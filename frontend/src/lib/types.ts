export type EventType =
  | "user_message"
  | "assistant_plan"
  | "assistant_message"
  | "tool_call_sql"
  | "tool_result_sql"
  | "tool_call_python"
  | "tool_result_python"
  | "time_travel"
  | "worldline_created";

export interface TimelineEvent {
  id: string;
  parent_event_id: string | null;
  type: EventType;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface ThreadCreateResponse {
  thread_id: string;
}

export interface ThreadListItem {
  id: string;
  title: string;
  created_at: string;
  message_count: number;
  last_activity: string;
}

export interface ThreadsResponse {
  threads: ThreadListItem[];
  next_cursor: string | null;
}

export interface WorldlineCreateResponse {
  worldline_id: string;
}

export interface WorldlineBranchResponse {
  new_worldline_id: string;
}

export interface WorldlineItem {
  id: string;
  parent_worldline_id: string | null;
  forked_from_event_id: string | null;
  head_event_id: string | null;
  name: string;
  created_at: string;
}

export interface WorldlinesResponse {
  worldlines: WorldlineItem[];
  next_cursor: string | null;
}

export interface EventsResponse {
  events: TimelineEvent[];
  next_cursor: string | null;
}

export interface SseEventFrame {
  seq: number;
  worldline_id: string;
  event: TimelineEvent;
}

export interface SseDoneFrame {
  seq: number;
  worldline_id: string;
  done: true;
}

export interface SqlResultPayload {
  columns?: Array<{ name: string; type: string }>;
  rows?: unknown[][];
  row_count?: number;
  preview_count?: number;
  execution_ms?: number;
  error?: string;
}

export interface PythonArtifact {
  type: string;
  name: string;
  artifact_id: string;
}

export interface PythonResultPayload {
  stdout?: string;
  stderr?: string;
  error?: string | null;
  artifacts?: PythonArtifact[];
  previews?: {
    dataframes?: Array<{
      name: string;
      columns: string[];
      rows: unknown[][];
    }>;
  };
  execution_ms?: number;
}

export interface Thread {
  id: string;
  name: string;
  createdAt: string;
  lastActivity: string;
  messageCount: number;
}

export interface Connector {
  id: string;
  name: string;
  type: "sqlite" | "postgres" | "duckdb" | "mysql";
  connectionString: string;
  isActive: boolean;
  lastConnected?: string;
}
