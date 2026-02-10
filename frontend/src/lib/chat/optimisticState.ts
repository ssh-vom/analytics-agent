import type { TimelineEvent } from "$lib/types";

export interface OptimisticState {
  optimisticId: string | null;
  events: TimelineEvent[];
}

export function createOptimisticUserMessage(text: string): {
  id: string;
  event: TimelineEvent;
} {
  const id = `optimistic-user-${Date.now()}`;
  const event: TimelineEvent = {
    id,
    parent_event_id: null,
    type: "user_message",
    payload: { text },
    created_at: new Date().toISOString(),
  };
  return { id, event };
}

export function insertOptimisticEvent(
  events: TimelineEvent[],
  optimisticEvent: TimelineEvent,
): TimelineEvent[] {
  return [...events, optimisticEvent];
}

export function replaceOptimisticWithReal(
  events: TimelineEvent[],
  optimisticId: string | null,
  realEvent: TimelineEvent,
): {
  events: TimelineEvent[];
  replaced: boolean;
} {
  if (!optimisticId) {
    return { events: [...events, realEvent], replaced: false };
  }

  const filtered = events.filter((e) => e.id !== optimisticId);
  const wasReplaced = filtered.length !== events.length;

  return {
    events: [...filtered, realEvent],
    replaced: wasReplaced,
  };
}

export function removeOptimisticEvent(
  events: TimelineEvent[],
  optimisticId: string | null,
): TimelineEvent[] {
  if (!optimisticId) {
    return events;
  }
  return events.filter((e) => e.id !== optimisticId);
}
