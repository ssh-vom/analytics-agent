import { get, writable } from "svelte/store";

import {
  branchWorldline,
  createChatJob,
  createWorldline,
  fetchChatSession,
  fetchThreadWorldlines,
  fetchWorldlineEvents,
  streamChatTurn,
} from "$lib/api/client";
import { sanitizeCodeArtifacts } from "$lib/codeSanitizer";
import {
  createOptimisticUserMessage,
  insertOptimisticEvent,
  removeOptimisticEvent,
  replaceOptimisticWithReal,
} from "$lib/chat/optimisticState";
import { computeWorldlineQueueStats } from "$lib/chat/queueStats";
import {
  appendRuntimeStateTransition,
  extractPersistedStateTrace,
  stateTransitionFromDelta,
  withRuntimeStateTrace,
  type RuntimeStateTransition,
} from "$lib/chat/stateTrace";
import { statusFromDelta, statusFromStreamEvent } from "$lib/chat/streamStatus";
import { chatJobs } from "$lib/stores/chatJobs";
import { createStreamingState, type StreamingState } from "$lib/streaming";
import type { ChatJob, StreamDeltaPayload, TimelineEvent, WorldlineItem } from "$lib/types";

export type SessionState = {
  threadId: string;
  activeWorldlineId: string;
  worldlines: WorldlineItem[];
  eventsByWorldline: Record<string, TimelineEvent[]>;
  streamingByWorldline: Record<string, StreamingState>;
  sendingByWorldline: Record<string, boolean>;
  stateTraceByWorldline: Record<string, RuntimeStateTransition[]>;
  statusText: string;
  selectedArtifactId: string | null;
  isReady: boolean;
  isHydratingThread: boolean;
};

export type SessionRuntimeCallbacks = {
  refreshContextTables: () => Promise<void>;
  scrollToBottom: (force?: boolean) => void;
  onTurnCompleted: () => void;
};

export type QueuePromptInput = {
  message: string;
  worldlineId?: string;
  provider: string;
  model?: string;
  maxIterations?: number;
  buildContextualMessage?: (message: string) => string;
  onAccepted?: () => void;
};

export type SendPromptInput = QueuePromptInput & {
  beforeSend?: (worldlineId: string) => Promise<void>;
  onStreamingStart?: () => void;
};

export type VisibleWorldlineHint = {
  parentWorldlineId?: string | null;
  suggestedName?: string | null;
  createdAt?: string | null;
};

export type WorldlineManagerContext = {
  threadId: string | null;
  getActiveWorldlineId: () => string;
  getWorldlines: () => WorldlineItem[];
  getEventsByWorldline: () => Record<string, TimelineEvent[]>;
  setWorldlines: (worldlines: WorldlineItem[]) => void;
  setActiveWorldlineId: (id: string) => void;
  persistPreferredWorldline: (id: string) => void;
  setWorldlineEvents: (worldlineId: string, events: TimelineEvent[]) => void;
  onStatusChange: (status: string) => void;
  onScroll: () => void;
  refreshContextTables: () => Promise<void>;
};

export type WorldlineManager = {
  refreshWorldlines: () => Promise<void>;
  loadWorldline: (worldlineId: string) => Promise<void>;
  selectWorldline: (worldlineId: string) => Promise<void>;
  branchFromEvent: (eventId: string) => Promise<string | null>;
  ensureWorldline: () => Promise<string | null>;
};

export type SendPromptOptions = {
  worldlineId: string;
  message: string;
  provider: string;
  model?: string;
  maxIterations?: number;
};

export type SendPromptContext = {
  optimisticId: string;
  getActiveWorldlineId: () => string;
  getEvents: (worldlineId: string) => TimelineEvent[];
  setWorldlineEvents: (worldlineId: string, events: TimelineEvent[]) => void;
  appendEvent: (worldlineId: string, event: TimelineEvent) => void;
  rollbackOptimisticMessage: (worldlineId: string, optimisticId: string) => void;
  setWorldlineSending: (worldlineId: string, sending: boolean) => void;
  resetStreamingDrafts: (worldlineId?: string) => void;
  refreshWorldlines: () => Promise<void>;
  ensureWorldlineVisible: (
    worldlineId: string,
    hint?: VisibleWorldlineHint,
  ) => void;
  onStatusChange: (status: string) => void;
  onScroll: () => void;
  onTurnCompleted: () => void;
  setStreamingState: (worldlineId: string, state: StreamingState) => void;
  getStreamingState: (worldlineId: string) => StreamingState | undefined;
  appendRuntimeStateTransition: (
    worldlineId: string,
    transition: RuntimeStateTransition,
  ) => void;
};

export type SendPromptWithStreaming = (
  options: SendPromptOptions,
  context: SendPromptContext,
) => Promise<void>;

