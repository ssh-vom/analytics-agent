import { describe, expect, it } from "vitest";

import { computeWorldlineQueueStats } from "$lib/chat/queueStats";
import type { ChatJob } from "$lib/types";

function makeJob(id: string, worldlineId: string, status: ChatJob["status"]): ChatJob {
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
    created_at: "2026-01-01T00:00:00Z",
    started_at: null,
    finished_at: null,
  };
}

describe("queueStats", () => {
  it("counts queued and running jobs per worldline", () => {
    const jobsById: Record<string, ChatJob> = {
      job1: makeJob("job1", "worldline_a", "running"),
      job2: makeJob("job2", "worldline_a", "queued"),
      job3: makeJob("job3", "worldline_a", "completed"),
      job4: makeJob("job4", "worldline_b", "queued"),
    };

    const stats = computeWorldlineQueueStats("worldline_a", jobsById);
    expect(stats).toEqual({ depth: 2, running: 1, queued: 1 });
  });
});
