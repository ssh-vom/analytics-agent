<script lang="ts">
  import { goto } from "$app/navigation";
  import { onMount } from "svelte";

  import {
    branchWorldline,
    fetchThreadWorldlineSummaries,
  } from "$lib/api/client";
  import { activeThread, threads } from "$lib/stores/threads";
  import type { Thread } from "$lib/types";
  import { AlertCircle, ArrowRight, Clock, GitBranch, Loader2 } from "lucide-svelte";

  interface WorldlineView {
    id: string;
    name: string;
    parentId: string | null;
    createdAt: string;
    lastActivity: string;
    messageCount: number;
    activeJobs: number;
    isActive: boolean;
    headEventId: string | null;
  }

  let threadId = "";
  let activeWorldlineId = "";
  let worldlines: WorldlineView[] = [];
  let loading = true;
  let errorMessage = "";
  let statusText = "";
  let branchingWorldlineId: string | null = null;
  let switchingWorldlineId: string | null = null;

  onMount(async () => {
    await threads.loadThreads();
    activeThread.loadFromStorage();

    if (!$activeThread) {
      const firstThread = getFirstThread();
      if (firstThread) {
        activeThread.set(firstThread);
        activeThread.saveToStorage(firstThread);
      }
    }

    if ($activeThread?.id) {
      await loadWorldlinesForThread($activeThread.id);
      return;
    }

    loading = false;
    errorMessage = "No thread selected. Create or open a thread first.";
  });

  $: if ($activeThread?.id && $activeThread.id !== threadId && !loading) {
    void loadWorldlinesForThread($activeThread.id);
  }

  function getFirstThread(): Thread | null {
    let first: Thread | null = null;
    const unsubscribe = threads.subscribe((state) => {
      first = state.threads[0] ?? null;
    });
    unsubscribe();
    return first;
  }

  async function loadWorldlinesForThread(targetThreadId: string): Promise<void> {
    loading = true;
    errorMessage = "";
    statusText = "";
    threadId = targetThreadId;

    try {
      const response = await fetchThreadWorldlineSummaries(targetThreadId);
      const rawWorldlines = response.worldlines;

      if (rawWorldlines.length === 0) {
        worldlines = [];
        activeWorldlineId = "";
        return;
      }

      const preferred = localStorage.getItem("textql_active_worldline");
      const preferredValid =
        typeof preferred === "string" &&
        rawWorldlines.some((line) => line.id === preferred);

      if (preferredValid) {
        activeWorldlineId = preferred as string;
      } else if (!rawWorldlines.some((line) => line.id === activeWorldlineId)) {
        activeWorldlineId = rawWorldlines[0].id;
      }

      worldlines = rawWorldlines.map((line) => ({
        id: line.id,
        name: line.name || line.id.slice(0, 12),
        parentId: line.parent_worldline_id,
        createdAt: line.created_at,
        lastActivity: line.last_activity,
        messageCount: line.message_count,
        activeJobs: (line.jobs.running ?? 0) + (line.jobs.queued ?? 0),
        isActive: line.id === activeWorldlineId,
        headEventId: line.head_event_id,
      }));
    } catch (error) {
      worldlines = [];
      errorMessage =
        error instanceof Error ? error.message : "Failed to load worldlines.";
    } finally {
      loading = false;
    }
  }

  function formatDate(dateString: string): string {
    const date = new Date(dateString);
    return date.toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
      year: "numeric",
    });
  }

  function getBranchDepth(worldline: WorldlineView): number {
    let depth = 0;
    let currentParentId = worldline.parentId;
    const seen = new Set<string>();

    while (currentParentId && !seen.has(currentParentId)) {
      seen.add(currentParentId);
      const parent = worldlines.find((line) => line.id === currentParentId);
      if (!parent) {
        break;
      }
      depth += 1;
      currentParentId = parent.parentId;
    }
    return depth;
  }

  function getParentName(worldline: WorldlineView): string {
    if (!worldline.parentId) {
      return "unknown";
    }
    return (
      worldlines.find((line) => line.id === worldline.parentId)?.name ?? "unknown"
    );
  }

  function nextBranchName(source: WorldlineView): string {
    const base = source.name || "branch";
    const siblingCount = worldlines.filter(
      (line) => line.parentId === source.id,
    ).length;
    return `${base}-branch-${siblingCount + 1}`;
  }

  async function switchToWorldline(worldlineId: string): Promise<void> {
    if (!$activeThread) {
      return;
    }

    switchingWorldlineId = worldlineId;
    activeWorldlineId = worldlineId;
    worldlines = worldlines.map((line) => ({
      ...line,
      isActive: line.id === worldlineId,
    }));
    localStorage.setItem("textql_active_worldline", worldlineId);
    activeThread.saveToStorage($activeThread);

    try {
      await goto("/chat");
    } finally {
      switchingWorldlineId = null;
    }
  }

  async function branchFromWorldline(source: WorldlineView): Promise<void> {
    if (!source.headEventId || !threadId) {
      return;
    }

    branchingWorldlineId = source.id;
    statusText = `Branching from ${source.name}...`;
    errorMessage = "";

    try {
      const result = await branchWorldline(
        source.id,
        source.headEventId,
        nextBranchName(source),
      );
      const newWorldlineId = result.new_worldline_id;
      activeWorldlineId = newWorldlineId;
      localStorage.setItem("textql_active_worldline", newWorldlineId);
      statusText = `Created ${newWorldlineId.slice(0, 12)} from ${source.name}`;

      await loadWorldlinesForThread(threadId);
    } catch (error) {
      errorMessage =
        error instanceof Error ? error.message : "Failed to create branch.";
      statusText = "";
    } finally {
      branchingWorldlineId = null;
    }
  }
