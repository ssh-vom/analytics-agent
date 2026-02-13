import { describe, expect, it } from "vitest";

import type { TimelineEvent } from "$lib/types";
import type { DisplayItem } from "$lib/streaming";
import { groupDisplayItemsIntoCells, groupEventsIntoCells } from "./cells";

function makeSubagentCallEvent(
  id: string,
  callId: string,
): TimelineEvent {
  return {
    id,
    parent_event_id: null,
    type: "tool_call_subagents",
    payload: { call_id: callId, goal: "fan out" },
    created_at: "2026-01-01T00:00:00Z",
  };
}

function makeSubagentResultEvent(
  id: string,
  parentEventId: string,
  parentToolCallId: string,
): TimelineEvent {
  return {
    id,
    parent_event_id: parentEventId,
    type: "tool_result_subagents",
    payload: {
      parent_tool_call_id: parentToolCallId,
      task_count: 2,
      completed_count: 2,
      failed_count: 0,
      timed_out_count: 0,
      partial_failure: false,
      tasks: [
        { task_index: 0, task_label: "a", status: "completed" },
        { task_index: 1, task_label: "b", status: "completed" },
      ],
    },
    created_at: "2026-01-01T00:00:01Z",
  };
}

describe("cells", () => {
  it("reconciles subagent result that arrives before call using parent_tool_call_id", () => {
    const events: TimelineEvent[] = [
      makeSubagentResultEvent("event_result", "missing_parent", "call_1"),
      makeSubagentCallEvent("event_call", "call_1"),
    ];

    const cells = groupEventsIntoCells(events);
    const subagentCells = cells.filter((cell) => cell.kind === "subagents");

    expect(subagentCells).toHaveLength(1);
    const subagentCell = subagentCells[0];
    if (subagentCell.kind !== "subagents") {
      throw new Error("unexpected cell type");
    }
    expect(subagentCell.call?.id).toBe("event_call");
    expect(subagentCell.result?.id).toBe("event_result");
  });

  it("uses streaming progress without creating duplicate subagent cell", () => {
    const callEvent = makeSubagentCallEvent("event_call", "call_1");
    const items: DisplayItem[] = [
      { kind: "event", event: callEvent },
      {
        kind: "streaming_tool",
        callId: "call_1",
        type: "subagents",
        code: "tasks: 1/3 complete",
        rawArgs: '{"goal":"fan out"}',
        createdAt: "2026-01-01T00:00:02Z",
        subagentProgress: {
          task_count: 3,
          completed_count: 1,
          failed_count: 0,
          timed_out_count: 0,
          partial_failure: false,
          tasks: [
            {
              task_index: 0,
              task_label: "a",
              status: "completed",
              child_worldline_id: "w1",
              result_worldline_id: "w1",
              assistant_preview: "ok",
              error: "",
            },
          ],
        },
      },
    ];

    const cells = groupDisplayItemsIntoCells(items);
    const subagentCells = cells.filter((cell) => cell.kind === "subagents");

    expect(subagentCells).toHaveLength(1);
    const subagentCell = subagentCells[0];
    if (subagentCell.kind !== "subagents") {
      throw new Error("unexpected cell type");
    }
    expect(subagentCell.call?.id).toBe("event_call");
    expect(subagentCell.result?.type).toBe("tool_result_subagents");
    expect(subagentCell.result?.payload._streaming).toBe(true);
  });

  it("keeps persisted subagent result authoritative over streaming progress", () => {
    const callEvent = makeSubagentCallEvent("event_call", "call_1");
    const resultEvent = makeSubagentResultEvent("event_result", "event_call", "call_1");
    const items: DisplayItem[] = [
      { kind: "event", event: callEvent },
      { kind: "event", event: resultEvent },
      {
        kind: "streaming_tool",
        callId: "call_1",
        type: "subagents",
        code: "tasks: 1/3 complete",
        rawArgs: '{"goal":"fan out"}',
        createdAt: "2026-01-01T00:00:02Z",
        subagentProgress: {
          task_count: 3,
          completed_count: 1,
          failed_count: 0,
          timed_out_count: 0,
          partial_failure: false,
          tasks: [],
        },
      },
    ];

    const cells = groupDisplayItemsIntoCells(items);
    const subagentCell = cells.find((cell) => cell.kind === "subagents");
    if (!subagentCell || subagentCell.kind !== "subagents") {
      throw new Error("missing subagent cell");
    }
    expect(subagentCell.result?.id).toBe("event_result");
    expect(subagentCell.result?.payload._streaming).not.toBe(true);
  });
});
