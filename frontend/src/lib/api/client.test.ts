import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fetchThreadWorldlineSummaries,
  fetchWorldlineEvents,
} from "./client";

function buildEvent(id: string) {
  return {
    id,
    parent_event_id: null,
    type: "assistant_message" as const,
    payload: { text: id },
    created_at: "2026-01-01T00:00:00Z",
  };
}

describe("fetchWorldlineEvents", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("follows cursor pagination until completion", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            events: [buildEvent("event_1")],
            next_cursor: "event_1",
          }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            events: [buildEvent("event_2")],
            next_cursor: null,
          }),
          { status: 200 },
        ),
      );

    vi.stubGlobal("fetch", fetchMock);

    const events = await fetchWorldlineEvents("worldline_1", {
      pageSize: 2,
      maxPages: 5,
    });

    expect(events.map((event) => event.id)).toEqual(["event_1", "event_2"]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(String(fetchMock.mock.calls[0][0])).toContain("limit=2");
    expect(String(fetchMock.mock.calls[1][0])).toContain("cursor=event_1");
  });

  it("stops when maxPages is reached", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        new Response(
          JSON.stringify({
            events: [buildEvent("event_1")],
            next_cursor: "event_1",
          }),
          { status: 200 },
        ),
      );

    vi.stubGlobal("fetch", fetchMock);

    const events = await fetchWorldlineEvents("worldline_1", {
      pageSize: 100,
      maxPages: 1,
    });

    expect(events.map((event) => event.id)).toEqual(["event_1"]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("fetchThreadWorldlineSummaries", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("requests worldline summaries endpoint", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          worldlines: [
            {
              id: "worldline_1",
              parent_worldline_id: null,
              forked_from_event_id: null,
              head_event_id: "event_1",
              name: "main",
              created_at: "2026-01-01T00:00:00Z",
              message_count: 2,
              last_event_at: "2026-01-01T00:01:00Z",
              last_activity: "2026-01-01T00:01:00Z",
              jobs: {
                queued: 0,
                running: 0,
                completed: 1,
                failed: 0,
                cancelled: 0,
                latest_status: "completed",
              },
            },
          ],
          next_cursor: null,
        }),
        { status: 200 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const response = await fetchThreadWorldlineSummaries("thread_1");

    expect(response.worldlines).toHaveLength(1);
    expect(response.worldlines[0].message_count).toBe(2);
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/api/threads/thread_1/worldline-summaries",
    );
  });
});
