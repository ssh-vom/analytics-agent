<script lang="ts">
  import { goto } from "$app/navigation";
  import { page } from "$app/stores";
  import { threads, activeThread, createNewThread, loadThread } from "$lib/stores/threads";
  import { chatJobs, type JobToast } from "$lib/stores/chatJobs";
  import { createWorldline, fetchThreadWorldlines } from "$lib/api/client";
  import { GitBranch } from "lucide-svelte";
  import { Database } from "lucide-svelte";
  import { FileSpreadsheet } from "lucide-svelte";
  import { Settings } from "lucide-svelte";
  import { Plus } from "lucide-svelte";
  import { ChevronDown } from "lucide-svelte";
  import { ChevronRight } from "lucide-svelte";
  import { MessageSquare } from "lucide-svelte";
  import { Zap } from "lucide-svelte";
  import { Columns } from "lucide-svelte";
  import { onDestroy, onMount } from "svelte";

  let threadsExpanded = true;

  function toggleThreads() {
    threadsExpanded = !threadsExpanded;
  }

  function formatRelativeTime(dateString: string): string {
    const date = new Date(dateString);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);

    if (diffMins < 1) return "just now";
    if (diffMins < 60) return `${diffMins}m ago`;
    if (diffHours < 24) return `${diffHours}h ago`;
    if (diffDays < 7) return `${diffDays}d ago`;
    return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }

  onMount(() => {
    threads.loadThreads();
    activeThread.loadFromStorage();
    chatJobs.startPolling();
  });

  onDestroy(() => {
    chatJobs.stopPolling();
  });

  function dismissJobToast(toastId: string): void {
    chatJobs.dismissToast(toastId);
  }

  async function openJobToast(toast: JobToast): Promise<void> {
    dismissJobToast(toast.id);

    if (toast.resultWorldlineId) {
      localStorage.setItem("textql_active_worldline", toast.resultWorldlineId);
    }
    await loadThread(toast.threadId);
    await goto("/chat");

    if (toast.resultWorldlineId) {
      window.dispatchEvent(
        new CustomEvent("textql:open-worldline", {
          detail: {
            threadId: toast.threadId,
            worldlineId: toast.resultWorldlineId,
          },
        }),
      );
    }
  }

  async function ensureThreadHasWorldline(threadId: string): Promise<string> {
    const existing = await fetchThreadWorldlines(threadId);
    if (existing.worldlines.length > 0) {
      return existing.worldlines[0].id;
    }

    const created = await createWorldline(threadId, "main");
    return created.worldline_id;
  }

  async function handleCreateNewThread() {
    try {
      const thread = await createNewThread();
      
      // Load the thread and navigate - worldline will be created lazily on first message
      await loadThread(thread.id);
      
      // Clear any stored worldline ID since we're starting fresh
      localStorage.removeItem("textql_active_worldline");
      
      // Navigate to chat page
      await goto("/chat");
    } catch (err) {
      console.error("Failed to create thread:", err);
      alert("Failed to create thread: " + (err instanceof Error ? err.message : "Unknown error"));
    }
  }

  async function handleThreadSelect(threadId: string): Promise<void> {
    await loadThread(threadId);
    // Don't ensure worldline here - let it be created lazily on first message
    // Just clear the stored worldline ID
    localStorage.removeItem("textql_active_worldline");
    await goto("/chat");
  }

  $: currentPath = $page.url.pathname;
  $: isConnectors = currentPath === "/connectors";
  $: isSettings = currentPath === "/settings";
  $: isWorldlines = currentPath === "/worldlines";
  $: isData = currentPath === "/data";
  $: isSchema = currentPath === "/schema";
</script>