export type SessionStoreDependencies = {
  fetchChatSession: typeof fetchChatSession;
  createChatJob: typeof createChatJob;
  sendPromptWithStreaming: SendPromptWithStreaming;
  createWorldlineManager: (context: WorldlineManagerContext) => WorldlineManager;
  hydrateChatJobs: (jobs: ChatJob[]) => void;
  registerQueuedJob: (job: ChatJob) => void;
  pollChatJobs: () => Promise<void>;
  getChatJobsById: () => Record<string, ChatJob>;
};

const INITIAL_STATE: SessionState = {
  threadId: "",
  activeWorldlineId: "",
  worldlines: [],
  eventsByWorldline: {},
  streamingByWorldline: {},
  sendingByWorldline: {},
  stateTraceByWorldline: {},
  statusText: "Initializing...",
  selectedArtifactId: null,
  isReady: false,
  isHydratingThread: false,
};

const NOOP_ASYNC = async (): Promise<void> => undefined;
const NOOP_SCROLL = (): void => undefined;
const NOOP_TURN_COMPLETED = (): void => undefined;

type StreamingToolKind = "sql" | "python" | "subagents";
type StreamingToolCall = StreamingState["toolCalls"] extends Map<string, infer T>
  ? T
  : never;
type SubagentProgressSnapshot = Exclude<StreamingToolCall["subagentProgress"], undefined>;
type SubagentProgressTask = SubagentProgressSnapshot["tasks"][number];

const FALLBACK_DRAFT_ID: Record<StreamingToolKind, string> = {
  sql: "sql-draft",
  python: "python-draft",
  subagents: "subagents-draft",
};

function persistPreferredWorldline(worldlineId: string): void {
  if (!worldlineId || typeof localStorage === "undefined") {
    return;
  }
  localStorage.setItem("textql_active_worldline", worldlineId);
}

function jobsByIdFromList(jobs: ChatJob[]): Record<string, ChatJob> {
  return jobs.reduce<Record<string, ChatJob>>((acc, job) => {
    acc[job.id] = job;
    return acc;
  }, {});
}

function dedupePreserveOrder(events: TimelineEvent[]): TimelineEvent[] {
  const seenIds = new Set<string>();
  const output: TimelineEvent[] = [];

  for (const event of events) {
    if (seenIds.has(event.id)) {
      continue;
    }
    seenIds.add(event.id);
    output.push(event);
  }

  return output;
}

function withWorldlineEvents(
  eventsByWorldline: Record<string, TimelineEvent[]>,
  worldlineId: string,
  events: TimelineEvent[],
): Record<string, TimelineEvent[]> {
  return {
    ...eventsByWorldline,
    [worldlineId]: dedupePreserveOrder(events),
  };
}

function withAppendedWorldlineEvent(
  eventsByWorldline: Record<string, TimelineEvent[]>,
  worldlineId: string,
  event: TimelineEvent,
): Record<string, TimelineEvent[]> {
  const existing = eventsByWorldline[worldlineId] ?? [];
  return withWorldlineEvents(eventsByWorldline, worldlineId, [...existing, event]);
}

function withVisibleWorldline(
  worldlines: WorldlineItem[],
  worldlineId: string,
  hint: VisibleWorldlineHint = {},
): WorldlineItem[] {
  const syntheticName = worldlineId.slice(0, 12);
  const worldlineIndex = worldlines.findIndex((line) => line.id === worldlineId);
  if (worldlineIndex >= 0) {
    const existing = worldlines[worldlineIndex];
    const isSynthetic =
      existing.name === syntheticName || existing.name === worldlineId;
    const nextParent =
      existing.parent_worldline_id ??
      (typeof hint.parentWorldlineId === "string" ? hint.parentWorldlineId : null);
    const shouldReplaceName =
      isSynthetic &&
      typeof hint.suggestedName === "string" &&
      hint.suggestedName.trim().length > 0;
    const nextName = shouldReplaceName ? hint.suggestedName.trim() : existing.name;
    const shouldReplaceCreatedAt =
      isSynthetic &&
      typeof hint.createdAt === "string" &&
      hint.createdAt.trim().length > 0;
    const nextCreatedAt = shouldReplaceCreatedAt
      ? hint.createdAt
      : existing.created_at;

    if (
      nextParent === existing.parent_worldline_id &&
      nextName === existing.name &&
      nextCreatedAt === existing.created_at
    ) {
      return worldlines;
    }

    return worldlines.map((line, index) =>
      index === worldlineIndex
        ? {
            ...line,
            parent_worldline_id: nextParent,
            name: nextName,
            created_at: nextCreatedAt,
          }
        : line,
    );
  }

  return [
    ...worldlines,
    {
      id: worldlineId,
      name:
        typeof hint.suggestedName === "string" && hint.suggestedName.trim()
          ? hint.suggestedName.trim()
          : syntheticName,
      parent_worldline_id:
        typeof hint.parentWorldlineId === "string" ? hint.parentWorldlineId : null,
      forked_from_event_id: null,
      head_event_id: null,
      created_at:
        typeof hint.createdAt === "string" && hint.createdAt.trim()
          ? hint.createdAt
          : new Date().toISOString(),
    },
  ];
}

