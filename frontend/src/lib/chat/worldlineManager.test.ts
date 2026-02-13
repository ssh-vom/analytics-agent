import { beforeEach, describe, expect, it, vi } from "vitest";

import type { TimelineEvent } from "$lib/types";
import {
  branchWorldline,
  createWorldline,
  fetchThreadWorldlines,
  fetchWorldlineEvents,
} from "$lib/api/client";
import { createWorldlineManager } from "./worldlineManager";

vi.mock("$lib/api/client", () => ({
  fetchThreadWorldlines: vi.fn(),
  fetchWorldlineEvents: vi.fn(),
  branchWorldline: vi.fn(),
  createWorldline: vi.fn(),
}));

function makeEvent(id: string): TimelineEvent {
  return {
    id,
    parent_event_id: null,
    type: "assistant_message",
    payload: { text: id },
    created_at: "2026-01-01T00:00:00Z",
  };
}

describe("worldlineManager", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("selectWorldline reads current events via getter (no stale snapshot)", async () => {
    let threadId: string | null = "thread_1";
    let activeWorldlineId = "w1";
    let worldlines = [{ id: "w1" }];
    let eventsByWorldline: Record<string, TimelineEvent[]> = {};

    const setWorldlineEvents = vi.fn();
    const refreshContextTables = vi.fn(async () => undefined);
    const onScroll = vi.fn();
    const setActiveWorldlineId = vi.fn((id: string) => {
      activeWorldlineId = id;
    });
    const persistPreferredWorldline = vi.fn();

    const manager = createWorldlineManager({
      get threadId() {
        return threadId;
      },
      getActiveWorldlineId: () => activeWorldlineId,
      getWorldlines: () => worldlines,
      getEventsByWorldline: () => eventsByWorldline,
      setWorldlines: vi.fn(),
      setActiveWorldlineId,
      persistPreferredWorldline,
      setWorldlineEvents,
      onStatusChange: vi.fn(),
      onScroll,
      refreshContextTables,
    });

    // Update after manager creation to ensure getter sees latest map.
    eventsByWorldline = { w1: [makeEvent("persisted_1")] };
    await manager.selectWorldline("w1");

    expect(fetchWorldlineEvents).not.toHaveBeenCalled();
    expect(setWorldlineEvents).not.toHaveBeenCalled();
    expect(setActiveWorldlineId).toHaveBeenCalledWith("w1");
    expect(persistPreferredWorldline).toHaveBeenCalledWith("w1");
    expect(refreshContextTables).toHaveBeenCalledTimes(1);
    expect(onScroll).toHaveBeenCalled();
    expect(threadId).toBe("thread_1");
    expect(worldlines).toHaveLength(1);
  });

  it("loadWorldline merges fetched events with optimistic local tail", async () => {
    let eventsByWorldline: Record<string, TimelineEvent[]> = {
      w1: [
        {
          id: "optimistic:1",
          parent_event_id: null,
          type: "user_message",
          payload: { text: "hello", optimistic: true },
          created_at: "2026-01-01T00:00:01Z",
        },
      ],
    };

    vi.mocked(fetchWorldlineEvents).mockResolvedValue([makeEvent("persisted_1")]);

    const setWorldlineEvents = vi.fn(
      (worldlineId: string, events: TimelineEvent[]) => {
        eventsByWorldline = { ...eventsByWorldline, [worldlineId]: events };
      }
    );

    const manager = createWorldlineManager({
      threadId: "thread_1",
      getActiveWorldlineId: () => "w1",
      getWorldlines: () => [{ id: "w1" }],
      getEventsByWorldline: () => eventsByWorldline,
      setWorldlines: vi.fn(),
      setActiveWorldlineId: vi.fn(),
      persistPreferredWorldline: vi.fn(),
      setWorldlineEvents,
      onStatusChange: vi.fn(),
      onScroll: vi.fn(),
      refreshContextTables: vi.fn(async () => undefined),
    });

    await manager.loadWorldline("w1");

    expect(setWorldlineEvents).toHaveBeenCalledTimes(1);
    const mergedEvents = eventsByWorldline.w1.map((event) => event.id);
    expect(mergedEvents).toEqual(["persisted_1", "optimistic:1"]);
  });

  it("branchFromEvent uses live active worldline and worldline count from getters", async () => {
    let activeWorldlineId = "w-main";
    let worldlines = [{ id: "w-main" }, { id: "w-side" }];
    vi.mocked(branchWorldline).mockResolvedValue({ new_worldline_id: "w-branch" });
    vi.mocked(fetchThreadWorldlines).mockResolvedValue({
      worldlines: [{ id: "w-main" }, { id: "w-side" }, { id: "w-branch" }],
      next_cursor: null,
    });
    vi.mocked(fetchWorldlineEvents).mockResolvedValue([makeEvent("persisted_2")]);

    const manager = createWorldlineManager({
      threadId: "thread_1",
      getActiveWorldlineId: () => activeWorldlineId,
      getWorldlines: () => worldlines,
      getEventsByWorldline: () => ({}),
      setWorldlines: (next) => {
        worldlines = next as Array<{ id: string }>;
      },
      setActiveWorldlineId: (nextId) => {
        activeWorldlineId = nextId;
      },
      persistPreferredWorldline: vi.fn(),
      setWorldlineEvents: vi.fn(),
      onStatusChange: vi.fn(),
      onScroll: vi.fn(),
      refreshContextTables: vi.fn(async () => undefined),
    });

    const branchId = await manager.branchFromEvent("event_1");
    expect(branchId).toBe("w-branch");
    expect(branchWorldline).toHaveBeenCalledWith("w-main", "event_1", "branch-3");
  });

  it("ensureWorldline creates a new worldline when none is active", async () => {
    let threadId: string | null = "thread_1";
    let activeWorldlineId = "";
    vi.mocked(createWorldline).mockResolvedValue({ worldline_id: "w-new" });
    vi.mocked(fetchThreadWorldlines).mockResolvedValue({
      worldlines: [{ id: "w-new" }],
      next_cursor: null,
    });

    const manager = createWorldlineManager({
      get threadId() {
        return threadId;
      },
      getActiveWorldlineId: () => activeWorldlineId,
      getWorldlines: () => [],
      getEventsByWorldline: () => ({}),
      setWorldlines: vi.fn(),
      setActiveWorldlineId: (nextId) => {
        activeWorldlineId = nextId;
      },
      persistPreferredWorldline: vi.fn(),
      setWorldlineEvents: vi.fn(),
      onStatusChange: vi.fn(),
      onScroll: vi.fn(),
      refreshContextTables: vi.fn(async () => undefined),
    });

    const ensured = await manager.ensureWorldline();
    expect(ensured).toBe("w-new");
    expect(activeWorldlineId).toBe("w-new");
    expect(createWorldline).toHaveBeenCalledWith("thread_1", "main");
  });
});
