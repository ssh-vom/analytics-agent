import { get, writable } from "svelte/store";

import { ackChatJob, fetchChatJobs } from "$lib/api/client";
import type { ChatJob } from "$lib/types";

const POLL_INTERVAL_MS = 3000;
const IDLE_POLL_INTERVAL_MS = 7000;
const HIDDEN_POLL_INTERVAL_MS = 15000;
const MAX_POLL_BACKOFF_MS = 30000;
const CHAT_JOBS_RUNTIME_KEY = "__textql_chat_jobs_runtime__";

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

interface ChatJobsRuntime {
  timer: ReturnType<typeof setTimeout> | null;
  started: boolean;
  pollInFlight: boolean;
  backoffMs: number;
}

function getRuntime(): ChatJobsRuntime {
  const globalScope = globalThis as typeof globalThis & {
    [CHAT_JOBS_RUNTIME_KEY]?: ChatJobsRuntime;
  };
  const existing = globalScope[CHAT_JOBS_RUNTIME_KEY];
  if (existing) {
    return existing;
  }
  const created: ChatJobsRuntime = {
    timer: null,
    started: false,
    pollInFlight: false,
    backoffMs: POLL_INTERVAL_MS,
  };
  globalScope[CHAT_JOBS_RUNTIME_KEY] = created;
  return created;
}

function createChatJobsStore() {
  const { subscribe, update, set } = writable<ChatJobsState>({
    jobsById: {},
    toasts: [],
    lastUpdatedAt: null,
    error: null,
  });

  const runtime = getRuntime();
  let hasInitialSnapshot = false;
  const notifiedJobIds = new Set<string>();

  function reset() {
    if (runtime.timer) {
      clearTimeout(runtime.timer);
      runtime.timer = null;
    }
    runtime.started = false;
    runtime.pollInFlight = false;
    runtime.backoffMs = POLL_INTERVAL_MS;
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

  function hydrateSnapshot(jobs: ChatJob[]): void {
    const nextById: Record<string, ChatJob> = {};
    for (const job of jobs) {
      nextById[job.id] = job;
    }
    update((state) => ({
      ...state,
      jobsById: nextById,
      lastUpdatedAt: new Date().toISOString(),
      error: null,
    }));
    hasInitialSnapshot = true;
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
    if (runtime.pollInFlight) {
      return;
    }

    if (
      typeof document !== "undefined" &&
      document.visibilityState === "hidden"
    ) {
      runtime.backoffMs = HIDDEN_POLL_INTERVAL_MS;
      return;
    }

    runtime.pollInFlight = true;
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

      const activeCount = response.jobs.filter(
        (job) => job.status === "queued" || job.status === "running",
      ).length;
      runtime.backoffMs =
        activeCount > 0 ? POLL_INTERVAL_MS : IDLE_POLL_INTERVAL_MS;
      hasInitialSnapshot = true;
    } catch (error) {
      update((state) => ({
        ...state,
        error: error instanceof Error ? error.message : "Failed to poll chat jobs",
      }));
      runtime.backoffMs = Math.min(runtime.backoffMs * 2, MAX_POLL_BACKOFF_MS);
    } finally {
      runtime.pollInFlight = false;
    }
  }

  async function pollAndSchedule(): Promise<void> {
    await poll();
    if (!runtime.started) {
      return;
    }
    if (runtime.timer) {
      clearTimeout(runtime.timer);
    }
    runtime.timer = setTimeout(() => {
      void pollAndSchedule();
    }, runtime.backoffMs);
  }

  function startPolling(): void {
    if (runtime.started) {
      return;
    }
    runtime.started = true;
    if (runtime.timer) {
      clearTimeout(runtime.timer);
      runtime.timer = null;
    }
    void pollAndSchedule();
  }

  function stopPolling(): void {
    runtime.started = false;
    if (runtime.timer) {
      clearTimeout(runtime.timer);
      runtime.timer = null;
    }
  }

  return {
    subscribe,
    startPolling,
    stopPolling,
    poll,
    reset,
    dismissToast,
    registerQueuedJob,
    hydrateSnapshot,
    getThreadSummary,
    getWorldlineQueueDepth,
  };
}

export const chatJobs = createChatJobsStore();
