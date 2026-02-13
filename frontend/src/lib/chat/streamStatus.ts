import type { StreamDeltaPayload, TimelineEvent } from "$lib/types";

export function statusFromStreamEvent(eventType: TimelineEvent["type"]): string {
  if (eventType === "tool_call_sql") {
    return "Running SQL...";
  }
  if (eventType === "tool_call_python") {
    return "Running Python...";
  }
  if (eventType === "tool_call_subagents") {
    return "Spawning subagents...";
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
  if (delta.type === "tool_call_subagents" && !delta.done) {
    return "Drafting subagent fan-out...";
  }
  if (delta.type === "subagent_progress") {
    const total = typeof delta.task_count === "number" ? delta.task_count : 0;
    const completed = typeof delta.completed_count === "number" ? delta.completed_count : 0;
    const failed = typeof delta.failed_count === "number" ? delta.failed_count : 0;
    const timedOut = typeof delta.timed_out_count === "number" ? delta.timed_out_count : 0;
    const doneCount = completed + failed + timedOut;

    if (delta.phase === "finished" && delta.task_status === "failed") {
      if (delta.failure_code === "subagent_loop_limit") {
        return "Subagent failed (loop limit reached)...";
      }
      return "Subagent failed...";
    }
    if (delta.phase === "finished" && delta.task_status === "completed") {
      if (total > 0) {
        return `Subagents completed (${doneCount}/${total})...`;
      }
      return "Subagents completed...";
    }

    if (delta.phase === "retrying") {
      if (total > 0) {
        return `Retrying subagents (${doneCount}/${total})...`;
      }
      return "Retrying subagents...";
    }

    if (delta.phase === "queued" || delta.task_status === "queued") {
      return "Queued subagents...";
    }

    if (total > 0) {
      return `Running subagents (${doneCount}/${total})...`;
    }
    return "Running subagents...";
  }

  return null;
}
