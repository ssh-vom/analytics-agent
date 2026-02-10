import type {
  StreamDeltaPayload,
  StreamDeltaType,
  TimelineEvent,
} from "$lib/types";

/** A display item is either a persisted event or a streaming placeholder. */
export type DisplayItem =
  | { kind: "event"; event: TimelineEvent }
  | { kind: "streaming_text"; text: string; createdAt: string }
  | { kind: "streaming_tool"; callId: string; type: "sql" | "python"; code: string; rawArgs: string; createdAt: string };

export interface StreamingState {
  text: string;
  textCreatedAt: string;
  toolCalls: Map<string, { type: "sql" | "python"; code: string; rawArgs: string; createdAt: string }>;
}

export function createStreamingState(): StreamingState {
  return {
    text: "",
    textCreatedAt: "",
    toolCalls: new Map(),
  };
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
      return parsed[codeField];
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
      return JSON.parse(`"${match[1]}"`);
    } catch {
      return match[1]
        .replace(/\\n/g, "\n")
        .replace(/\\t/g, "\t")
        .replace(/\\"/g, '"')
        .replace(/\\\\/g, "\\");
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

  if (delta.done) {
    const callId = (delta.call_id ?? `${kind}-draft`).trim() || `${kind}-draft`;
    const existing = state.toolCalls.get(callId);
    if (existing) {
      const finalCode = extractCodeFromArgs(existing.rawArgs, kind);
      const next = new Map(state.toolCalls);
      next.set(callId, { ...existing, code: finalCode });
      return { ...state, toolCalls: next };
    }
    return state;
  }

  const callId = (delta.call_id ?? `${kind}-draft`).trim() || `${kind}-draft`;
  const argsDelta = delta.delta ?? "";

  let nextText = state.text;
  let nextTextCreatedAt = state.textCreatedAt;
  if (kind === "sql" && state.text && !state.toolCalls.has(callId)) {
    nextText = "";
    nextTextCreatedAt = "";
  }

  const existing = state.toolCalls.get(callId);
  const next = new Map(state.toolCalls);
  if (existing) {
    const rawArgs = existing.rawArgs + argsDelta;
    next.set(callId, {
      type: kind,
      rawArgs,
      code: extractCodeFromArgs(rawArgs, kind),
      createdAt: existing.createdAt,
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
    return { ...state, text: "", textCreatedAt: "" };
  }
  if (event.type === "tool_call_sql") {
    const callId = event.payload?.call_id;
    const toDelete =
      typeof callId === "string" && callId.trim()
        ? callId
        : (() => {
            const sqlCalls = [...state.toolCalls.entries()].filter(
              ([_, v]) => v.type === "sql"
            );
            if (sqlCalls.length === 0) return null;
            const mostRecent = sqlCalls.sort(
              (a, b) =>
                new Date(b[1].createdAt).getTime() -
                new Date(a[1].createdAt).getTime()
            )[0];
            return mostRecent[0];
          })();
    if (toDelete) {
      const next = new Map(state.toolCalls);
      next.delete(toDelete);
      return { ...state, toolCalls: next };
    }
    return state;
  }
  if (event.type === "tool_call_python") {
    const callId = event.payload?.call_id;
    const toDelete =
      typeof callId === "string" && callId.trim()
        ? callId
        : (() => {
            const pyCalls = [...state.toolCalls.entries()].filter(
              ([_, v]) => v.type === "python"
            );
            if (pyCalls.length === 0) return null;
            const mostRecent = pyCalls.sort(
              (a, b) =>
                new Date(b[1].createdAt).getTime() -
                new Date(a[1].createdAt).getTime()
            )[0];
            return mostRecent[0];
          })();
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
  if (item.type === "sql") {
    return {
      id: `draft-sql-${item.callId}`,
      parent_event_id: null,
      type: "tool_call_sql",
      payload: { sql: item.code, limit: 100, call_id: item.callId },
      created_at: item.createdAt,
    };
  }
  return {
    id: `draft-python-${item.callId}`,
    parent_event_id: null,
    type: "tool_call_python",
    payload: { code: item.code, timeout: 30, call_id: item.callId },
    created_at: item.createdAt,
  };
}
