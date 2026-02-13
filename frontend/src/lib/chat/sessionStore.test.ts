import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  createChatJob,
  fetchChatSession,
  fetchThreadWorldlines,
  fetchWorldlineEvents,
  streamChatTurn,
} from "$lib/api/client";
import type { ChatJob, ChatSessionResponse, TimelineEvent, WorldlineSummaryItem } from "$lib/types";
import { createSessionStore, type SessionState, type SessionStore } from "./sessionStore";

vi.mock("$lib/api/client", () => ({
  fetchChatSession: vi.fn(),
  createChatJob: vi.fn(),
  fetchThreadWorldlines: vi.fn(),
  fetchWorldlineEvents: vi.fn(),
  branchWorldline: vi.fn(),
  createWorldline: vi.fn(),
  streamChatTurn: vi.fn(),
}));

type StoreHarness = {
  store: SessionStore;
  hydrateChatJobs: ReturnType<typeof vi.fn>;
  registerQueuedJob: ReturnType<typeof vi.fn>;
  pollChatJobs: ReturnType<typeof vi.fn>;
  getChatJobsById: ReturnType<typeof vi.fn>;
  sendPromptWithStreaming: ReturnType<typeof vi.fn>;
};

function snapshot(store: SessionStore) {
  let next: SessionState = {
    threadId: "",
    activeWorldlineId: "",
    worldlines: [],
    eventsByWorldline: {},
    streamingByWorldline: {},
    sendingByWorldline: {},
    stateTraceByWorldline: {},
    statusText: "",
    selectedArtifactId: null,
    isReady: false,
    isHydratingThread: false,
  };
  const unsubscribe = store.subscribe((state) => {
    next = state;
  });
  unsubscribe();
  return next;
}

function makeWorldline(id: string): WorldlineSummaryItem {
  return {
    id,
    parent_worldline_id: null,
    forked_from_event_id: null,
    head_event_id: null,
    name: id,
    created_at: "2026-01-01T00:00:00Z",
    message_count: 0,
    last_event_at: null,
    last_activity: "2026-01-01T00:00:00Z",
    jobs: {
      queued: 0,
      running: 0,
      completed: 0,
      failed: 0,
      cancelled: 0,
      latest_status: null,
    },
  };
}

function makeJob(overrides: Partial<ChatJob> = {}): ChatJob {
  return {
    id: overrides.id ?? "job_1",
    thread_id: overrides.thread_id ?? "thread_1",
    worldline_id: overrides.worldline_id ?? "worldline_1",
    parent_job_id: overrides.parent_job_id ?? null,
    fanout_group_id: overrides.fanout_group_id ?? null,
    task_label: overrides.task_label ?? null,
    parent_tool_call_id: overrides.parent_tool_call_id ?? null,
    status: overrides.status ?? "queued",
    error: overrides.error ?? null,
    request: overrides.request ?? {},
    result_worldline_id: overrides.result_worldline_id ?? null,
    result_summary: overrides.result_summary ?? null,
    seen_at: overrides.seen_at ?? null,
    created_at: overrides.created_at ?? "2026-01-01T00:00:00Z",
    started_at: overrides.started_at ?? null,
    finished_at: overrides.finished_at ?? null,
    queue_position: overrides.queue_position,
  };
}

function makeAssistantEvent(id: string): TimelineEvent {
  return {
    id,
    parent_event_id: null,
    type: "assistant_message",
    payload: { text: id },
    created_at: "2026-01-01T00:00:00Z",
  };
}

function makeUserEvent(id: string): TimelineEvent {
  return {
    id,
    parent_event_id: null,
    type: "user_message",
    payload: { text: id },
    created_at: "2026-01-01T00:00:00Z",
  };
}

