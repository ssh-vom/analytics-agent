import { describe, expect, it } from "vitest";

import type { ChatJob, TimelineEvent, WorldlineItem } from "$lib/types";
import {
  pickActiveJobWorldlineId,
  withAppendedWorldlineEvent,
  withVisibleWorldline,
  withWorldlineEvents,
} from "./worldlineState";

function makeEvent(id: string): TimelineEvent {
  return {
    id,
    parent_event_id: null,
    type: "assistant_message",
    payload: { text: id },
    created_at: "2026-01-01T00:00:00Z",
  };
}

function makeJob(
  id: string,
  worldlineId: string,
  status: ChatJob["status"],
  createdAt: string,
): ChatJob {
  return {
    id,
    thread_id: "thread_1",
    worldline_id: worldlineId,
    status,
    error: null,
    request: {},
    result_worldline_id: null,
    result_summary: null,
    seen_at: null,
    created_at: createdAt,
    started_at: status === "running" ? createdAt : null,
    finished_at: null,
  };
}

describe("worldlineState", () => {
  it("chooses running job worldline over queued", () => {
    const worldlines: WorldlineItem[] = [
      {
        id: "w1",
        parent_worldline_id: null,
        forked_from_event_id: null,
        head_event_id: null,
        name: "main",
        created_at: "2026-01-01T00:00:00Z",
      },
      {
        id: "w2",
        parent_worldline_id: null,
        forked_from_event_id: null,
        head_event_id: null,
        name: "alt",
        created_at: "2026-01-01T00:00:00Z",
      },
    ];

    const jobs = {
      job_1: makeJob("job_1", "w1", "queued", "2026-01-01T00:00:01Z"),
      job_2: makeJob("job_2", "w2", "running", "2026-01-01T00:00:02Z"),
    };

    const selected = pickActiveJobWorldlineId(worldlines, "thread_1", jobs);
    expect(selected).toBe("w2");
  });

  it("dedupes events when setting worldline events", () => {
    const event = makeEvent("event_1");
    const state = withWorldlineEvents({}, "w1", [event, event]);
    expect(state.w1).toHaveLength(1);
  });

  it("appends worldline events", () => {
    const state = withWorldlineEvents({}, "w1", [makeEvent("event_1")]);
    const next = withAppendedWorldlineEvent(state, "w1", makeEvent("event_2"));
    expect(next.w1.map((event) => event.id)).toEqual(["event_1", "event_2"]);
  });

  it("adds hidden worldline to visible list", () => {
    const initial: WorldlineItem[] = [];
    const next = withVisibleWorldline(initial, "worldline_xyz");
    expect(next).toHaveLength(1);
    expect(next[0].id).toBe("worldline_xyz");
  });

  it("enriches existing placeholder worldline with hint metadata", () => {
    const initial = withVisibleWorldline([], "worldline_child");
    const next = withVisibleWorldline(initial, "worldline_child", {
      parentWorldlineId: "worldline_parent",
      suggestedName: "subagent-1",
      createdAt: "2026-01-01T00:00:00Z",
    });
    expect(next).toHaveLength(1);
    expect(next[0].parent_worldline_id).toBe("worldline_parent");
    expect(next[0].name).toBe("subagent-1");
    expect(next[0].created_at).toBe("2026-01-01T00:00:00Z");
  });
});
