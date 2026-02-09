import { writable } from "svelte/store";
import type { Thread } from "$lib/types";
import { createThread, fetchThreads } from "$lib/api/client";

interface ThreadsState {
  threads: Thread[];
  loading: boolean;
  error: string | null;
}

function createThreadsStore() {
  const { subscribe, update } = writable<ThreadsState>({
    threads: [],
    loading: false,
    error: null,
  });

  return {
    subscribe,
    
    loadThreads: async () => {
      update((s) => ({ ...s, loading: true, error: null }));
      try {
        const response = await fetchThreads();
        const mapped = response.threads.map((thread) => ({
          id: thread.id,
          name: thread.title || "New Thread",
          createdAt: thread.created_at,
          lastActivity: thread.last_activity,
          messageCount: thread.message_count,
        }));
        update((s) => ({ ...s, threads: mapped, loading: false }));
      } catch (err) {
        update((s) => ({
          ...s,
          loading: false,
          error: err instanceof Error ? err.message : "Failed to load threads",
        }));
      }
    },

    saveThreads: (newThreads: Thread[]) => {
      update((s) => ({ ...s, threads: newThreads }));
    },

    addThread: async (name: string = "New Chat") => {
      try {
        // Create thread via API
        const result = await createThread(name);
        const newThread: Thread = {
          id: result.thread_id,
          name,
          createdAt: new Date().toISOString(),
          lastActivity: new Date().toISOString(),
          messageCount: 0,
        };
        
        update((s) => {
          const threads = [newThread, ...s.threads];
          return { ...s, threads, loading: false, error: null };
        });
        
        return newThread;
      } catch (err) {
        update((s) => ({
          ...s,
          loading: false,
          error: err instanceof Error ? err.message : "Failed to create thread",
        }));
        throw err;
      }
    },

    addThreadLocal: (thread: Thread) => {
      update((s) => {
        const threads = [thread, ...s.threads];
        return { ...s, threads, loading: false, error: null };
      });
    },

    updateThread: (id: string, updates: Partial<Thread>) => {
      update((s) => {
        const threads = s.threads.map((t) =>
          t.id === id ? { ...t, ...updates } : t
        );
        return { ...s, threads };
      });
    },

    deleteThread: (id: string) => {
      update((s) => {
        const threads = s.threads.filter((t) => t.id !== id);
        return { ...s, threads };
      });
    },
  };
}

export const threads = createThreadsStore();

// Active thread store
function createActiveThreadStore() {
  const { subscribe, set } = writable<Thread | null>(null);

  return {
    subscribe,
    set,
    loadFromStorage: () => {
      const saved = localStorage.getItem("textql_active_thread");
      if (saved) {
        set(JSON.parse(saved));
      }
    },
    saveToStorage: (thread: Thread | null) => {
      if (thread) {
        localStorage.setItem("textql_active_thread", JSON.stringify(thread));
      } else {
        localStorage.removeItem("textql_active_thread");
      }
    },
  };
}

export const activeThread = createActiveThreadStore();

// Helper function to create a new thread and make it active
export async function createNewThread(name?: string) {
  const thread = await threads.addThread(name);
  activeThread.set(thread);
  activeThread.saveToStorage(thread);
  return thread;
}

// Helper function to load a thread and make it active
export async function loadThread(threadId: string) {
  let found: Thread | undefined;

  const unsubscribe = threads.subscribe((state) => {
    found = state.threads.find((t) => t.id === threadId);
  });
  unsubscribe();

  if (!found) {
    await threads.loadThreads();
    const retryUnsubscribe = threads.subscribe((state) => {
      found = state.threads.find((t) => t.id === threadId);
    });
    retryUnsubscribe();
  }

  if (found) {
    activeThread.set(found);
    activeThread.saveToStorage(found);
    
    // Update last activity
    threads.updateThread(threadId, {
      lastActivity: new Date().toISOString(),
    });
  }
}
