import type {
  StreamDeltaPayload,
  TimelineEvent,
} from "$lib/types";
import { sanitizeCodeArtifacts } from "$lib/codeSanitizer";

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
}

export interface SubagentProgressSnapshot {
  task_count: number;
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

const FALLBACK_DRAFT_ID: Record<"sql" | "python" | "subagents", string> = {
  sql: "sql-draft",
  python: "python-draft",
  subagents: "subagents-draft",
};

export function createStreamingState(): StreamingState {
  return {
    text: "",
    textCreatedAt: "",
    toolCalls: new Map(),
  };
}

function mostRecentDraftIdByKind(
  toolCalls: StreamingState["toolCalls"],
  kind: "sql" | "python" | "subagents"
): string | null {
  const calls = [...toolCalls.entries()].filter(([, v]) => v.type === kind);
  if (calls.length === 0) {
    return null;
  }
  const mostRecent = calls.sort(
    (a, b) =>
      new Date(b[1].createdAt).getTime() - new Date(a[1].createdAt).getTime()
  )[0];
  return mostRecent[0];
}

function resolveDeltaDraftId(
  state: StreamingState,
  kind: "sql" | "python" | "subagents",
  callId: string | undefined
): string {
  const normalized = (callId ?? "").trim();
  if (normalized) {
    return normalized;
  }
  const fallbackId = FALLBACK_DRAFT_ID[kind];
  if (state.toolCalls.has(fallbackId)) {
    return fallbackId;
  }
  // Reuse an existing in-flight draft of this kind to avoid duplicates.
  const existingKindDraft = mostRecentDraftIdByKind(state.toolCalls, kind);
  if (existingKindDraft) {
    return existingKindDraft;
  }
  return fallbackId;
}

function resolveDeleteDraftId(
  state: StreamingState,
  kind: "sql" | "python" | "subagents",
  callIdFromEvent: unknown
): string | null {
  if (typeof callIdFromEvent === "string" && callIdFromEvent.trim()) {
    const key = callIdFromEvent.trim();
    if (state.toolCalls.has(key)) {
      return key;
    }
  }
  // call_id missing OR call_id mismatched with draft key -> fallback by kind.
  return mostRecentDraftIdByKind(state.toolCalls, kind);
}

function normalizeSubagentStatus(
  status: unknown
): "queued" | "running" | "completed" | "failed" | "timeout" {
  if (
    status === "queued" ||
    status === "running" ||
    status === "completed" ||
    status === "failed" ||
    status === "timeout"
  ) {
    return status;
  }
  return "queued";
}

function mergeSubagentProgress(
  previous: SubagentProgressSnapshot | undefined,
  delta: StreamDeltaPayload
): SubagentProgressSnapshot {
  const previousTasks = previous?.tasks ?? [];
  const tasksByIndex = new Map<number, SubagentProgressTask>();
  for (const task of previousTasks) {
    tasksByIndex.set(task.task_index, task);
  }

  const taskIndex =
    typeof delta.task_index === "number" && Number.isFinite(delta.task_index)
      ? Math.max(0, Math.floor(delta.task_index))
      : null;
  if (taskIndex !== null) {
    const prior = tasksByIndex.get(taskIndex);
    tasksByIndex.set(taskIndex, {
      task_index: taskIndex,
      task_label:
        typeof delta.task_label === "string" && delta.task_label.trim()
          ? delta.task_label
          : prior?.task_label ?? `task-${taskIndex + 1}`,
      status: normalizeSubagentStatus(delta.task_status ?? prior?.status),
      child_worldline_id:
        typeof delta.child_worldline_id === "string"
          ? delta.child_worldline_id
          : prior?.child_worldline_id ?? "",
      result_worldline_id:
        typeof delta.result_worldline_id === "string"
          ? delta.result_worldline_id
          : prior?.result_worldline_id ?? "",
      assistant_preview:
        typeof delta.assistant_preview === "string"
          ? delta.assistant_preview
          : prior?.assistant_preview ?? "",
      error:
        typeof delta.error === "string" ? delta.error : prior?.error ?? "",
    });
  }

  const tasks = [...tasksByIndex.values()].sort(
    (a, b) => a.task_index - b.task_index
  );
  const taskCountFromDelta =
    typeof delta.task_count === "number" ? delta.task_count : undefined;
  const task_count = Math.max(
    taskCountFromDelta ?? previous?.task_count ?? 0,
    tasks.length
  );
  const completed_count =
    typeof delta.completed_count === "number"
      ? delta.completed_count
      : previous?.completed_count;
  const failed_count =
    typeof delta.failed_count === "number"
      ? delta.failed_count
      : previous?.failed_count;
  const timed_out_count =
    typeof delta.timed_out_count === "number"
      ? delta.timed_out_count
      : previous?.timed_out_count;
  const partial_failure =
    (failed_count ?? 0) > 0 || (timed_out_count ?? 0) > 0;

  return {
    task_count,
    max_subagents:
      typeof delta.max_subagents === "number"
        ? delta.max_subagents
        : previous?.max_subagents,
    max_parallel_subagents:
      typeof delta.max_parallel_subagents === "number"
        ? delta.max_parallel_subagents
        : previous?.max_parallel_subagents,
    queued_count:
      typeof delta.queued_count === "number"
        ? delta.queued_count
        : previous?.queued_count,
    running_count:
      typeof delta.running_count === "number"
        ? delta.running_count
        : previous?.running_count,
    completed_count,
    failed_count,
    timed_out_count,
    partial_failure,
    tasks,
  };
}

function summarizeSubagentProgressCode(
  rawArgs: string,
  progress: SubagentProgressSnapshot
): string {
  const done =
    (progress.completed_count ?? 0) +
    (progress.failed_count ?? 0) +
    (progress.timed_out_count ?? 0);
  if (progress.task_count > 0) {
    return `tasks: ${done}/${progress.task_count} complete`;
  }
  return extractCodeFromArgs(rawArgs, "subagents");
}

function resolveSubagentProgressCallId(
  state: StreamingState,
  delta: StreamDeltaPayload
): string {
  const parentCallId =
    typeof delta.parent_tool_call_id === "string"
      ? delta.parent_tool_call_id
      : undefined;
  const deltaCallId =
    typeof delta.call_id === "string" ? delta.call_id : undefined;
  return resolveDeltaDraftId(
    state,
    "subagents",
    deltaCallId ?? parentCallId
  );
}

function extractCodeFromArgs(
  rawArgs: string,
  kind: "sql" | "python" | "subagents"
): string {
  if (kind === "subagents") {
    try {
      const parsed = JSON.parse(rawArgs);
      if (typeof parsed === "object" && parsed !== null) {
        const maybeTasks = (parsed as { tasks?: unknown }).tasks;
        const taskCount = Array.isArray(maybeTasks) ? maybeTasks.length : 0;
        if (taskCount > 0) {
          return `tasks: ${taskCount}`;
        }
        const goal = (parsed as { goal?: unknown }).goal;
        if (typeof goal === "string" && goal.trim()) {
          return `goal: ${goal.trim()}`;
        }
      }
    } catch {
      // Keep a stable placeholder while args stream in.
    }
    return "preparing subagent fan-out";
  }
  const codeField = kind === "sql" ? "sql" : "code";
  try {
    const parsed = JSON.parse(rawArgs);
    if (
      typeof parsed === "object" &&
      parsed !== null &&
      typeof parsed[codeField] === "string"
    ) {
      return sanitizeCodeArtifacts(parsed[codeField]);
    }
  } catch {
    // JSON incomplete; try regex for partial display
  }
  const pattern = new RegExp(
    `"${codeField}"\\s*:\\s*"((?:[^"\\\\]|\\\\.)*)`,
    "s"
  );
  const match = rawArgs.match(pattern);
  if (match) {
    try {
      return sanitizeCodeArtifacts(JSON.parse(`"${match[1]}"`));
    } catch {
      return sanitizeCodeArtifacts(
        match[1]
        .replace(/\\n/g, "\n")
        .replace(/\\t/g, "\t")
        .replace(/\\"/g, '"')
        .replace(/\\\\/g, "\\")
      );
    }
  }
  return "";
}

/**
 * Apply a delta to streaming state. Returns a new state (immutable for Svelte reactivity).
 */
export function applyDelta(
  state: StreamingState,
  delta: StreamDeltaPayload
): StreamingState {
  if (delta.type === "assistant_text") {
    if (delta.done) {
      return { ...state, text: "", textCreatedAt: "" };
    }
    if (typeof delta.delta === "string" && delta.delta.length > 0) {
      const textCreatedAt = state.text ? state.textCreatedAt : new Date().toISOString();
      return {
        ...state,
        text: state.text + delta.delta,
        textCreatedAt,
      };
    }
    return state;
  }

  if (delta.type === "subagent_progress") {
    const callId = resolveSubagentProgressCallId(state, delta);
    const next = new Map(state.toolCalls);
    const existing = next.get(callId);
    const progress = mergeSubagentProgress(existing?.subagentProgress, delta);
    const rawArgs = existing?.rawArgs ?? "";
    next.set(callId, {
      type: "subagents",
      rawArgs,
      code: summarizeSubagentProgressCode(rawArgs, progress),
      createdAt: existing?.createdAt ?? new Date().toISOString(),
      skipped: existing?.skipped,
      skipReason: existing?.skipReason,
      subagentProgress: progress,
    });
    return { ...state, toolCalls: next };
  }
  return state;
}

/**
 * When a persisted event arrives, clear the matching streaming placeholder.
 * Returns a new state (immutable for Svelte reactivity).
 */
export function clearFromEvent(
  state: StreamingState,
  event: TimelineEvent
): StreamingState {
  if (event.type === "assistant_message" || event.type === "assistant_plan") {
    return {
      ...state,
      text: "",
      textCreatedAt: "",
      toolCalls: new Map(),
    };
  }
  if (event.type === "tool_call_sql") {
    const toDelete = resolveDeleteDraftId(state, "sql", event.payload?.call_id);
    if (toDelete) {
      const next = new Map(state.toolCalls);
      next.delete(toDelete);
      return { ...state, toolCalls: next };
    }
    return state;
  }
  if (event.type === "tool_call_python") {
    const toDelete = resolveDeleteDraftId(state, "python", event.payload?.call_id);
    if (toDelete) {
      const next = new Map(state.toolCalls);
      next.delete(toDelete);
      return { ...state, toolCalls: next };
    }
    return state;
  }
  if (event.type === "tool_call_subagents") {
    const callIdFromEvent =
      typeof event.payload?.call_id === "string" ? event.payload.call_id : null;
    if (!callIdFromEvent) {
      return state;
    }
    const existing = state.toolCalls.get(callIdFromEvent);
    if (existing) {
      return state;
    }
    const fallbackId = FALLBACK_DRAFT_ID.subagents;
    const fallback = state.toolCalls.get(fallbackId);
    if (!fallback || fallback.type !== "subagents") {
      return state;
    }
    const next = new Map(state.toolCalls);
    next.delete(fallbackId);
    next.set(callIdFromEvent, {
      ...fallback,
      code:
        fallback.subagentProgress !== undefined
          ? summarizeSubagentProgressCode(fallback.rawArgs, fallback.subagentProgress)
          : fallback.code,
    });
    return { ...state, toolCalls: next };
  }
  if (event.type === "tool_result_subagents") {
    const parentToolCallId =
      typeof event.payload?.parent_tool_call_id === "string"
        ? event.payload.parent_tool_call_id
        : null;
    const toDelete = resolveDeleteDraftId(state, "subagents", parentToolCallId);
    if (toDelete) {
      const next = new Map(state.toolCalls);
      next.delete(toDelete);
      return { ...state, toolCalls: next };
    }
    return state;
  }
  return state;
}

/**
 * Build an ordered list of display items: events first, then streaming placeholders.
 */
export function buildDisplayItems(
  events: TimelineEvent[],
  state: StreamingState
): DisplayItem[] {
  const items: DisplayItem[] = events.map((e) => ({ kind: "event" as const, event: e }));
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
  item: Extract<DisplayItem, { kind: "streaming_tool" }>
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