function withStreamingState(
  statesByWorldline: Record<string, StreamingState>,
  worldlineId: string,
  streamState: StreamingState,
): Record<string, StreamingState> {
  return {
    ...statesByWorldline,
    [worldlineId]: streamState,
  };
}

function withoutStreamingState(
  statesByWorldline: Record<string, StreamingState>,
  worldlineId?: string,
): Record<string, StreamingState> {
  if (!worldlineId) {
    return {};
  }
  if (!(worldlineId in statesByWorldline)) {
    return statesByWorldline;
  }
  const next = { ...statesByWorldline };
  delete next[worldlineId];
  return next;
}

function withWorldlineSending(
  sendingByWorldline: Record<string, boolean>,
  worldlineId: string,
  isSending: boolean,
): Record<string, boolean> {
  if (!worldlineId) {
    return sendingByWorldline;
  }
  if (isSending) {
    return {
      ...sendingByWorldline,
      [worldlineId]: true,
    };
  }
  if (!(worldlineId in sendingByWorldline)) {
    return sendingByWorldline;
  }
  const next = { ...sendingByWorldline };
  delete next[worldlineId];
  return next;
}

function rollbackOptimisticWorldlineEvent(
  eventsByWorldline: Record<string, TimelineEvent[]>,
  worldlineId: string,
  optimisticId: string | null,
): Record<string, TimelineEvent[]> {
  const currentEvents = eventsByWorldline[worldlineId] ?? [];
  return {
    ...eventsByWorldline,
    [worldlineId]: removeOptimisticEvent(currentEvents, optimisticId),
  };
}

function mapSummaryWorldlineToItem(worldline: {
  id: string;
  parent_worldline_id: string | null;
  forked_from_event_id: string | null;
  head_event_id: string | null;
  name: string;
  created_at: string;
}): WorldlineItem {
  return {
    id: worldline.id,
    parent_worldline_id: worldline.parent_worldline_id,
    forked_from_event_id: worldline.forked_from_event_id,
    head_event_id: worldline.head_event_id,
    name: worldline.name,
    created_at: worldline.created_at,
  };
}

function createWorldlineManagerInternal(context: WorldlineManagerContext): WorldlineManager {
  const {
    getActiveWorldlineId,
    getWorldlines,
    getEventsByWorldline,
    setWorldlines,
    setActiveWorldlineId,
    persistPreferredWorldline,
    setWorldlineEvents,
    onStatusChange,
    onScroll,
    refreshContextTables,
  } = context;

  function getThreadId(): string | null {
    return context.threadId;
  }

  function mergeFetchedWithOptimistic(
    fetched: TimelineEvent[],
    existing: TimelineEvent[],
  ): TimelineEvent[] {
    if (existing.length === 0) {
      return fetched;
    }
    const fetchedIds = new Set(fetched.map((event) => event.id));
    const optimisticTail = existing.filter(
      (event) => event.id.startsWith("optimistic:") && !fetchedIds.has(event.id),
    );
    if (optimisticTail.length === 0) {
      return fetched;
    }
    return [...fetched, ...optimisticTail];
  }

  async function refreshWorldlines(): Promise<void> {
    const threadId = getThreadId();
    if (!threadId) {
      return;
    }
    const response = await fetchThreadWorldlines(threadId);
    setWorldlines(response.worldlines.map(mapSummaryWorldlineToItem));
  }

  async function loadWorldline(worldlineId: string): Promise<void> {
    const fetchedEvents = await fetchWorldlineEvents(worldlineId);
    const existingEvents = getEventsByWorldline()[worldlineId] ?? [];
    const mergedEvents = mergeFetchedWithOptimistic(fetchedEvents, existingEvents);
    setWorldlineEvents(worldlineId, mergedEvents);
    onScroll();
  }

  async function selectWorldline(worldlineId: string): Promise<void> {
    setActiveWorldlineId(worldlineId);
    persistPreferredWorldline(worldlineId);

    if (!getEventsByWorldline()[worldlineId]) {
      await loadWorldline(worldlineId);
    }
    await refreshContextTables();
    onScroll();
  }

  async function branchFromEvent(eventId: string): Promise<string | null> {
    const activeWorldlineId = getActiveWorldlineId();
    const worldlines = getWorldlines();
    if (!activeWorldlineId || !eventId) {
      return null;
    }

    try {
      onStatusChange("Branching worldline...");
      const response = await branchWorldline(
        activeWorldlineId,
        eventId,
        `branch-${worldlines.length + 1}`,
      );
      const newWorldlineId = response.new_worldline_id;
      setActiveWorldlineId(newWorldlineId);
      persistPreferredWorldline(newWorldlineId);
      await refreshWorldlines();
      await loadWorldline(newWorldlineId);
      await refreshContextTables();
      onStatusChange("Branch created");
      return newWorldlineId;
    } catch (error) {
      onStatusChange(error instanceof Error ? error.message : "Branch failed");
      return null;
    }
  }

  async function ensureWorldline(): Promise<string | null> {
    const activeWorldlineId = getActiveWorldlineId();
    if (activeWorldlineId) {
      return activeWorldlineId;
    }

    const threadId = getThreadId();
    if (threadId) {
      onStatusChange("Creating worldline...");
      try {
        const worldline = await createWorldline(threadId, "main");
        const newWorldlineId = worldline.worldline_id;
        setActiveWorldlineId(newWorldlineId);
        persistPreferredWorldline(newWorldlineId);
        await refreshWorldlines();
        onStatusChange("Ready");
        return newWorldlineId;
      } catch (error) {
        onStatusChange(
          error instanceof Error ? error.message : "Failed to create worldline",
        );
        return null;
      }
    }

    return null;
  }

  return {
    refreshWorldlines,
    loadWorldline,
    selectWorldline,
    branchFromEvent,
    ensureWorldline,
  };
}