<div class="app-layout">
  <aside class="sidebar">
    <div class="sidebar-header">
      <a href="/chat" class="logo">
        <Zap size={18} color="var(--accent-green)" />
        <span class="logo-text">AnalyticZ</span>
      </a>
    </div>

    <div class="sidebar-content">
      <div class="section">
        <button class="section-header" on:click={toggleThreads}>
          {#if threadsExpanded}
            <ChevronDown size={14} />
          {:else}
            <ChevronRight size={14} />
          {/if}
          <MessageSquare size={14} />
          <span class="section-title">Threads</span>
          <span class="section-count">({$threads.threads.length})</span>
        </button>

        {#if threadsExpanded}
          <div class="section-content">
            <div class="thread-list">
              {#each $threads.threads as thread}
                {@const threadJobSummary = chatJobs.getThreadSummary(thread.id)}
                <button
                  class="thread-card"
                  class:active={thread.id === $activeThread?.id}
                  on:click={() => handleThreadSelect(thread.id)}
                >
                  <div class="thread-info">
                    <span class="thread-name">{thread.name}</span>
                    <div class="thread-meta-row">
                      <span class="thread-meta">
                        {thread.messageCount} msgs · {formatRelativeTime(thread.lastActivity)}
                      </span>
                      {#if threadJobSummary.running > 0}
                        <span class="thread-job-pill running">
                          {threadJobSummary.running} running
                        </span>
                      {:else if threadJobSummary.queued > 0}
                        <span class="thread-job-pill queued">
                          {threadJobSummary.queued} queued
                        </span>
                      {/if}
                    </div>
                  </div>
                  {#if thread.id === $activeThread?.id}
                    <div class="active-indicator"></div>
                  {/if}
                </button>
              {/each}
            </div>

            <button class="new-thread-btn" on:click={handleCreateNewThread}>
              <Plus size={14} />
              <span>New Thread</span>
            </button>
          </div>
        {/if}
      </div>
    </div>

    <div class="sidebar-footer">
      <nav class="nav-menu">
        <a href="/worldlines" class="nav-item" class:active={isWorldlines}>
          <GitBranch size={16} />
          <span>Worldlines</span>
        </a>
        <a href="/data" class="nav-item" class:active={isData}>
          <FileSpreadsheet size={16} />
          <span>Data Sources</span>
        </a>
        <a href="/schema" class="nav-item" class:active={isSchema}>
          <Columns size={16} />
          <span>Schema</span>
        </a>
        <a href="/connectors" class="nav-item" class:active={isConnectors}>
          <Database size={16} />
          <span>Connectors</span>
        </a>
        <a href="/settings" class="nav-item" class:active={isSettings}>
          <Settings size={16} />
          <span>Settings</span>
        </a>
      </nav>

      <div class="version">v1.0.0-beta</div>
    </div>
  </aside>

  <main class="main-content">
    <slot />
  </main>
</div>

{#if $chatJobs.toasts.length > 0}
  <aside class="job-toast-stack" aria-live="polite">
    {#each $chatJobs.toasts as toast (toast.id)}
      <article class="job-toast" class:failed={toast.status === "failed"}>
        <div class="job-toast-copy">
          <strong>{toast.title}</strong>
          <p>{toast.message}</p>
        </div>
        <div class="job-toast-actions">
          <button type="button" class="toast-btn" on:click={() => openJobToast(toast)}>
            {toast.status === "completed" && toast.resultWorldlineId ? "Open Result" : "Open"}
          </button>
          <button
            type="button"
            class="toast-btn ghost"
            on:click={() => dismissJobToast(toast.id)}
          >
            Dismiss
          </button>
        </div>
      </article>
    {/each}
  </aside>
{/if}

<style>
  .app-layout {
    display: grid;
    grid-template-columns: var(--sidebar-width) 1fr;
    height: 100vh;
    overflow: hidden;
  }

  .sidebar {
    display: flex;
    flex-direction: column;
    background: var(--bg-1);
    border-right: 1px solid var(--border-soft);
    overflow: hidden;
  }

  .sidebar-header {
    padding: var(--space-4) var(--space-4) var(--space-3);
    border-bottom: 1px solid var(--border-soft);
  }

  .logo {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    text-decoration: none;
    color: var(--text-primary);
  }

  .logo-text {
    font-family: var(--font-heading);
    font-size: 18px;
    font-weight: 400;
    letter-spacing: 0.02em;
  }

  .sidebar-content {
    flex: 1;
    overflow: hidden;
    padding: var(--space-4);
  }

  .section {
    margin-bottom: var(--space-5);
  }

  .section-header {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    width: 100%;
    padding: var(--space-3) var(--space-2);
    background: transparent;
    border: none;
    border-radius: var(--radius-sm);
    color: var(--text-muted);
    font-family: var(--font-body);
    font-size: 13px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    cursor: pointer;
    transition: color var(--transition-fast);
  }

  .section-header:hover {
    color: var(--text-secondary);
  }

  .section-title {
    flex: 1;
    text-align: left;
  }

  .section-count {
    color: var(--text-dim);
    font-size: 12px;
  }

  .section-content {
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding-top: var(--space-1);
    min-height: 0;
  }

  .thread-list {
    display: flex;
    flex-direction: column;
    gap: 2px;
    overflow-y: auto;
    max-height: min(56vh, calc(100vh - 260px));
    padding-right: 2px;
  }

  .thread-card {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: var(--space-3) var(--space-3);
    background: transparent;
    border: none;
    border-radius: var(--radius-sm);
    cursor: pointer;
    transition: background var(--transition-fast);
    text-align: left;
  }

  .thread-card:hover {
    background: var(--surface-hover);
  }

  .thread-card.active {
    background: var(--surface-active);
  }

  .thread-info {
    display: flex;
    flex-direction: column;
    gap: 1px;
    min-width: 0;
  }

  .thread-name {
    color: var(--text-primary);
    font-size: 14px;
    font-weight: 500;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .thread-meta {
    color: var(--text-dim);
    font-size: 12px;
  }

  .thread-meta-row {
    display: flex;
    align-items: center;
    gap: var(--space-2);
  }

  .thread-job-pill {
    display: inline-flex;
    align-items: center;
    padding: 1px 6px;
    border-radius: var(--radius-sm);
    font-size: 10px;
    font-family: var(--font-mono);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    border: 1px solid var(--border-soft);
    color: var(--text-dim);
  }

  .thread-job-pill.running {
    color: var(--accent-orange);
    border-color: var(--accent-orange-muted);
    background: color-mix(in srgb, var(--accent-orange-muted) 45%, transparent);
  }

  .thread-job-pill.queued {
    color: var(--text-secondary);
    border-color: var(--border-medium);
    background: var(--surface-1);
  }

  .active-indicator {
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: var(--accent-green);
    flex-shrink: 0;
  }

  .new-thread-btn {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-3) var(--space-3);
    margin-top: var(--space-2);
    background: transparent;
    border: 1px dashed var(--border-medium);
    border-radius: var(--radius-sm);
    color: var(--text-muted);
    font-size: 13px;
    cursor: pointer;
    transition: all var(--transition-fast);
  }

  .new-thread-btn:hover {
    background: var(--surface-hover);
    border-color: var(--text-dim);
    color: var(--text-secondary);
  }

  .sidebar-footer {
    padding: var(--space-4);
    border-top: 1px solid var(--border-soft);
  }

  .nav-menu {
    display: flex;
    flex-direction: column;
    gap: 1px;
    margin-bottom: var(--space-4);
  }

  .nav-item {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    padding: var(--space-3) var(--space-3);
    color: var(--text-muted);
    text-decoration: none;
    border-radius: var(--radius-sm);
    font-size: 14px;
    transition: all var(--transition-fast);
  }

  .nav-item:hover {
    color: var(--text-secondary);
    background: var(--surface-hover);
  }

  .nav-item.active {
    color: var(--accent-green);
    background: var(--accent-green-muted);
  }

  .version {
    color: var(--text-dim);
    font-size: 12px;
    font-family: var(--font-mono);
    text-align: center;
  }

  .main-content {
    overflow: hidden;
    height: 100vh;
    overscroll-behavior: none;
  }

  .job-toast-stack {
    position: fixed;
    right: var(--space-4);
    bottom: var(--space-4);
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    width: min(360px, calc(100vw - 24px));
    z-index: 150;
  }

  .job-toast {
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    background: var(--bg-1);
    box-shadow: var(--shadow-sm);
    padding: var(--space-3);
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }

  .job-toast.failed {
    border-color: var(--danger);
  }

  .job-toast-copy {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
  }

  .job-toast-copy strong {
    font-size: 12px;
    color: var(--text-primary);
  }

  .job-toast-copy p {
    margin: 0;
    color: var(--text-dim);
    font-size: 12px;
    line-height: 1.35;
  }

  .job-toast-actions {
    display: flex;
    gap: var(--space-2);
  }

  .toast-btn {
    border: 1px solid var(--accent-green);
    border-radius: var(--radius-sm);
    background: var(--accent-green-muted);
    color: var(--accent-green);
    font-size: 11px;
    padding: 4px 8px;
    cursor: pointer;
  }

  .toast-btn.ghost {
    border-color: var(--border-soft);
    background: transparent;
    color: var(--text-dim);
  }

  .toast-btn:hover {
    border-color: var(--border-medium);
    color: var(--text-primary);
  }

  @media (max-width: 768px) {
    .app-layout {
      grid-template-columns: 1fr;
    }

    .sidebar {
      display: none;
    }

    .job-toast-stack {
      right: var(--space-2);
      left: var(--space-2);
      width: auto;
      bottom: var(--space-2);
    }
  }
</style>
