import {
  fetchThreadWorldlines,
  fetchWorldlineEvents,
  branchWorldline,
  createWorldline,
} from "$lib/api/client";
import type { TimelineEvent } from "$lib/types";

export type WorldlineManagerContext = {
  threadId: string | null;
  getActiveWorldlineId: () => string;
  getWorldlines: () => unknown[];
  getEventsByWorldline: () => Record<string, TimelineEvent[]>;
  setWorldlines: (worldlines: unknown[]) => void;
  setActiveWorldlineId: (id: string) => void;
  persistPreferredWorldline: (id: string) => void;
  setWorldlineEvents: (worldlineId: string, events: TimelineEvent[]) => void;
  onStatusChange: (status: string) => void;
  onScroll: () => void;
  refreshContextTables: () => Promise<void>;
};

export function createWorldlineManager(context: WorldlineManagerContext) {
  const {
    getActiveWorldlineId,
    getWorldlines,
    getEventsByWorldline,
    setWorldlines,
    setActiveWorldlineId,
    persistPreferredWorldline,
    setWorldlineEvents,
    onStatusChange,
    onScroll,
    refreshContextTables,
  } = context;

  function getThreadId(): string | null {
    return context.threadId;
  }

  function mergeFetchedWithOptimistic(
    fetched: TimelineEvent[],
    existing: TimelineEvent[],
  ): TimelineEvent[] {
    if (existing.length === 0) {
      return fetched;
    }
    const fetchedIds = new Set(fetched.map((event) => event.id));
    const optimisticTail = existing.filter(
      (event) =>
        event.id.startsWith("optimistic:") && !fetchedIds.has(event.id),
    );
    if (optimisticTail.length === 0) {
      return fetched;
    }
    return [...fetched, ...optimisticTail];
  }

  async function refreshWorldlines(): Promise<void> {
    const threadId = getThreadId();
    if (!threadId) {
      return;
    }
    const response = await fetchThreadWorldlines(threadId);
    setWorldlines(response.worldlines);
  }

  async function loadWorldline(worldlineId: string): Promise<void> {
    const fetchedEvents = await fetchWorldlineEvents(worldlineId);
    const existingEvents = getEventsByWorldline()[worldlineId] ?? [];
    const mergedEvents = mergeFetchedWithOptimistic(fetchedEvents, existingEvents);
    setWorldlineEvents(worldlineId, mergedEvents);
    onScroll();
  }

  async function selectWorldline(worldlineId: string): Promise<void> {
    setActiveWorldlineId(worldlineId);
    persistPreferredWorldline(worldlineId);

    if (!getEventsByWorldline()[worldlineId]) {
      await loadWorldline(worldlineId);
    }
    await refreshContextTables();
    onScroll();
  }

  async function branchFromEvent(eventId: string): Promise<string | null> {
    const activeWorldlineId = getActiveWorldlineId();
    const worldlines = getWorldlines();
    if (!activeWorldlineId || !eventId) {
      return null;
    }

    try {
      onStatusChange("Branching worldline...");
      const response = await branchWorldline(
        activeWorldlineId,
        eventId,
        `branch-${worldlines.length + 1}`,
      );
      const newWorldlineId = response.new_worldline_id;
      setActiveWorldlineId(newWorldlineId);
      persistPreferredWorldline(newWorldlineId);
      await refreshWorldlines();
      await loadWorldline(newWorldlineId);
      await refreshContextTables();
      onStatusChange("Branch created");
      return newWorldlineId;
    } catch (error) {
      onStatusChange(
        error instanceof Error ? error.message : "Branch failed"
      );
      return null;
    }
  }

  async function ensureWorldline(): Promise<string | null> {
    const activeWorldlineId = getActiveWorldlineId();
    // If we already have a worldline, use it
    if (activeWorldlineId) {
      return activeWorldlineId;
    }

    // If we have a thread but no worldline, create one lazily
    const threadId = getThreadId();
    if (threadId) {
      onStatusChange("Creating worldline...");
      try {
        const worldline = await createWorldline(threadId, "main");
        const newWorldlineId = worldline.worldline_id;
        setActiveWorldlineId(newWorldlineId);
        persistPreferredWorldline(newWorldlineId);
        await refreshWorldlines();
        onStatusChange("Ready");
        return newWorldlineId;
      } catch (error) {
        onStatusChange(
          error instanceof Error ? error.message : "Failed to create worldline"
        );
        return null;
      }
    }

    return null;
  }

  return {
    refreshWorldlines,
    loadWorldline,
    selectWorldline,
    branchFromEvent,
    ensureWorldline,
  };
}

export type WorldlineManager = ReturnType<typeof createWorldlineManager>;
