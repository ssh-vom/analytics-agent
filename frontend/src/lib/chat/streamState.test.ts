import { describe, expect, it } from "vitest";

import { createStreamingState } from "$lib/streaming";
import type { TimelineEvent } from "$lib/types";
import {
  rollbackOptimisticWorldlineEvent,
  withStreamingState,
  withWorldlineSending,
  withoutStreamingState,
} from "./streamState";

const optimisticEvent: TimelineEvent = {
  id: "optimistic:123",
  parent_event_id: null,
  type: "user_message",
  payload: { text: "hello", optimistic: true },
  created_at: "2026-01-01T00:00:00Z",
};

describe("streamState", () => {
  it("sets and removes streaming state by worldline", () => {
    const state = withStreamingState({}, "w1", createStreamingState());
    expect(Object.keys(state)).toEqual(["w1"]);

    const removed = withoutStreamingState(state, "w1");
    expect(removed).toEqual({});
  });

  it("tracks worldline sending flags", () => {
    const sending = withWorldlineSending({}, "w1", true);
    expect(sending.w1).toBe(true);

    const cleared = withWorldlineSending(sending, "w1", false);
    expect(cleared.w1).toBeUndefined();
  });

  it("rolls back optimistic events", () => {
    const state = rollbackOptimisticWorldlineEvent(
      { w1: [optimisticEvent] },
      "w1",
      optimisticEvent.id,
    );
    expect(state.w1).toEqual([]);
  });
});
