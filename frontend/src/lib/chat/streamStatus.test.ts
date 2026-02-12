import { describe, expect, it } from "vitest";

import { statusFromDelta, statusFromStreamEvent } from "$lib/chat/streamStatus";

describe("streamStatus", () => {
  it("maps stream events to status labels", () => {
    expect(statusFromStreamEvent("tool_call_sql")).toBe("Running SQL...");
    expect(statusFromStreamEvent("tool_call_python")).toBe("Running Python...");
    expect(statusFromStreamEvent("assistant_message")).toBe("Done");
  });

  it("maps stream deltas to status labels", () => {
    expect(
      statusFromDelta({ type: "tool_call_python", skipped: true, reason: "invalid_tool_payload" }),
    ).toBe("Retrying after invalid tool payload...");
    expect(statusFromDelta({ type: "assistant_text", delta: "hello" })).toBe(
      "Composing response...",
    );
    expect(statusFromDelta({ type: "state_transition", to_state: "planning" })).toBeNull();
  });
});
