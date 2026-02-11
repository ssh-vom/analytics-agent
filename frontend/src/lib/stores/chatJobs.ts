import { get, writable } from "svelte/store";

import { ackChatJob, fetchChatJobs } from "$lib/api/client";
import type { ChatJob } from "$lib/types";

const POLL_INTERVAL_MS = 2000;

type FinalJobStatus = "completed" | "failed";

export interface JobToast {
  id: string;
  jobId: string;
  threadId: string;
  status: FinalJobStatus;
  title: string;
  message: string;
  resultWorldlineId: string | null;
}

interface ThreadJobSummary {
  queued: number;
  running: number;
  active: number;
}

interface ChatJobsState {
  jobsById: Record<string, ChatJob>;
  toasts: JobToast[];
  lastUpdatedAt: string | null;
  error: string | null;
}

function createChatJobsStore() {
  const { subscribe, update, set } = writable<ChatJobsState>({
    jobsById: {},
    toasts: [],
    lastUpdatedAt: null,
    error: null,
  });

  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let pollInFlight = false;
  let hasInitialSnapshot = false;
  const notifiedJobIds = new Set<string>();

  function reset() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
    pollInFlight = false;
    hasInitialSnapshot = false;
    notifiedJobIds.clear();
    set({
      jobsById: {},
      toasts: [],
      lastUpdatedAt: null,
      error: null,
    });
  }

  function dismissToast(toastId: string): void {
    update((state) => ({
      ...state,
      toasts: state.toasts.filter((toast) => toast.id !== toastId),
    }));
  }

  function registerQueuedJob(job: ChatJob): void {
    update((state) => ({
      ...state,
      jobsById: {
        ...state.jobsById,
        [job.id]: job,
      },
      lastUpdatedAt: new Date().toISOString(),
    }));
  }

  function getThreadSummary(threadId: string): ThreadJobSummary {
    if (!threadId) {
      return { queued: 0, running: 0, active: 0 };
    }

    const state = get({ subscribe });
    let queued = 0;
    let running = 0;
    for (const job of Object.values(state.jobsById)) {
      if (job.thread_id !== threadId) {
        continue;
      }
      if (job.status === "queued") {
        queued += 1;
      } else if (job.status === "running") {
        running += 1;
      }
    }

    return {
      queued,
      running,
      active: queued + running,
    };
  }

  function getWorldlineQueueDepth(worldlineId: string): number {
    if (!worldlineId) {
      return 0;
    }

    const state = get({ subscribe });
    return Object.values(state.jobsById).filter(
      (job) =>
        job.worldline_id === worldlineId &&
        (job.status === "queued" || job.status === "running"),
    ).length;
  }

  function buildToastFromJob(job: ChatJob & { status: FinalJobStatus }): JobToast {
    const preview = job.result_summary?.assistant_preview?.trim();
    const fallbackMessage =
      job.status === "completed"
        ? "Background request finished."
        : job.error || "Background request failed.";

    return {
      id: `${job.id}-${Date.now()}`,
      jobId: job.id,
      threadId: job.thread_id,
      status: job.status,
      title:
        job.status === "completed"
          ? "Background analysis complete"
          : "Background analysis failed",
      message: preview && job.status === "completed" ? preview : fallbackMessage,
      resultWorldlineId: job.result_worldline_id,
    };
  }

  async function poll(): Promise<void> {
    if (pollInFlight) {
      return;
    }

    pollInFlight = true;
    try {
      const response = await fetchChatJobs({
        statuses: ["queued", "running", "completed", "failed"],
        limit: 500,
      });

      const previousById = get({ subscribe }).jobsById;
      const nextById: Record<string, ChatJob> = {};
      for (const job of response.jobs) {
        nextById[job.id] = job;
      }

      const toasts: JobToast[] = [];
      if (hasInitialSnapshot) {
        for (const job of response.jobs) {
          const previous = previousById[job.id];
          const transitionedToFinal =
            previous != null &&
            previous.status !== job.status &&
            (job.status === "completed" || job.status === "failed");

          if (!transitionedToFinal) {
            continue;
          }
          if (job.seen_at || notifiedJobIds.has(job.id)) {
            continue;
          }

          notifiedJobIds.add(job.id);
          toasts.push(
            buildToastFromJob(job as ChatJob & { status: FinalJobStatus }),
          );
          void ackChatJob(job.id, true).catch(() => undefined);
        }
      }

      update((state) => ({
        ...state,
        jobsById: nextById,
        toasts: [...state.toasts, ...toasts].slice(-6),
        lastUpdatedAt: new Date().toISOString(),
        error: null,
      }));

      hasInitialSnapshot = true;
    } catch (error) {
      update((state) => ({
        ...state,
        error: error instanceof Error ? error.message : "Failed to poll chat jobs",
      }));
    } finally {
      pollInFlight = false;
    }
  }

  function startPolling(): void {
    if (pollTimer) {
      return;
    }
    void poll();
    pollTimer = setInterval(() => {
      void poll();
    }, POLL_INTERVAL_MS);
  }

  function stopPolling(): void {
    if (!pollTimer) {
      return;
    }
    clearInterval(pollTimer);
    pollTimer = null;
  }

  return {
    subscribe,
    startPolling,
    stopPolling,
    poll,
    reset,
    dismissToast,
    registerQueuedJob,
    getThreadSummary,
    getWorldlineQueueDepth,
  };
}

export const chatJobs = createChatJobsStore();
