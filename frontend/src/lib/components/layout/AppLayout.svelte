<script lang="ts">
  import { goto } from "$app/navigation";
  import { page } from "$app/stores";
  import { threads, activeThread, createNewThread, loadThread } from "$lib/stores/threads";
  import { createWorldline } from "$lib/api/client";
  import { Sidebar } from "lucide-svelte";
  import { GitBranch } from "lucide-svelte";
  import { Database } from "lucide-svelte";
  import { FileSpreadsheet } from "lucide-svelte";
  import { Settings } from "lucide-svelte";
  import { Plus } from "lucide-svelte";
  import { ChevronDown } from "lucide-svelte";
  import { ChevronRight } from "lucide-svelte";
  import { MessageSquare } from "lucide-svelte";
  import { Zap } from "lucide-svelte";
  import { onMount } from "svelte";

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
  });

  async function handleCreateNewThread() {
    try {
      const thread = await createNewThread();
      
      // Create a worldline for this thread
      const worldline = await createWorldline(thread.id, "main");
      
      // Store the worldline ID in localStorage so chat page can pick it up
      localStorage.setItem("textql_active_worldline", worldline.worldline_id);
      
      // Navigate to chat page
      await goto("/chat");
    } catch (err) {
      console.error("Failed to create thread:", err);
      alert("Failed to create thread: " + (err instanceof Error ? err.message : "Unknown error"));
    }
  }

  async function handleThreadSelect(threadId: string): Promise<void> {
    await loadThread(threadId);
    await goto("/chat");
  }

  $: currentPath = $page.url.pathname;
  $: isChat = currentPath === "/" || currentPath === "/chat";
  $: isConnectors = currentPath === "/connectors";
  $: isSettings = currentPath === "/settings";
  $: isWorldlines = currentPath === "/worldlines";
  $: isData = currentPath === "/data";
</script>

<div class="app-layout">
  <aside class="sidebar">
    <div class="sidebar-header">
      <a href="/chat" class="logo">
        <Zap class="logo-icon" size={20} />
        <span class="logo-text">TextQL</span>
      </a>
    </div>

    <div class="sidebar-content">
      <!-- Threads Section -->
      <div class="section">
        <button class="section-header" on:click={toggleThreads}>
          {#if threadsExpanded}
            <ChevronDown size={16} />
          {:else}
            <ChevronRight size={16} />
          {/if}
          <MessageSquare size={16} />
          <span class="section-title">Threads</span>
          <span class="section-count">({$threads.threads.length})</span>
        </button>

        {#if threadsExpanded}
          <div class="section-content">
            <div class="thread-list">
              {#each $threads.threads as thread}
                <button
                  class="thread-card"
                  class:active={thread.id === $activeThread?.id}
                  on:click={() => handleThreadSelect(thread.id)}
                >
                  <div class="thread-info">
                    <span class="thread-name">{thread.name}</span>
                    <span class="thread-meta">
                      {thread.messageCount} msgs · {formatRelativeTime(thread.lastActivity)}
                    </span>
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
          <GitBranch size={18} />
          <span>Worldlines</span>
        </a>
        <a href="/data" class="nav-item" class:active={isData}>
          <FileSpreadsheet size={18} />
          <span>Data Sources</span>
        </a>
        <a href="/connectors" class="nav-item" class:active={isConnectors}>
          <Database size={18} />
          <span>Connectors</span>
        </a>
        <a href="/settings" class="nav-item" class:active={isSettings}>
          <Settings size={18} />
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

<style>
  .app-layout {
    display: grid;
    grid-template-columns: var(--sidebar-width) 1fr;
    height: 100vh;
    background: var(--bg-0);
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

  .logo-icon {
    color: var(--accent-orange);
  }

  .logo-text {
    font-family: var(--font-heading);
    font-size: 20px;
    font-weight: 500;
    letter-spacing: -0.02em;
  }

  .sidebar-content {
    flex: 1;
    overflow: hidden;
    padding: var(--space-3);
  }

  .section {
    margin-bottom: var(--space-4);
  }

  .section-header {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    width: 100%;
    padding: var(--space-2) var(--space-3);
    background: transparent;
    border: none;
    border-radius: var(--radius-md);
    color: var(--text-secondary);
    font-family: var(--font-body);
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    transition: all var(--transition-fast);
  }

  .section-header:hover {
    background: var(--surface-hover);
    color: var(--text-primary);
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
    gap: var(--space-1);
    padding: var(--space-1) 0;
    min-height: 0;
  }

  .thread-list {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
    overflow-y: auto;
    max-height: min(56vh, calc(100vh - 260px));
    padding-right: 2px;
  }

  .thread-card {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: var(--space-3);
    background: var(--surface-0);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-lg);
    cursor: pointer;
    transition: all var(--transition-fast);
    text-align: left;
  }

  .thread-card:hover {
    background: var(--surface-hover);
    border-color: var(--border-medium);
  }

  .thread-card.active {
    background: var(--surface-active);
    border-color: var(--border-accent);
  }

  .thread-info {
    display: flex;
    flex-direction: column;
    gap: 2px;
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

  .active-indicator {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--accent-orange);
    flex-shrink: 0;
  }

  .new-thread-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--space-2);
    padding: var(--space-3);
    background: transparent;
    border: 1px dashed var(--border-medium);
    border-radius: var(--radius-lg);
    color: var(--text-secondary);
    font-size: 13px;
    cursor: pointer;
    transition: all var(--transition-fast);
  }

  .new-thread-btn:hover {
    background: var(--surface-hover);
    border-color: var(--accent-orange);
    color: var(--text-primary);
  }

  .sidebar-footer {
    padding: var(--space-3);
    border-top: 1px solid var(--border-soft);
  }

  .nav-menu {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
    margin-bottom: var(--space-4);
  }

  .nav-item {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    padding: var(--space-2) var(--space-3);
    color: var(--text-secondary);
    text-decoration: none;
    border-radius: var(--radius-md);
    font-size: 14px;
    transition: all var(--transition-fast);
  }

  .nav-item:hover {
    background: var(--surface-hover);
    color: var(--text-primary);
  }

  .nav-item.active {
    background: var(--accent-orange-muted);
    color: var(--accent-orange);
  }

  .version {
    color: var(--text-dim);
    font-size: 11px;
    text-align: center;
  }

  .main-content {
    overflow: hidden;
    background: var(--bg-0);
    height: 100vh;
    overscroll-behavior: none;
  }

  @media (max-width: 768px) {
    .app-layout {
      grid-template-columns: 1fr;
    }

    .sidebar {
      display: none;
    }
  }
</style>
