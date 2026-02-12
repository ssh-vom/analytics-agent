import { describe, expect, it } from "vitest";

import type { TimelineEvent } from "$lib/types";
import {
  appendRuntimeStateTransition,
  extractPersistedStateTrace,
  runtimeStatePath,
  stateTransitionFromDelta,
} from "$lib/chat/stateTrace";

describe("stateTrace", () => {
  it("extracts persisted trace from latest assistant event", () => {
    const events: TimelineEvent[] = [
      {
        id: "event_user",
        parent_event_id: null,
        type: "user_message",
        payload: { text: "hello" },
        created_at: "2026-01-01T00:00:00Z",
      },
      {
        id: "event_assistant",
        parent_event_id: "event_user",
        type: "assistant_message",
        payload: {
          text: "done",
          state_trace: [
            { from: null, to: "planning", reason: "turn_started" },
            {
              from: "planning",
              to: "presenting",
              reason: "assistant_text_ready",
            },
          ],
        },
        created_at: "2026-01-01T00:00:01Z",
      },
    ];

    const trace = extractPersistedStateTrace(events);
    expect(trace).toHaveLength(2);
    expect(runtimeStatePath(trace)).toBe("planning -> presenting");
  });

  it("parses state transition deltas", () => {
    const transition = stateTransitionFromDelta({
      type: "state_transition",
      from_state: "planning",
      to_state: "analyzing",
      reason: "tool_result:run_sql_success",
    });

    expect(transition).toEqual({
      from_state: "planning",
      to_state: "analyzing",
      reason: "tool_result:run_sql_success",
    });
  });

  it("appends transitions by worldline", () => {
    const next = appendRuntimeStateTransition(
      {},
      "worldline_a",
      { from_state: null, to_state: "planning", reason: "turn_started" },
    );

    expect(next.worldline_a).toHaveLength(1);
    expect(next.worldline_a[0]?.to_state).toBe("planning");
  });
});
