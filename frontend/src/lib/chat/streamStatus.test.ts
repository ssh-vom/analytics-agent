import { describe, expect, it } from "vitest";

import { statusFromDelta, statusFromStreamEvent } from "$lib/chat/streamStatus";

describe("streamStatus", () => {
  it("maps stream events to status labels", () => {
    expect(statusFromStreamEvent("tool_call_sql")).toBe("Running SQL...");
    expect(statusFromStreamEvent("tool_call_python")).toBe("Running Python...");
    expect(statusFromStreamEvent("tool_call_subagents")).toBe("Spawning subagents...");
    expect(statusFromStreamEvent("assistant_message")).toBe("Done");
  });

  it("maps stream deltas to status labels", () => {
    expect(
      statusFromDelta({ type: "tool_call_python", skipped: true, reason: "invalid_tool_payload" }),
    ).toBe("Retrying after invalid tool payload...");
    expect(statusFromDelta({ type: "assistant_text", delta: "hello" })).toBe(
      "Composing response...",
    );
    expect(
      statusFromDelta({
        type: "subagent_progress",
        task_count: 3,
        completed_count: 1,
        failed_count: 0,
        timed_out_count: 0,
      }),
    ).toBe("Running subagents (1/3)...");
    expect(statusFromDelta({ type: "state_transition", to_state: "planning" })).toBeNull();
  });
});
