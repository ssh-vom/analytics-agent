import { beforeEach, describe, expect, it, vi } from "vitest";

import { fetchThreads } from "$lib/api/client";
import { threads } from "./threads";

vi.mock("$lib/api/client", () => ({
  createThread: vi.fn(),
  fetchThreads: vi.fn(),
}));

type ThreadsState = {
  threads: Array<{
    id: string;
    name: string;
    createdAt: string;
    lastActivity: string;
    messageCount: number;
  }>;
  loading: boolean;
  error: string | null;
};

function snapshot(): ThreadsState {
  let state: ThreadsState = { threads: [], loading: false, error: null };
  const unsubscribe = threads.subscribe((next) => {
    state = next;
  });
  unsubscribe();
  return state;
}

describe("threads store", () => {
  const storage: Record<string, string> = {};

  beforeEach(() => {
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

    for (const key of Object.keys(storage)) {
      delete storage[key];
    }
    vi.clearAllMocks();
    threads.saveThreads([]);
    localStorage.removeItem("textql_threads_cache");
  });

  it("loads threads and caches them on success", async () => {
    vi.mocked(fetchThreads).mockResolvedValue({
      threads: [
        {
          id: "thread_1",
          title: "Main",
          created_at: "2026-01-01T00:00:00Z",
          message_count: 5,
          last_activity: "2026-01-01T00:01:00Z",
        },
      ],
      next_cursor: null,
    });

    await threads.loadThreads();

    const state = snapshot();
    expect(state.threads).toHaveLength(1);
    expect(state.threads[0].id).toBe("thread_1");
    expect(fetchThreads).toHaveBeenCalledTimes(1);

    const cachedRaw = localStorage.getItem("textql_threads_cache");
    expect(cachedRaw).not.toBeNull();
    const cached = cachedRaw ? JSON.parse(cachedRaw) : [];
    expect(Array.isArray(cached)).toBe(true);
    expect(cached[0].id).toBe("thread_1");
  });

  it("falls back to cached threads after transient fetch failures", async () => {
    localStorage.setItem(
      "textql_threads_cache",
      JSON.stringify([
        {
          id: "thread_cached",
          name: "Cached Thread",
          createdAt: "2026-01-01T00:00:00Z",
          lastActivity: "2026-01-01T00:00:00Z",
          messageCount: 2,
        },
      ]),
    );
    vi.mocked(fetchThreads).mockRejectedValue(new Error("database is locked"));

    await threads.loadThreads();

    const state = snapshot();
    expect(fetchThreads).toHaveBeenCalledTimes(3);
    expect(state.threads).toHaveLength(1);
    expect(state.threads[0].id).toBe("thread_cached");
    expect(state.error).toContain("database is locked");
  });

  it("retries and succeeds without dropping existing threads", async () => {
    vi.mocked(fetchThreads)
      .mockRejectedValueOnce(new Error("temporary failure"))
      .mockResolvedValueOnce({
        threads: [
          {
            id: "thread_retry",
            title: "Retried",
            created_at: "2026-01-01T00:00:00Z",
            message_count: 1,
            last_activity: "2026-01-01T00:02:00Z",
          },
        ],
        next_cursor: null,
      });

    await threads.loadThreads();

    const state = snapshot();
    expect(fetchThreads).toHaveBeenCalledTimes(2);
    expect(state.threads).toHaveLength(1);
    expect(state.threads[0].id).toBe("thread_retry");
    expect(state.error).toBeNull();
  });
});
