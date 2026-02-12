import type { StreamDeltaPayload, TimelineEvent } from "$lib/types";

export function statusFromStreamEvent(eventType: TimelineEvent["type"]): string {
  if (eventType === "tool_call_sql") {
    return "Running SQL...";
  }
  if (eventType === "tool_call_python") {
    return "Running Python...";
  }
  if (eventType === "assistant_message") {
    return "Done";
  }
  return "Working...";
}

export function statusFromDelta(delta: StreamDeltaPayload): string | null {
  if (delta.type === "state_transition") {
    return null;
  }

  if (delta.skipped) {
    if (delta.reason === "invalid_tool_payload") {
      return "Retrying after invalid tool payload...";
    }
    if (delta.reason === "recent_identical_successful_tool_call") {
      return "Skipped (already ran)...";
    }
    if (delta.reason === "repeated_identical_tool_call") {
      return "Skipped (repeated in turn)...";
    }
    if (delta.reason === "duplicate_artifact_prevented") {
      return "Skipped (duplicate artifact)...";
    }
    return "Skipped...";
  }

  if (delta.type === "assistant_text" && !delta.done) {
    return "Composing response...";
  }
  if (delta.type === "tool_call_sql" && !delta.done) {
    return "Drafting SQL...";
  }
  if (delta.type === "tool_call_python" && !delta.done) {
    return "Drafting Python...";
  }

  return null;
}