function makeSessionResponse(options: {
  preferredWorldlineId: string | null;
  worldlines: string[];
  jobs?: ChatJob[];
}): ChatSessionResponse {
  return {
    thread: {
      id: "thread_1",
      title: "Thread",
      created_at: "2026-01-01T00:00:00Z",
      message_count: 0,
      last_activity: "2026-01-01T00:00:00Z",
    },
    worldlines: options.worldlines.map((id) => makeWorldline(id)),
    jobs: options.jobs ?? [],
    preferred_worldline_id: options.preferredWorldlineId,
  };
}

function createHarness(
  overrides: Partial<Pick<StoreHarness, "getChatJobsById" | "sendPromptWithStreaming">> = {},
): StoreHarness {
  const hydrateChatJobs = vi.fn();
  const registerQueuedJob = vi.fn();
  const pollChatJobs = vi.fn(async () => undefined);
  const getChatJobsById = overrides.getChatJobsById ?? vi.fn(() => ({}));
  const sendPromptWithStreaming =
    overrides.sendPromptWithStreaming ?? vi.fn(async () => undefined);

  const store = createSessionStore({
    hydrateChatJobs,
    registerQueuedJob,
    pollChatJobs,
    getChatJobsById,
    sendPromptWithStreaming,
  });

  return {
    store,
    hydrateChatJobs,
    registerQueuedJob,
    pollChatJobs,
    getChatJobsById,
    sendPromptWithStreaming,
  };
}

