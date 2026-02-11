import { removeOptimisticEvent } from "$lib/chat/optimisticState";
import type { StreamingState } from "$lib/streaming";
import type { TimelineEvent } from "$lib/types";

export function withStreamingState(
  statesByWorldline: Record<string, StreamingState>,
  worldlineId: string,
  state: StreamingState,
): Record<string, StreamingState> {
  return {
    ...statesByWorldline,
    [worldlineId]: state,
  };
}

export function withoutStreamingState(
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

export function withWorldlineSending(
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

export function rollbackOptimisticWorldlineEvent(
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
