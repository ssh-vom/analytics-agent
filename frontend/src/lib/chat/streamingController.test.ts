import { beforeEach, describe, expect, it, vi } from "vitest";

import type { StreamDeltaPayload, TimelineEvent } from "$lib/types";
import { createStreamingState, type StreamingState } from "$lib/streaming";
import { streamChatTurn } from "$lib/api/client";
import { sendPromptWithStreaming } from "./streamingController";

vi.mock("$lib/api/client", () => ({
  streamChatTurn: vi.fn(),
}));

function makeOptimisticEvent(id: string): TimelineEvent {
  return {
    id,
    parent_event_id: null,
    type: "user_message",
    payload: { text: "hello", optimistic: true },
    created_at: "2026-01-01T00:00:00Z",
  };
}

function makeRealUserEvent(id: string): TimelineEvent {
  return {
    id,
    parent_event_id: null,
    type: "user_message",
    payload: { text: "hello" },
    created_at: "2026-01-01T00:00:01Z",
  };
}

describe("streamingController", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("keeps done lifecycle lightweight and non-blocking", async () => {
    const worldlineId = "w1";
    const optimisticId = "optimistic:1";
    let eventsByWorldline: Record<string, TimelineEvent[]> = {
      [worldlineId]: [makeOptimisticEvent(optimisticId)],
    };
    let streamingStateByWorldline: Record<string, StreamingState> = {};
    const callOrder: string[] = [];

    vi.mocked(streamChatTurn).mockImplementation(async (options) => {
      await options.onEvent({
        seq: 1,
        worldline_id: worldlineId,
        event: makeRealUserEvent("event_user_1"),
      });
      await options.onDone?.({
        seq: 2,
        worldline_id: worldlineId,
        done: true,
      });
    });

    await sendPromptWithStreaming(
      {
        worldlineId,
        message: "hello",
        provider: "openai",
      },
      {
        optimisticId,
        getActiveWorldlineId: () => worldlineId,
        getEvents: (id) => eventsByWorldline[id] ?? [],
        setWorldlineEvents: (id, events) => {
          eventsByWorldline = { ...eventsByWorldline, [id]: events };
        },
        appendEvent: vi.fn(),
        rollbackOptimisticMessage: vi.fn(),
        setWorldlineSending: vi.fn(),
        resetStreamingDrafts: vi.fn(),
        refreshWorldlines: vi.fn(async () => {
          callOrder.push("refresh");
        }),
        ensureWorldlineVisible: vi.fn(),
        onStatusChange: vi.fn(),
        onScroll: vi.fn(),
        onTurnCompleted: () => {
          callOrder.push("turnCompleted");
        },
        setStreamingState: (id, state) => {
          streamingStateByWorldline = { ...streamingStateByWorldline, [id]: state };
        },
        getStreamingState: (id) => streamingStateByWorldline[id],
        appendRuntimeStateTransition: vi.fn(),
      }
    );

    expect(callOrder).toEqual(["refresh", "turnCompleted"]);
    expect(eventsByWorldline[worldlineId][0].id).toBe("event_user_1");
  });

  it("does not issue done-time worldline reloads", async () => {
    const worldlineId = "w1";
    const optimisticId = "optimistic:1";
    const optimisticEvent = makeOptimisticEvent(optimisticId);
    const refreshWorldlines = vi.fn(async () => undefined);

    vi.mocked(streamChatTurn).mockImplementation(async (options) => {
      await options.onDone?.({
        seq: 1,
        worldline_id: worldlineId,
        done: true,
      });
    });

    await sendPromptWithStreaming(
      {
        worldlineId,
        message: "hello",
        provider: "openai",
      },
      {
        optimisticId,
        getActiveWorldlineId: () => worldlineId,
        getEvents: () => [optimisticEvent],
        setWorldlineEvents: vi.fn(),
        appendEvent: vi.fn(),
        rollbackOptimisticMessage: vi.fn(),
        setWorldlineSending: vi.fn(),
        resetStreamingDrafts: vi.fn(),
        refreshWorldlines,
        ensureWorldlineVisible: vi.fn(),
        onStatusChange: vi.fn(),
        onScroll: vi.fn(),
        onTurnCompleted: vi.fn(),
        setStreamingState: vi.fn(),
        getStreamingState: () => undefined,
        appendRuntimeStateTransition: vi.fn(),
      }
    );

    expect(refreshWorldlines).toHaveBeenCalledTimes(1);
  });

  it("updates status text from subagent progress deltas", async () => {
    const worldlineId = "w1";
    const childWorldlineId = "worldline_child_1";
    const statuses: string[] = [];
    const setStreamingState = vi.fn();
    const ensureWorldlineVisible = vi.fn();

    vi.mocked(streamChatTurn).mockImplementation(async (options) => {
      const delta: StreamDeltaPayload = {
        type: "subagent_progress",
        child_worldline_id: childWorldlineId,
        task_count: 3,
        completed_count: 1,
        failed_count: 0,
        timed_out_count: 0,
      };
      await options.onDelta?.({
        seq: 1,
        worldline_id: worldlineId,
        delta,
      });
      await options.onDone?.({
        seq: 2,
        worldline_id: worldlineId,
        done: true,
      });
    });

    await sendPromptWithStreaming(
      {
        worldlineId,
        message: "fan out",
        provider: "openai",
      },
      {
        optimisticId: "optimistic:1",
        getActiveWorldlineId: () => worldlineId,
        getEvents: () => [],
        setWorldlineEvents: vi.fn(),
        appendEvent: vi.fn(),
        rollbackOptimisticMessage: vi.fn(),
        setWorldlineSending: vi.fn(),
        resetStreamingDrafts: vi.fn(),
        refreshWorldlines: vi.fn(async () => undefined),
        ensureWorldlineVisible,
        onStatusChange: (status) => {
          statuses.push(status);
        },
        onScroll: vi.fn(),
        onTurnCompleted: vi.fn(),
        setStreamingState,
        getStreamingState: () => createStreamingState(),
        appendRuntimeStateTransition: vi.fn(),
      }
    );

    expect(setStreamingState).toHaveBeenCalled();
    expect(ensureWorldlineVisible).toHaveBeenCalledWith(worldlineId);
    expect(ensureWorldlineVisible).toHaveBeenCalledWith(
      childWorldlineId,
      expect.objectContaining({
        parentWorldlineId: worldlineId,
      }),
    );
    expect(statuses).toContain("Running subagents (1/3)...");
    expect(statuses.at(-1)).toBe("Done");
  });
});
