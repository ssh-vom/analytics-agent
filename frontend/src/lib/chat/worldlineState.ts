import type { ChatJob, TimelineEvent, WorldlineItem } from "$lib/types";

export function pickActiveJobWorldlineId(
  threadWorldlines: WorldlineItem[],
  targetThreadId: string,
  jobsById: Record<string, ChatJob>,
): string | null {
  if (threadWorldlines.length === 0) {
    return null;
  }

  const candidateIds = new Set(threadWorldlines.map((line) => line.id));
  const jobs = Object.values(jobsById).filter(
    (job) =>
      job.thread_id === targetThreadId &&
      candidateIds.has(job.worldline_id) &&
      (job.status === "running" || job.status === "queued"),
  );

  if (jobs.length === 0) {
    return null;
  }

  jobs.sort((left, right) => {
    const statusScore = (status: "running" | "queued") =>
      status === "running" ? 2 : 1;
    const leftScore = statusScore(left.status as "running" | "queued");
    const rightScore = statusScore(right.status as "running" | "queued");
    if (leftScore !== rightScore) {
      return rightScore - leftScore;
    }

    const leftTime = Date.parse(left.started_at ?? left.created_at ?? "") || 0;
    const rightTime = Date.parse(right.started_at ?? right.created_at ?? "") || 0;
    if (leftTime !== rightTime) {
      return rightTime - leftTime;
    }
    return right.id.localeCompare(left.id);
  });

  return jobs[0]?.worldline_id ?? null;
}

export function dedupePreserveOrder(events: TimelineEvent[]): TimelineEvent[] {
  const seenIds = new Set<string>();
  const output: TimelineEvent[] = [];

  for (const event of events) {
    if (seenIds.has(event.id)) {
      continue;
    }
    seenIds.add(event.id);
    output.push(event);
  }

  return output;
}

export function withWorldlineEvents(
  eventsByWorldline: Record<string, TimelineEvent[]>,
  worldlineId: string,
  events: TimelineEvent[],
): Record<string, TimelineEvent[]> {
  return {
    ...eventsByWorldline,
    [worldlineId]: dedupePreserveOrder(events),
  };
}

export function withAppendedWorldlineEvent(
  eventsByWorldline: Record<string, TimelineEvent[]>,
  worldlineId: string,
  event: TimelineEvent,
): Record<string, TimelineEvent[]> {
  const existing = eventsByWorldline[worldlineId] ?? [];
  return withWorldlineEvents(eventsByWorldline, worldlineId, [...existing, event]);
}

export function withVisibleWorldline(
  worldlines: WorldlineItem[],
  worldlineId: string,
): WorldlineItem[] {
  if (worldlines.some((line) => line.id === worldlineId)) {
    return worldlines;
  }

  return [
    ...worldlines,
    {
      id: worldlineId,
      name: worldlineId.slice(0, 12),
      parent_worldline_id: null,
      forked_from_event_id: null,
      head_event_id: null,
      created_at: new Date().toISOString(),
    },
  ];
}
