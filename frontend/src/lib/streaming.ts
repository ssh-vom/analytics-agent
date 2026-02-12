import type {
  StreamDeltaPayload,
  StreamDeltaType,
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
      type: "sql" | "python";
      code: string;
      rawArgs: string;
      createdAt: string;
      skipped?: boolean;
      skipReason?: string;
    };

export interface StreamingState {
  text: string;
  textCreatedAt: string;
  toolCalls: Map<
    string,
    {
      type: "sql" | "python";
      code: string;
      rawArgs: string;
      createdAt: string;
      skipped?: boolean;
      skipReason?: string;
    }
  >;
}

const FALLBACK_DRAFT_ID: Record<"sql" | "python", string> = {
  sql: "sql-draft",
  python: "python-draft",
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
  kind: "sql" | "python"
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
  kind: "sql" | "python",
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

function findAliasDraftIdForCall(
  state: StreamingState,
  kind: "sql" | "python",
  canonicalCallId: string
): string | null {
  if (state.toolCalls.has(canonicalCallId)) {
    return canonicalCallId;
  }
  const fallbackId = FALLBACK_DRAFT_ID[kind];
  if (state.toolCalls.has(fallbackId)) {
    return fallbackId;
  }
  return null;
}

function resolveDeleteDraftId(
  state: StreamingState,
  kind: "sql" | "python",
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

function deltaTypeToKind(type: StreamDeltaType): "sql" | "python" | null {
  if (type === "tool_call_sql") return "sql";
  if (type === "tool_call_python") return "python";
  return null;
}

function extractCodeFromArgs(rawArgs: string, kind: "sql" | "python"): string {
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

  const kind = deltaTypeToKind(delta.type);
  if (!kind) return state;

  if (delta.skipped) {
    const draftId = resolveDeleteDraftId(state, kind, delta.call_id);
    if (!draftId) {
      return state;
    }
    const existing = state.toolCalls.get(draftId);
    if (!existing) {
      return state;
    }
    // Keep the draft but mark as skipped so the cell shows a "Skipped" badge
    const next = new Map(state.toolCalls);
    next.set(draftId, {
      ...existing,
      skipped: true,
      skipReason: typeof delta.reason === "string" ? delta.reason : undefined,
    });
    return { ...state, toolCalls: next };
  }

  if (delta.done) {
    const callId = resolveDeltaDraftId(state, kind, delta.call_id);
    const aliasId = findAliasDraftIdForCall(state, kind, callId);
    const existing = aliasId ? state.toolCalls.get(aliasId) : undefined;
    if (existing) {
      const finalCode = extractCodeFromArgs(existing.rawArgs, kind);
      const next = new Map(state.toolCalls);
      if (aliasId && aliasId !== callId) {
        next.delete(aliasId);
      }
      next.set(callId, { ...existing, code: finalCode });
      return { ...state, toolCalls: next };
    }
    return state;
  }

  const callId = resolveDeltaDraftId(state, kind, delta.call_id);
  const argsDelta = delta.delta ?? "";

  let nextText = state.text;
  let nextTextCreatedAt = state.textCreatedAt;
  if (kind === "sql" && state.text && !state.toolCalls.has(callId)) {
    nextText = "";
    nextTextCreatedAt = "";
  }

  const next = new Map(state.toolCalls);
  const aliasId = findAliasDraftIdForCall(state, kind, callId);
  const existingEntry = aliasId ? state.toolCalls.get(aliasId) : undefined;
  if (existingEntry) {
    const rawArgs = existingEntry.rawArgs + argsDelta;
    if (aliasId && aliasId !== callId) {
      next.delete(aliasId);
    }
    next.set(callId, {
      type: kind,
      rawArgs,
      code: extractCodeFromArgs(rawArgs, kind),
      createdAt: existingEntry.createdAt,
    });
  } else {
    next.set(callId, {
      type: kind,
      rawArgs: argsDelta,
      code: extractCodeFromArgs(argsDelta, kind),
      createdAt: new Date().toISOString(),
    });
  }
  return { ...state, text: nextText, textCreatedAt: nextTextCreatedAt, toolCalls: next };
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
      : { code: item.code, timeout: 30, call_id: item.callId };
  if (item.skipped) {
    payload.skipped = true;
    payload.skip_reason = item.skipReason;
  }
  return {
    id: `draft-${item.type}-${item.callId}`,
    parent_event_id: null,
    type: item.type === "sql" ? "tool_call_sql" : "tool_call_python",
    payload,
    created_at: item.createdAt,
  };
}