describe("sessionStore", () => {
  const storage: Record<string, string> = {};

  beforeEach(() => {
    for (const key of Object.keys(storage)) {
      delete storage[key];
    }
    Object.defineProperty(globalThis, "localStorage", {
      configurable: true,
      value: {
        getItem: (key: string) => (key in storage ? storage[key] : null),
        setItem: (key: string, value: string) => {
          storage[key] = String(value);
        },
        removeItem: (key: string) => {
          delete storage[key];
        },
      },
    });
    vi.clearAllMocks();
  });

  it("hydrates thread state and honors preferred worldline from session", async () => {
    vi.mocked(fetchChatSession).mockResolvedValue(
      makeSessionResponse({
        preferredWorldlineId: "worldline_2",
        worldlines: ["worldline_1", "worldline_2"],
        jobs: [
          makeJob({
            id: "job_running",
            worldline_id: "worldline_2",
            status: "running",
          }),
        ],
      }),
    );
    vi.mocked(fetchWorldlineEvents).mockResolvedValue([
      makeAssistantEvent("event_worldline_2"),
    ]);

    const harness = createHarness();
    await harness.store.hydrateThread("thread_1", "worldline_1");

    const state = snapshot(harness.store);
    expect(state.threadId).toBe("thread_1");
    expect(state.activeWorldlineId).toBe("worldline_2");
    expect(state.worldlines).toHaveLength(2);
    expect(state.eventsByWorldline.worldline_2).toHaveLength(1);
    expect(state.statusText).toBe("Background job running (1)");
    expect(state.isReady).toBe(true);
    expect(harness.hydrateChatJobs).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem("textql_active_worldline")).toBe("worldline_2");
  });

  it("selectWorldline loads events and refreshes context callbacks", async () => {
    vi.mocked(fetchChatSession).mockResolvedValue(
      makeSessionResponse({
        preferredWorldlineId: "worldline_1",
        worldlines: ["worldline_1", "worldline_2"],
      }),
    );
    vi.mocked(fetchWorldlineEvents).mockImplementation(async (worldlineId) => [
      makeAssistantEvent(`event_${worldlineId}`),
    ]);

    const harness = createHarness();
    const refreshContextTables = vi.fn(async () => undefined);
    harness.store.configureRuntime({
      refreshContextTables,
      scrollToBottom: vi.fn(),
      onTurnCompleted: vi.fn(),
    });

    await harness.store.hydrateThread("thread_1");
    await harness.store.selectWorldline("worldline_2");

    const state = snapshot(harness.store);
    expect(state.activeWorldlineId).toBe("worldline_2");
    expect(state.eventsByWorldline.worldline_2).toHaveLength(1);
    expect(refreshContextTables).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem("textql_active_worldline")).toBe("worldline_2");
  });

  it("refreshWorldlines updates list from API for current thread", async () => {
    vi.mocked(fetchThreadWorldlines).mockResolvedValue({
      worldlines: [makeWorldline("worldline_3")],
      next_cursor: null,
    });

    const harness = createHarness();
    harness.store.initializeThreadSession("thread_1");
    await harness.store.refreshWorldlines();

    const state = snapshot(harness.store);
    expect(state.worldlines.map((worldline) => worldline.id)).toEqual(["worldline_3"]);
    expect(fetchThreadWorldlines).toHaveBeenCalledWith("thread_1");
  });

  it("sendPrompt queues when current worldline has pending work", async () => {
    vi.mocked(fetchChatSession).mockResolvedValue(
      makeSessionResponse({
        preferredWorldlineId: "worldline_1",
        worldlines: ["worldline_1"],
      }),
    );
    vi.mocked(fetchWorldlineEvents).mockResolvedValue([]);
    vi.mocked(createChatJob).mockResolvedValue(
      makeJob({
        id: "job_queued",
        status: "queued",
        worldline_id: "worldline_1",
        queue_position: 2,
      }),
    );

    const pendingJobs = {
      existing: makeJob({
        id: "existing",
        status: "running",
        worldline_id: "worldline_1",
      }),
    };

    const harness = createHarness({
      getChatJobsById: vi.fn(() => pendingJobs),
    });
    await harness.store.hydrateThread("thread_1");

    const onAccepted = vi.fn();
    await harness.store.sendPrompt({
      message: "hello",
      provider: "openai",
      onAccepted,
      buildContextualMessage: (message) => `CTX:${message}`,
    });

    const state = snapshot(harness.store);
    expect(createChatJob).toHaveBeenCalledWith(
      expect.objectContaining({
        worldlineId: "worldline_1",
        message: "CTX:hello",
      }),
    );
    expect(harness.sendPromptWithStreaming).not.toHaveBeenCalled();
    expect(harness.registerQueuedJob).toHaveBeenCalledTimes(1);
    expect(harness.pollChatJobs).toHaveBeenCalledTimes(1);
    expect(onAccepted).toHaveBeenCalledTimes(1);
    expect(state.statusText).toBe("Queued request (2 in line)");
  });

  it("sendPrompt streams immediately when worldline is idle", async () => {
    vi.mocked(fetchChatSession).mockResolvedValue(
      makeSessionResponse({
        preferredWorldlineId: "worldline_1",
        worldlines: ["worldline_1"],
      }),
    );
    vi.mocked(fetchWorldlineEvents).mockResolvedValue([]);

    const onTurnCompleted = vi.fn();
    const sendPromptWithStreaming = vi.fn(async (_options, context) => {
      context.onStatusChange("Done");
      context.onTurnCompleted();
    });

    const harness = createHarness({
      sendPromptWithStreaming,
      getChatJobsById: vi.fn(() => ({})),
    });
    harness.store.configureRuntime({
      onTurnCompleted,
      scrollToBottom: vi.fn(),
      refreshContextTables: vi.fn(async () => undefined),
    });

    await harness.store.hydrateThread("thread_1");
    harness.store.selectArtifact("artifact_1");

    const onAccepted = vi.fn();
    await harness.store.sendPrompt({
      message: "  hello world  ",
      provider: "openrouter",
      buildContextualMessage: (message) => `CTX:${message}`,
      onAccepted,
    });

    const state = snapshot(harness.store);
    expect(sendPromptWithStreaming).toHaveBeenCalledWith(
      expect.objectContaining({
        worldlineId: "worldline_1",
        message: "CTX:hello world",
      }),
      expect.any(Object),
    );
    expect(onAccepted).toHaveBeenCalledTimes(1);
    expect(onTurnCompleted).toHaveBeenCalledTimes(1);
    expect(state.statusText).toBe("Done");
    expect(state.selectedArtifactId).toBeNull();
    expect(state.eventsByWorldline.worldline_1[0].id.startsWith("optimistic-user-")).toBe(true);
  });

  it("internal streaming flow keeps done fan-in non-blocking", async () => {
    vi.mocked(fetchChatSession).mockResolvedValue(
      makeSessionResponse({
        preferredWorldlineId: "worldline_1",
        worldlines: ["worldline_1"],
      }),
    );
    vi.mocked(fetchWorldlineEvents).mockResolvedValue([]);
    const callOrder: string[] = [];
    vi.mocked(fetchThreadWorldlines).mockImplementation(async () => {
      callOrder.push("refresh");
      return {
        worldlines: [makeWorldline("worldline_1")],
        next_cursor: null,
      };
    });
    vi.mocked(streamChatTurn).mockImplementation(async (options) => {
      await options.onEvent({
        seq: 1,
        worldline_id: "worldline_1",
        event: makeUserEvent("event_user_1"),
      });
      await options.onDone?.({
        seq: 2,
        worldline_id: "worldline_1",
        done: true,
      });
    });

    const store = createSessionStore({
      hydrateChatJobs: vi.fn(),
      registerQueuedJob: vi.fn(),
      pollChatJobs: vi.fn(async () => undefined),
      getChatJobsById: vi.fn(() => ({})),
    });
    store.configureRuntime({
      onTurnCompleted: () => {
        callOrder.push("turnCompleted");
      },
      scrollToBottom: vi.fn(),
      refreshContextTables: vi.fn(async () => undefined),
    });

    await store.hydrateThread("thread_1");
    await store.sendPrompt({
      message: "hello",
      provider: "openrouter",
    });

    const state = snapshot(store);
    expect(callOrder).toEqual(["refresh", "turnCompleted"]);
    expect(state.eventsByWorldline.worldline_1[0].id).toBe("event_user_1");
    expect(state.statusText).toBe("Done");
  });

  it("internal streaming flow updates subagent status and worldline visibility", async () => {
    vi.mocked(fetchChatSession).mockResolvedValue(
      makeSessionResponse({
        preferredWorldlineId: "worldline_1",
        worldlines: ["worldline_1"],
      }),
    );
    vi.mocked(fetchWorldlineEvents).mockResolvedValue([]);
    vi.mocked(fetchThreadWorldlines).mockResolvedValue({
      worldlines: [makeWorldline("worldline_1"), makeWorldline("worldline_child_1")],
      next_cursor: null,
    });
    vi.mocked(streamChatTurn).mockImplementation(async (options) => {
      await options.onDelta?.({
        seq: 1,
        worldline_id: "worldline_1",
        delta: {
          type: "subagent_progress",
          child_worldline_id: "worldline_child_1",
          task_count: 3,
          completed_count: 1,
          failed_count: 0,
          timed_out_count: 0,
        },
      });
      await options.onDone?.({
        seq: 2,
        worldline_id: "worldline_1",
        done: true,
      });
    });

    const statusHistory: string[] = [];
    const store = createSessionStore({
      hydrateChatJobs: vi.fn(),
      registerQueuedJob: vi.fn(),
      pollChatJobs: vi.fn(async () => undefined),
      getChatJobsById: vi.fn(() => ({})),
    });
    const unsubscribe = store.subscribe((state) => {
      statusHistory.push(state.statusText);
    });

    await store.hydrateThread("thread_1");
    await store.sendPrompt({
      message: "fan out",
      provider: "openai",
    });
    unsubscribe();

    const state = snapshot(store);
    expect(statusHistory).toContain("Running subagents (1/3)...");
    expect(state.statusText).toBe("Done");
    expect(state.worldlines.some((line) => line.id === "worldline_child_1")).toBe(true);
  });
});