</script>

<div class="worldlines-page">
  <header class="page-header">
    <div class="header-content">
      <div>
        <h1>Worldlines</h1>
        <p class="subtitle">Branch from any worldline, not just main.</p>
      </div>
      {#if statusText}
        <span class="status-chip">{statusText}</span>
      {/if}
    </div>
  </header>

  <main class="worldlines-content">
    {#if loading}
      <div class="loading-state">
        <Loader2 size={18} class="spin" />
        <span>Loading worldlines...</span>
      </div>
    {:else if errorMessage}
      <div class="error-state">
        <AlertCircle size={16} />
        <span>{errorMessage}</span>
      </div>
    {:else if worldlines.length === 0}
      <div class="empty-state">
        <GitBranch size={18} />
        <span>No worldlines found for this thread yet.</span>
      </div>
    {:else}
      <div class="worldlines-list">
        {#each worldlines as worldline (worldline.id)}
          <div class="worldline-card" class:active={worldline.isActive}>
            <div
              class="worldline-indent"
              style={`width: ${getBranchDepth(worldline) * 24}px`}
            ></div>

            <div class="worldline-main">
              <div class="worldline-header">
                <div class="worldline-info">
                  <GitBranch size={18} />
                  <span class="worldline-name">{worldline.name}</span>
                  {#if worldline.isActive}
                    <span class="active-badge">Active</span>
                  {/if}
                </div>

                <div class="worldline-meta">
                  <Clock size={14} />
                  <span>{formatDate(worldline.lastActivity)}</span>
                  <span class="separator">·</span>
                  <span>{worldline.messageCount} messages</span>
                  {#if worldline.activeJobs > 0}
                    <span class="separator">·</span>
                    <span>{worldline.activeJobs} active jobs</span>
                  {/if}
                </div>
              </div>

              {#if worldline.parentId}
                <div class="parent-info">
                  <ArrowRight size={14} />
                  <span>Branched from {getParentName(worldline)}</span>
                </div>
              {/if}
            </div>

            <div class="worldline-actions">
              <button
                class="action-btn branch"
                disabled={!worldline.headEventId || branchingWorldlineId === worldline.id}
                on:click={() => branchFromWorldline(worldline)}
                title={worldline.headEventId ? "Create a branch from this worldline head" : "Cannot branch from an empty worldline"}
              >
                {#if branchingWorldlineId === worldline.id}
                  <Loader2 size={14} class="spin" />
                  <span>Branching...</span>
                {:else}
                  <GitBranch size={14} />
                  <span>Branch</span>
                {/if}
              </button>

              {#if !worldline.isActive}
                <button
                  class="action-btn switch"
                  disabled={switchingWorldlineId === worldline.id}
                  on:click={() => switchToWorldline(worldline.id)}
                >
                  {#if switchingWorldlineId === worldline.id}
                    <Loader2 size={14} class="spin" />
                    <span>Opening...</span>
                  {:else}
                    <span>Switch</span>
                  {/if}
                </button>
              {:else}
                <span class="current-label">Current</span>
              {/if}
            </div>
          </div>
        {/each}
      </div>
    {/if}

    <div class="worldlines-info">
      <h3>Branching Rules</h3>
      <p>
        Branch creates a new child worldline from the selected worldline's current head event.
      </p>
      <p>
        If a worldline has no head event yet, branch is disabled until it has at least one event.
      </p>
    </div>
  </main>
</div>

<style>
  .worldlines-page {
    height: 100vh;
    overflow-y: auto;
    background: var(--bg-0);
  }

  .page-header {
    padding: var(--space-6) var(--space-8);
    border-bottom: 1px solid var(--border-soft);
    background: var(--surface-0);
  }

  .header-content {
    max-width: 980px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-3);
  }

  h1 {
    margin: 0 0 var(--space-2);
    font-family: var(--font-heading);
    font-size: 28px;
    font-weight: 500;
    color: var(--text-primary);
  }

  .subtitle {
    margin: 0;
    color: var(--text-muted);
    font-size: 15px;
  }

  .status-chip {
    font-size: 12px;
    color: var(--text-secondary);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-full);
    padding: 6px 10px;
    background: var(--surface-1);
    white-space: nowrap;
  }

  .worldlines-content {
    padding: var(--space-6) var(--space-8);
    max-width: 980px;
    display: flex;
    flex-direction: column;
    gap: var(--space-8);
  }

  .loading-state,
  .error-state,
  .empty-state {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-lg);
    background: var(--surface-0);
    color: var(--text-muted);
    padding: var(--space-4);
  }

  .error-state {
    border-color: var(--danger);
    color: var(--danger);
    background: var(--danger-muted);
  }

  .worldlines-list {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
  }

  .worldline-card {
    display: flex;
    align-items: flex-start;
    gap: var(--space-3);
    padding: var(--space-4);
    background: var(--surface-0);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-lg);
    transition: all var(--transition-fast);
  }

  .worldline-card:hover {
    border-color: var(--border-medium);
    box-shadow: var(--shadow-sm);
  }

  .worldline-card.active {
    border-color: var(--border-accent);
    background: linear-gradient(
      135deg,
      var(--surface-0) 0%,
      var(--accent-orange-muted) 100%
    );
  }

  .worldline-indent {
    flex-shrink: 0;
    min-height: 1px;
  }

  .worldline-main {
    flex: 1;
    min-width: 0;
  }

  .worldline-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-3);
    margin-bottom: var(--space-2);
  }

  .worldline-info {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    color: var(--text-secondary);
  }

  .worldline-name {
    font-family: var(--font-mono);
    font-size: 15px;
    color: var(--text-primary);
  }

  .active-badge {
    padding: 2px 8px;
    background: var(--accent-orange-muted);
    color: var(--accent-orange);
    font-size: 11px;
    font-weight: 600;
    border-radius: var(--radius-sm);
    text-transform: uppercase;
  }

  .worldline-meta {
    display: flex;
    align-items: center;
    gap: var(--space-1);
    color: var(--text-dim);
    font-size: 13px;
  }

  .separator {
    color: var(--border-medium);
  }

  .parent-info {
    display: flex;
    align-items: center;
    gap: var(--space-1);
    color: var(--text-dim);
    font-size: 12px;
    margin-top: var(--space-1);
  }

  .worldline-actions {
    flex-shrink: 0;
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
  }

  .action-btn {
    padding: var(--space-2) var(--space-3);
    background: var(--surface-1);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    color: var(--text-secondary);
    font-size: 13px;
    display: inline-flex;
    align-items: center;
    gap: var(--space-1);
    cursor: pointer;
    transition: all var(--transition-fast);
  }

  .action-btn:hover:not(:disabled) {
    background: var(--surface-hover);
    border-color: var(--border-medium);
    color: var(--text-primary);
  }

  .action-btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .action-btn.switch {
    background: var(--accent-cyan-muted);
    border-color: var(--accent-cyan);
    color: var(--accent-cyan);
  }

  .action-btn.switch:hover:not(:disabled) {
    background: var(--accent-cyan);
    color: #111;
  }

  .current-label {
    font-size: 13px;
    color: var(--accent-orange);
    font-weight: 500;
  }

  .worldlines-info {
    padding: var(--space-5);
    background: var(--surface-0);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-lg);
  }

  .worldlines-info h3 {
    margin: 0 0 var(--space-3);
    font-size: 16px;
    font-weight: 600;
    color: var(--text-primary);
  }

  .worldlines-info p {
    margin: 0 0 var(--space-3);
    color: var(--text-muted);
    font-size: 14px;
    line-height: 1.6;
  }

  .worldlines-info p:last-child {
    margin-bottom: 0;
  }

</style>
