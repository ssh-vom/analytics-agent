import type { StreamDeltaPayload, TimelineEvent } from "$lib/types";

export type RuntimeStateTransition = {
  from_state: string | null;
  to_state: string;
  reason: string;
};

const MAX_TRANSITIONS_PER_WORLDLINE = 24;

export function normalizeRuntimeStateTransition(
  value: unknown,
): RuntimeStateTransition | null {
  if (!value || typeof value !== "object") {
    return null;
  }

  const record = value as Record<string, unknown>;
  const toState = typeof record.to === "string" ? record.to : null;
  if (!toState) {
    return null;
  }

  const fromState =
    typeof record.from === "string" ? record.from : record.from === null ? null : null;
  const reason =
    typeof record.reason === "string" && record.reason.length > 0
      ? record.reason
      : "unspecified";

  return {
    from_state: fromState,
    to_state: toState,
    reason,
  };
}

export function extractPersistedStateTrace(
  events: TimelineEvent[],
): RuntimeStateTransition[] {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event.type !== "assistant_message") {
      continue;
    }

    const rawTrace = event.payload?.state_trace;
    if (!Array.isArray(rawTrace)) {
      continue;
    }

    const parsed = rawTrace
      .map((entry) => normalizeRuntimeStateTransition(entry))
      .filter((entry): entry is RuntimeStateTransition => entry !== null);

    if (parsed.length > 0) {
      return parsed.slice(-MAX_TRANSITIONS_PER_WORLDLINE);
    }
  }

  return [];
}

export function withRuntimeStateTrace(
  stateTraceByWorldline: Record<string, RuntimeStateTransition[]>,
  worldlineId: string,
  trace: RuntimeStateTransition[],
): Record<string, RuntimeStateTransition[]> {
  return {
    ...stateTraceByWorldline,
    [worldlineId]: trace.slice(-MAX_TRANSITIONS_PER_WORLDLINE),
  };
}

export function appendRuntimeStateTransition(
  stateTraceByWorldline: Record<string, RuntimeStateTransition[]>,
  worldlineId: string,
  transition: RuntimeStateTransition,
): Record<string, RuntimeStateTransition[]> {
  const existing = stateTraceByWorldline[worldlineId] ?? [];
  return withRuntimeStateTrace(stateTraceByWorldline, worldlineId, [
    ...existing,
    transition,
  ]);
}

export function stateTransitionFromDelta(
  delta: StreamDeltaPayload,
): RuntimeStateTransition | null {
  if (delta.type !== "state_transition") {
    return null;
  }

  const toState = typeof delta.to_state === "string" ? delta.to_state : "";
  if (!toState) {
    return null;
  }

  const fromState = typeof delta.from_state === "string" ? delta.from_state : null;
  const reason =
    typeof delta.reason === "string" && delta.reason.length > 0
      ? delta.reason
      : "unspecified";

  return {
    from_state: fromState,
    to_state: toState,
    reason,
  };
}

export function runtimeStatePath(trace: RuntimeStateTransition[]): string {
  return trace.map((entry) => entry.to_state).join(" -> ");
}

export function runtimeStateReasons(trace: RuntimeStateTransition[]): string {
  return trace.map((entry) => `${entry.to_state}: ${entry.reason}`).join(" | ");
}
