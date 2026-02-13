import type { TimelineEvent } from "$lib/types";

/** A display item is either a persisted event or a streaming placeholder. */
export type DisplayItem =
  | { kind: "event"; event: TimelineEvent }
  | { kind: "streaming_text"; text: string; createdAt: string }
  | {
      kind: "streaming_tool";
      callId: string;
      type: "sql" | "python" | "subagents";
      code: string;
      rawArgs: string;
      createdAt: string;
      skipped?: boolean;
      skipReason?: string;
      subagentProgress?: SubagentProgressSnapshot;
    };

export interface SubagentProgressTask {
  task_index: number;
  task_label: string;
  status: "queued" | "running" | "completed" | "failed" | "timeout";
  child_worldline_id: string;
  result_worldline_id: string;
  assistant_preview: string;
  error: string;
  retry_count?: number;
  failure_code?: string | null;
  recovered?: boolean;
  terminal_reason?: string | null;
}

export interface SubagentProgressSnapshot {
  task_count: number;
  phase?: "queued" | "started" | "retrying" | "finished";
  retry_count?: number;
  failure_code?: string | null;
  max_subagents?: number;
  max_parallel_subagents?: number;
  queued_count?: number;
  running_count?: number;
  completed_count?: number;
  failed_count?: number;
  timed_out_count?: number;
  partial_failure?: boolean;
  tasks: SubagentProgressTask[];
}

export interface StreamingState {
  text: string;
  textCreatedAt: string;
  toolCalls: Map<
    string,
    {
      type: "sql" | "python" | "subagents";
      code: string;
      rawArgs: string;
      createdAt: string;
      skipped?: boolean;
      skipReason?: string;
      subagentProgress?: SubagentProgressSnapshot;
    }
  >;
}

export function createStreamingState(): StreamingState {
  return {
    text: "",
    textCreatedAt: "",
    toolCalls: new Map(),
  };
}

/**
 * Build an ordered list of display items: events first, then streaming placeholders.
 */
export function buildDisplayItems(
  events: TimelineEvent[],
  state: StreamingState,
): DisplayItem[] {
  const items: DisplayItem[] = events.map((event) => ({
    kind: "event",
    event,
  }));
  if (state.text) {
    items.push({
      kind: "streaming_text",
      text: state.text,
      createdAt: state.textCreatedAt,
    });
  }
  for (const [callId, data] of state.toolCalls.entries()) {
    items.push({
      kind: "streaming_tool",
      callId,
      type: data.type,
      code: data.code,
      rawArgs: data.rawArgs,
      createdAt: data.createdAt,
      skipped: data.skipped,
      skipReason: data.skipReason,
      subagentProgress: data.subagentProgress,
    });
  }
  return items;
}

/**
 * Create a synthetic TimelineEvent for a streaming tool call (for SqlCell/PythonCell).
 */
export function streamingToolToEvent(
  item: Extract<DisplayItem, { kind: "streaming_tool" }>,
): TimelineEvent {
  const payload: Record<string, unknown> =
    item.type === "sql"
      ? { sql: item.code, limit: 100, call_id: item.callId }
      : item.type === "python"
        ? { code: item.code, timeout: 30, call_id: item.callId }
        : {
            goal: item.code,
            call_id: item.callId,
            ...(item.subagentProgress
              ? {
                  _streaming: true,
                  phase: item.subagentProgress.phase,
                  retry_count: item.subagentProgress.retry_count,
                  failure_code: item.subagentProgress.failure_code,
                  task_count: item.subagentProgress.task_count,
                  max_subagents: item.subagentProgress.max_subagents,
                  max_parallel_subagents:
                    item.subagentProgress.max_parallel_subagents,
                  queued_count: item.subagentProgress.queued_count,
                  running_count: item.subagentProgress.running_count,
                  completed_count: item.subagentProgress.completed_count,
                  failed_count: item.subagentProgress.failed_count,
                  timed_out_count: item.subagentProgress.timed_out_count,
                  partial_failure: item.subagentProgress.partial_failure,
                  tasks: item.subagentProgress.tasks,
                }
              : {}),
          };

  if (item.skipped) {
    payload.skipped = true;
    payload.skip_reason = item.skipReason;
  }

  return {
    id: `draft-${item.type}-${item.callId}`,
    parent_event_id: null,
    type:
      item.type === "sql"
        ? "tool_call_sql"
        : item.type === "python"
          ? "tool_call_python"
          : "tool_call_subagents",
    payload,
    created_at: item.createdAt,
  };
}
