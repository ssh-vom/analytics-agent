import type { ChatJob } from "$lib/types";

export type WorldlineQueueStats = {
  depth: number;
  running: number;
  queued: number;
};

export function computeWorldlineQueueStats(
  worldlineId: string,
  jobsById: Record<string, ChatJob>,
): WorldlineQueueStats {
  if (!worldlineId) {
    return { depth: 0, running: 0, queued: 0 };
  }

  let running = 0;
  let queued = 0;
  for (const job of Object.values(jobsById)) {
    if (job.worldline_id !== worldlineId) {
      continue;
    }
    if (job.status === "running") {
      running += 1;
    } else if (job.status === "queued") {
      queued += 1;
    }
  }

  return {
    depth: running + queued,
    running,
    queued,
  };
}
