export type EventType =
  | "user_message"
  | "assistant_plan"
  | "assistant_message"
  | "tool_call_sql"
  | "tool_result_sql"
  | "tool_call_python"
  | "tool_result_python"
  | "tool_call_subagents"
  | "tool_result_subagents"
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

export interface WorldlineSummaryItem extends WorldlineItem {
  message_count: number;
  last_event_at: string | null;
  last_activity: string;
  jobs: {
    queued: number;
    running: number;
    completed: number;
    failed: number;
    cancelled: number;
    latest_status: ChatJobStatus | null;
  };
}

export interface WorldlineSummaryResponse {
  worldlines: WorldlineSummaryItem[];
  next_cursor: string | null;
}

export interface ChatSessionResponse {
  thread: ThreadListItem;
  worldlines: WorldlineSummaryItem[];
  jobs: ChatJob[];
  preferred_worldline_id: string | null;
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

export type StreamDeltaType =
  | "assistant_text"
  | "tool_call_sql"
  | "tool_call_python"
  | "tool_call_subagents"
  | "subagent_progress"
  | "state_transition";

export interface StreamDeltaPayload {
  type: StreamDeltaType;
  call_id?: string;
  delta?: string;
  done?: boolean;
  skipped?: boolean;
  reason?: string;
  error?: string;
  from_state?: string | null;
  to_state?: string;
  fanout_group_id?: string;
  parent_tool_call_id?: string;
  source_worldline_id?: string;
  from_event_id?: string;
  task_index?: number;
  task_label?: string;
  task_status?: "queued" | "running" | "completed" | "failed" | "timeout";
  phase?: "queued" | "started" | "retrying" | "finished";
  retry_count?: number;
  failure_code?: string | null;
  recovered?: boolean;
  terminal_reason?: string | null;
  group_seq?: number;
  ordering_key?: string;
  task_count?: number;
  max_subagents?: number;
  max_parallel_subagents?: number;
  queued_count?: number;
  running_count?: number;
  completed_count?: number;
  failed_count?: number;
  timed_out_count?: number;
  child_worldline_id?: string;
  result_worldline_id?: string;
  assistant_preview?: string;
  queue_reason?: string | null;
}

export interface SseDeltaFrame {
  seq: number;
  worldline_id: string;
  delta: StreamDeltaPayload;
}

export interface SseDoneFrame {
  seq: number;
  worldline_id: string;
  done: true;
}

export type ChatJobStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export interface ChatJob {
  id: string;
  thread_id: string;
  worldline_id: string;
  parent_job_id?: string | null;
  fanout_group_id?: string | null;
  task_label?: string | null;
  parent_tool_call_id?: string | null;
  status: ChatJobStatus;
  error: string | null;
  request: {
    message?: string;
    provider?: string | null;
    model?: string | null;
    max_iterations?: number;
  };
  result_worldline_id: string | null;
  result_summary: {
    event_count?: number;
    assistant_preview?: string;
  } | null;
  seen_at: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  queue_position?: number;
}

export interface ChatJobsResponse {
  jobs: ChatJob[];
  count: number;
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

export interface ArtifactTablePreview {
  format: "table";
  columns: string[];
  rows: string[][];
  row_count: number;
  preview_count: number;
  truncated: boolean;
}

export interface ArtifactPreviewResponse {
  artifact_id: string;
  name: string;
  type: string;
  preview: ArtifactTablePreview;
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