function resolveHydratedStatus(worldlineId: string, jobs: ChatJob[]): string {
  const queueStats = computeWorldlineQueueStats(worldlineId, jobsByIdFromList(jobs));
  if (queueStats.running > 0) {
    return `Background job running (${queueStats.running})`;
  }
  if (queueStats.queued > 0) {
    return `Background jobs queued (${queueStats.queued})`;
  }
  return "Ready";
}

function mostRecentDraftIdByKind(
  toolCalls: StreamingState["toolCalls"],
  kind: StreamingToolKind,
): string | null {
  const calls = [...toolCalls.entries()].filter(([, value]) => value.type === kind);
  if (calls.length === 0) {
    return null;
  }
  const mostRecent = calls.sort(
    (left, right) =>
      new Date(right[1].createdAt).getTime() - new Date(left[1].createdAt).getTime(),
  )[0];
  return mostRecent[0];
}

function resolveDeltaDraftId(
  state: StreamingState,
  kind: StreamingToolKind,
  callId: string | undefined,
): string {
  const normalized = (callId ?? "").trim();
  if (normalized) {
    return normalized;
  }
  const fallbackId = FALLBACK_DRAFT_ID[kind];
  if (state.toolCalls.has(fallbackId)) {
    return fallbackId;
  }

  const existingKindDraft = mostRecentDraftIdByKind(state.toolCalls, kind);
  if (existingKindDraft) {
    return existingKindDraft;
  }
  return fallbackId;
}

function resolveDeleteDraftId(
  state: StreamingState,
  kind: StreamingToolKind,
  callIdFromEvent: unknown,
): string | null {
  if (typeof callIdFromEvent === "string" && callIdFromEvent.trim()) {
    const key = callIdFromEvent.trim();
    if (state.toolCalls.has(key)) {
      return key;
    }
  }
  return mostRecentDraftIdByKind(state.toolCalls, kind);
}

function normalizeSubagentStatus(
  status: unknown,
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
  delta: StreamDeltaPayload,
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
      error: typeof delta.error === "string" ? delta.error : prior?.error ?? "",
    });
  }

  const tasks = [...tasksByIndex.values()].sort((left, right) => left.task_index - right.task_index);
  const taskCountFromDelta =
    typeof delta.task_count === "number" ? delta.task_count : undefined;
  const task_count = Math.max(taskCountFromDelta ?? previous?.task_count ?? 0, tasks.length);
  const completed_count =
    typeof delta.completed_count === "number"
      ? delta.completed_count
      : previous?.completed_count;
  const failed_count =
    typeof delta.failed_count === "number" ? delta.failed_count : previous?.failed_count;
  const timed_out_count =
    typeof delta.timed_out_count === "number"
      ? delta.timed_out_count
      : previous?.timed_out_count;

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
    partial_failure: (failed_count ?? 0) > 0 || (timed_out_count ?? 0) > 0,
    tasks,
  };
}

function extractCodeFromArgs(rawArgs: string, kind: StreamingToolKind): string {
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
    // JSON may be partial while streaming.
  }

  const pattern = new RegExp(`"${codeField}"\\s*:\\s*"((?:[^"\\\\]|\\\\.)*)`, "s");
  const match = rawArgs.match(pattern);
  if (!match) {
    return "";
  }

  try {
    return sanitizeCodeArtifacts(JSON.parse(`"${match[1]}"`));
  } catch {
    return sanitizeCodeArtifacts(
      match[1]
        .replace(/\\n/g, "\n")
        .replace(/\\t/g, "\t")
        .replace(/\\"/g, '"')
        .replace(/\\\\/g, "\\"),
    );
  }
}

function summarizeSubagentProgressCode(
  rawArgs: string,
  progress: SubagentProgressSnapshot,
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
  delta: StreamDeltaPayload,
): string {
  const parentCallId =
    typeof delta.parent_tool_call_id === "string" ? delta.parent_tool_call_id : undefined;
  const deltaCallId = typeof delta.call_id === "string" ? delta.call_id : undefined;
  return resolveDeltaDraftId(state, "subagents", deltaCallId ?? parentCallId);
}

function applyStreamingDelta(
  state: StreamingState,
  delta: StreamDeltaPayload,
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

function clearStreamingFromEvent(
  state: StreamingState,
  event: TimelineEvent,
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

const sendPromptWithStreamingInternal: SendPromptWithStreaming = async (
  options,
  context,
): Promise<void> => {
  const {
    worldlineId,
    message,
    provider,
    model,
    maxIterations = 10,
  } = options;

  const {
    optimisticId,
    getActiveWorldlineId,
    getEvents,
    setWorldlineEvents,
    appendEvent,
    rollbackOptimisticMessage,
    setWorldlineSending,
    resetStreamingDrafts,
    refreshWorldlines,
    ensureWorldlineVisible,
    onStatusChange,
    onScroll,
    onTurnCompleted,
    setStreamingState,
    getStreamingState,
    appendRuntimeStateTransition,
  } = context;

  const fanoutOrderBaseByGroup = new Map<string, number>();
  const isActiveWorldline = (candidateWorldlineId: string): boolean =>
    candidateWorldlineId === getActiveWorldlineId();

  const ensureDeltaWorldlinesVisible = (
    ownerWorldlineId: string,
    delta: StreamDeltaPayload,
  ): void => {
    if (delta.type !== "subagent_progress") {
      return;
    }

    const taskIndex =
      typeof delta.task_index === "number" && Number.isFinite(delta.task_index)
        ? Math.max(0, Math.floor(delta.task_index))
        : null;
    const suggestedName =
      typeof delta.task_label === "string" && delta.task_label.trim()
        ? delta.task_label.trim()
        : taskIndex !== null
          ? `subagent-${taskIndex + 1}`
          : null;
    const groupKey =
      typeof delta.fanout_group_id === "string" && delta.fanout_group_id
        ? delta.fanout_group_id
        : `${ownerWorldlineId}:${delta.parent_tool_call_id ?? "subagents"}`;

    let groupBase = fanoutOrderBaseByGroup.get(groupKey);
    if (groupBase === undefined) {
      groupBase = Date.now();
      fanoutOrderBaseByGroup.set(groupKey, groupBase);
    }

    const createdAtHint =
      taskIndex !== null ? new Date(groupBase + taskIndex).toISOString() : null;
    const parentWorldlineId =
      typeof delta.source_worldline_id === "string" && delta.source_worldline_id
        ? delta.source_worldline_id
        : ownerWorldlineId;
    const visibilityHint: VisibleWorldlineHint = {
      parentWorldlineId,
      suggestedName,
      createdAt: createdAtHint,
    };

    if (typeof delta.child_worldline_id === "string" && delta.child_worldline_id) {
      ensureWorldlineVisible(delta.child_worldline_id, visibilityHint);
    }
    if (typeof delta.result_worldline_id === "string" && delta.result_worldline_id) {
      ensureWorldlineVisible(delta.result_worldline_id, visibilityHint);
    }
  };

  setWorldlineSending(worldlineId, true);

  try {
    await streamChatTurn({
      worldlineId,
      message,
      provider,
      model,
      maxIterations,
      onEvent: (frame) => {
        const frameWorldlineId = frame.worldline_id;
        ensureWorldlineVisible(frameWorldlineId);

        const frameStreamingState =
          getStreamingState(frameWorldlineId) ?? createStreamingState();
        setStreamingState(
          frameWorldlineId,
          clearStreamingFromEvent(frameStreamingState, frame.event),
        );

        if (frame.event.type === "user_message") {
          const existing = getEvents(frameWorldlineId);
          const { events: updated } = replaceOptimisticWithReal(
            existing,
            optimisticId,
            frame.event,
          );
          setWorldlineEvents(frameWorldlineId, updated);
        } else {
          appendEvent(frameWorldlineId, frame.event);
        }

        if (isActiveWorldline(frameWorldlineId)) {
          onScroll();
          onStatusChange(statusFromStreamEvent(frame.event.type));
        }
      },
      onDelta: (frame) => {
        const frameWorldlineId = frame.worldline_id;
        ensureWorldlineVisible(frameWorldlineId);
        ensureDeltaWorldlinesVisible(frameWorldlineId, frame.delta);

        const frameStreamingState =
          getStreamingState(frameWorldlineId) ?? createStreamingState();
        setStreamingState(
          frameWorldlineId,
          applyStreamingDelta(frameStreamingState, frame.delta),
        );

        const stateTransition = stateTransitionFromDelta(frame.delta);
        if (stateTransition) {
          appendRuntimeStateTransition(frameWorldlineId, stateTransition);
          if (isActiveWorldline(frameWorldlineId)) {
            onStatusChange(`State: ${stateTransition.to_state.replace(/_/g, " ")}`);
            onScroll();
          }
          return;
        }

        if (isActiveWorldline(frameWorldlineId)) {
          const status = statusFromDelta(frame.delta);
          if (status) {
            onStatusChange(status);
          }
          onScroll();
        }
      },
      onDone: async (frame) => {
        const completedWorldlineId = frame.worldline_id;
        resetStreamingDrafts(completedWorldlineId);
        void refreshWorldlines().catch(() => undefined);

        if (isActiveWorldline(completedWorldlineId)) {
          onStatusChange("Done");
          onScroll();
        }
        onTurnCompleted();
      },
      onError: (error) => {
        resetStreamingDrafts(worldlineId);
        rollbackOptimisticMessage(worldlineId, optimisticId);
        onStatusChange(`Error: ${error}`);
      },
    });
  } catch (error) {
    resetStreamingDrafts(worldlineId);
    rollbackOptimisticMessage(worldlineId, optimisticId);
    const message = error instanceof Error ? error.message : "Request failed";
    onStatusChange(message);
  } finally {
    setWorldlineSending(worldlineId, false);
  }
};

export function createSessionStore(
  partialDependencies: Partial<SessionStoreDependencies> = {},
) {
  const dependencies: SessionStoreDependencies = {
    fetchChatSession,
    createChatJob,
    sendPromptWithStreaming: sendPromptWithStreamingInternal,
    createWorldlineManager: createWorldlineManagerInternal,
    hydrateChatJobs: (jobs) => {
      chatJobs.hydrateSnapshot(jobs);
    },
    registerQueuedJob: (job) => {
      chatJobs.registerQueuedJob(job);
    },
    pollChatJobs: () => chatJobs.poll(),
    getChatJobsById: () => get(chatJobs).jobsById,
    ...partialDependencies,
  };

  const store = writable<SessionState>(INITIAL_STATE);
  let state: SessionState = INITIAL_STATE;
  let runtimeCallbacks: SessionRuntimeCallbacks = {
    refreshContextTables: NOOP_ASYNC,
    scrollToBottom: NOOP_SCROLL,
    onTurnCompleted: NOOP_TURN_COMPLETED,
  };

  function commit(next: SessionState): void {
    state = next;
    store.set(next);
  }

  function mutate(updater: (previous: SessionState) => SessionState): void {
    commit(updater(state));
  }

  function setStatusText(statusText: string): void {
    mutate((previous) => ({ ...previous, statusText }));
  }

  function setWorldlineEvents(worldlineId: string, events: TimelineEvent[]): void {
    const persistedTrace = extractPersistedStateTrace(events);
    mutate((previous) => {
      let nextStateTraceByWorldline = previous.stateTraceByWorldline;
      if (persistedTrace.length > 0 || !previous.stateTraceByWorldline[worldlineId]) {
        nextStateTraceByWorldline = withRuntimeStateTrace(
          previous.stateTraceByWorldline,
          worldlineId,
          persistedTrace,
        );
      }
      return {
        ...previous,
        eventsByWorldline: withWorldlineEvents(previous.eventsByWorldline, worldlineId, events),
        stateTraceByWorldline: nextStateTraceByWorldline,
      };
    });
  }

  function appendEvent(worldlineId: string, event: TimelineEvent): void {
    mutate((previous) => {
      let nextStateTraceByWorldline = previous.stateTraceByWorldline;
      if (event.type === "assistant_message") {
        const trace = extractPersistedStateTrace([event]);
        if (trace.length > 0) {
          nextStateTraceByWorldline = withRuntimeStateTrace(
            previous.stateTraceByWorldline,
            worldlineId,
            trace,
          );
        }
      }
      return {
        ...previous,
        eventsByWorldline: withAppendedWorldlineEvent(previous.eventsByWorldline, worldlineId, event),
        stateTraceByWorldline: nextStateTraceByWorldline,
      };
    });
  }

  function setStreamingState(worldlineId: string, streamState: StreamingState): void {
    mutate((previous) => ({
      ...previous,
      streamingByWorldline: withStreamingState(
        previous.streamingByWorldline,
        worldlineId,
        streamState,
      ),
    }));
  }

  function setWorldlineSending(worldlineId: string, isSending: boolean): void {
    mutate((previous) => ({
      ...previous,
      sendingByWorldline: withWorldlineSending(previous.sendingByWorldline, worldlineId, isSending),
    }));
  }

  function resetStreamingDrafts(worldlineId?: string): void {
    mutate((previous) => ({
      ...previous,
      streamingByWorldline: withoutStreamingState(previous.streamingByWorldline, worldlineId),
    }));
  }

  function rollbackOptimisticMessage(worldlineId: string, optimisticId: string | null): void {
    mutate((previous) => ({
      ...previous,
      eventsByWorldline: rollbackOptimisticWorldlineEvent(
        previous.eventsByWorldline,
        worldlineId,
        optimisticId,
      ),
    }));
  }

  function ensureWorldlineVisible(worldlineId: string, hint?: VisibleWorldlineHint): void {
    mutate((previous) => ({
      ...previous,
      worldlines: withVisibleWorldline(previous.worldlines, worldlineId, hint),
    }));
  }

  const worldlineManager = dependencies.createWorldlineManager({
    get threadId() {
      return state.threadId || null;
    },
    getActiveWorldlineId: () => state.activeWorldlineId,
    getWorldlines: () => state.worldlines,
    getEventsByWorldline: () => state.eventsByWorldline,
    setWorldlines: (worldlines) => {
      mutate((previous) => ({
        ...previous,
        worldlines,
      }));
    },
    setActiveWorldlineId: (worldlineId) => {
      mutate((previous) => ({
        ...previous,
        activeWorldlineId: worldlineId,
      }));
    },
    persistPreferredWorldline,
    setWorldlineEvents,
    onStatusChange: setStatusText,
    onScroll: () => runtimeCallbacks.scrollToBottom(),
    refreshContextTables: () => runtimeCallbacks.refreshContextTables(),
  });

  function configureRuntime(partialRuntime: Partial<SessionRuntimeCallbacks>): void {
    runtimeCallbacks = {
      ...runtimeCallbacks,
      ...partialRuntime,
    };
  }

  function initializeThreadSession(threadId: string): void {
    commit({
      ...INITIAL_STATE,
      threadId,
      statusText: "Ready",
      isReady: true,
    });
  }

  function selectArtifact(artifactId: string | null): void {
    mutate((previous) => ({ ...previous, selectedArtifactId: artifactId }));
  }

  async function hydrateThread(
    targetThreadId: string,
    preferredWorldlineId?: string,
  ): Promise<void> {
    mutate((previous) => ({
      ...previous,
      isHydratingThread: true,
      statusText: "Loading thread...",
      threadId: targetThreadId,
      worldlines: [],
      eventsByWorldline: {},
      activeWorldlineId: "",
      selectedArtifactId: null,
      streamingByWorldline: {},
      sendingByWorldline: {},
      stateTraceByWorldline: {},
    }));

    try {
      const session = await dependencies.fetchChatSession(targetThreadId);
      const worldlines = session.worldlines.map(mapSummaryWorldlineToItem);
      dependencies.hydrateChatJobs(session.jobs);

      const candidateWorldlineIds = new Set(worldlines.map((line) => line.id));
      const preferredFromSession =
        typeof session.preferred_worldline_id === "string"
          ? session.preferred_worldline_id
          : null;
      const nextActiveWorldlineId =
        preferredFromSession && candidateWorldlineIds.has(preferredFromSession)
          ? preferredFromSession
          : preferredWorldlineId && candidateWorldlineIds.has(preferredWorldlineId)
            ? preferredWorldlineId
            : worldlines[0]?.id ?? "";

      mutate((previous) => ({
        ...previous,
        worldlines,
        activeWorldlineId: nextActiveWorldlineId,
      }));

      if (nextActiveWorldlineId) {
        persistPreferredWorldline(nextActiveWorldlineId);
        await worldlineManager.loadWorldline(nextActiveWorldlineId);
        setStatusText(resolveHydratedStatus(nextActiveWorldlineId, session.jobs));
        runtimeCallbacks.scrollToBottom(true);
      } else {
        setStatusText("Ready");
      }

      mutate((previous) => ({ ...previous, isReady: true }));
    } catch (error) {
      mutate((previous) => ({
        ...previous,
        statusText: error instanceof Error ? error.message : "Failed to load thread",
        isReady: false,
      }));
    } finally {
      mutate((previous) => ({ ...previous, isHydratingThread: false }));
    }
  }

  async function selectWorldline(worldlineId: string): Promise<void> {
    await worldlineManager.selectWorldline(worldlineId);
  }

  async function refreshWorldlines(): Promise<void> {
    await worldlineManager.refreshWorldlines();
  }

  async function branchFromEvent(eventId: string): Promise<string | null> {
    return worldlineManager.branchFromEvent(eventId);
  }

  async function queuePrompt(input: QueuePromptInput): Promise<void> {
    const message = input.message.trim();
    if (!message) {
      return;
    }

    let targetWorldlineId =
      input.worldlineId && input.worldlineId.trim()
        ? input.worldlineId
        : state.activeWorldlineId;
    if (!targetWorldlineId) {
      targetWorldlineId = await worldlineManager.ensureWorldline();
    }
    if (!targetWorldlineId) {
      setStatusText("Error: No active worldline. Please refresh the page.");
      return;
    }

    try {
      const contextualMessage = input.buildContextualMessage
        ? input.buildContextualMessage(message)
        : message;
      const job = await dependencies.createChatJob({
        worldlineId: targetWorldlineId,
        message: contextualMessage,
        provider: input.provider,
        model: input.model?.trim() || undefined,
        maxIterations: input.maxIterations ?? 20,
      });

      setStatusText(
        job.queue_position && job.queue_position > 1
          ? `Queued request (${job.queue_position} in line)`
          : "Queued request",
      );
      dependencies.registerQueuedJob(job);
      input.onAccepted?.();
      void dependencies.pollChatJobs().catch(() => undefined);
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : "Failed to queue request");
    }
  }

  async function sendPrompt(input: SendPromptInput): Promise<void> {
    const message = input.message.trim();
    if (!message) {
      return;
    }

    const requestWorldlineId = await worldlineManager.ensureWorldline();
    if (!requestWorldlineId) {
      setStatusText("Error: No active worldline. Please refresh the page.");
      return;
    }

    if (input.beforeSend) {
      await input.beforeSend(requestWorldlineId);
    }

    const isCurrentWorldlineSending = Boolean(state.sendingByWorldline[requestWorldlineId]);
    const queueStats = computeWorldlineQueueStats(
      requestWorldlineId,
      dependencies.getChatJobsById(),
    );
    const hasPendingWorldlineJobs = queueStats.depth > 0;

    if (isCurrentWorldlineSending || hasPendingWorldlineJobs) {
      await queuePrompt({
        ...input,
        worldlineId: requestWorldlineId,
      });
      return;
    }

    input.onAccepted?.();
    input.onStreamingStart?.();
    setStatusText("Agent is thinking...");
    runtimeCallbacks.scrollToBottom(true);
    resetStreamingDrafts(requestWorldlineId);
    selectArtifact(null);
    mutate((previous) => ({
      ...previous,
      stateTraceByWorldline: withRuntimeStateTrace(
        previous.stateTraceByWorldline,
        requestWorldlineId,
        [],
      ),
    }));

    const { id: optimisticId, event: optimisticEvent } = createOptimisticUserMessage(message);
    const currentEvents = state.eventsByWorldline[requestWorldlineId] ?? [];
    setWorldlineEvents(
      requestWorldlineId,
      insertOptimisticEvent(currentEvents, optimisticEvent),
    );
    runtimeCallbacks.scrollToBottom(true);

    const contextualMessage = input.buildContextualMessage
      ? input.buildContextualMessage(message)
      : message;

    await dependencies.sendPromptWithStreaming(
      {
        worldlineId: requestWorldlineId,
        message: contextualMessage,
        provider: input.provider,
        model: input.model?.trim() || undefined,
        maxIterations: input.maxIterations ?? 20,
      },
      {
        optimisticId,
        getActiveWorldlineId: () => state.activeWorldlineId,
        getEvents: (worldlineId) => state.eventsByWorldline[worldlineId] ?? [],
        setWorldlineEvents,
        appendEvent,
        rollbackOptimisticMessage,
        setWorldlineSending,
        resetStreamingDrafts,
        refreshWorldlines,
        ensureWorldlineVisible,
        onStatusChange: (status) => {
          if (state.activeWorldlineId === requestWorldlineId) {
            setStatusText(status);
          }
        },
        onScroll: () => {
          if (state.activeWorldlineId === requestWorldlineId) {
            runtimeCallbacks.scrollToBottom();
          }
        },
        onTurnCompleted: () => {
          runtimeCallbacks.onTurnCompleted();
        },
        setStreamingState,
        getStreamingState: (worldlineId) =>
          state.streamingByWorldline[worldlineId] ?? createStreamingState(),
        appendRuntimeStateTransition: (worldlineId, transition) => {
          mutate((previous) => ({
            ...previous,
            stateTraceByWorldline: appendRuntimeStateTransition(
              previous.stateTraceByWorldline,
              worldlineId,
              transition,
            ),
          }));
        },
      },
    );
  }

  return {
    subscribe: store.subscribe,
    configureRuntime,
    initializeThreadSession,
    setStatusText,
    selectArtifact,
    hydrateThread,
    selectWorldline,
    refreshWorldlines,
    branchFromEvent,
    queuePrompt,
    sendPrompt,
  };
}

export type SessionStore = ReturnType<typeof createSessionStore>;
