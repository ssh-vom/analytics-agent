<script lang="ts">
  import { GitBranch } from "lucide-svelte";
  import { Clock } from "lucide-svelte";
  import { ArrowRight } from "lucide-svelte";

  interface Worldline {
    id: string;
    name: string;
    parentId: string | null;
    createdAt: string;
    messageCount: number;
    isActive: boolean;
  }

  // Sample worldlines - in real app would come from API
  let worldlines: Worldline[] = [
    {
      id: "wl-001",
      name: "main",
      parentId: null,
      createdAt: new Date().toISOString(),
      messageCount: 42,
      isActive: true,
    },
    {
      id: "wl-002",
      name: "branch-1",
      parentId: "wl-001",
      createdAt: new Date(Date.now() - 86400000).toISOString(),
      messageCount: 15,
      isActive: false,
    },
    {
      id: "wl-003",
      name: "branch-2",
      parentId: "wl-001",
      createdAt: new Date(Date.now() - 172800000).toISOString(),
      messageCount: 8,
      isActive: false,
    },
  ];

  function formatDate(dateString: string): string {
    const date = new Date(dateString);
    return date.toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
      year: "numeric",
    });
  }

  function getBranchDepth(worldline: Worldline): number {
    if (!worldline.parentId) return 0;
    const parent = worldlines.find(w => w.id === worldline.parentId);
    if (!parent) return 0;
    return 1 + getBranchDepth(parent);
  }
</script>

<div class="worldlines-page">
  <header class="page-header">
    <div class="header-content">
      <div>
        <h1>Worldlines</h1>
        <p class="subtitle">Explore and manage your analysis branches</p>
      </div>
    </div>
  </header>

  <main class="worldlines-content">
    <div class="worldlines-list">
      {#each worldlines as worldline}
        <div class="worldline-card" class:active={worldline.isActive}>
          <div class="worldline-indent" style="width: {getBranchDepth(worldline) * 24}px"></div>
          
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
                <span>{formatDate(worldline.createdAt)}</span>
                <span class="separator">·</span>
                <span>{worldline.messageCount} messages</span>
              </div>
            </div>
            
            {#if worldline.parentId}
              <div class="parent-info">
                <ArrowRight size={14} />
                <span>Branched from {worldlines.find(w => w.id === worldline.parentId)?.name || "unknown"}</span>
              </div>
            {/if}
          </div>
          
          <div class="worldline-actions">
            {#if !worldline.isActive}
              <button class="action-btn switch">Switch</button>
            {:else}
              <span class="current-label">Current</span>
            {/if}
          </div>
        </div>
      {/each}
    </div>

    <div class="worldlines-info">
      <h3>About Worldlines</h3>
      <p>
        Worldlines represent different branches of your analysis session. 
        You can create branches at any point to explore alternative queries 
        or analysis paths without losing your previous work.
      </p>
      <p>
        The "main" worldline is your primary session. Create branches when you 
        want to experiment with different approaches or answer follow-up questions 
        in isolation.
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
    max-width: 900px;
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

  .worldlines-content {
    padding: var(--space-6) var(--space-8);
    max-width: 900px;
    display: flex;
    flex-direction: column;
    gap: var(--space-8);
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
    background: linear-gradient(135deg, var(--surface-0) 0%, var(--accent-orange-muted) 100%);
  }

  .worldline-indent {
    flex-shrink: 0;
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
  }

  .action-btn {
    padding: var(--space-2) var(--space-3);
    background: var(--surface-1);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    color: var(--text-secondary);
    font-size: 13px;
    cursor: pointer;
    transition: all var(--transition-fast);
  }

  .action-btn:hover {
    background: var(--surface-hover);
    border-color: var(--border-medium);
    color: var(--text-primary);
  }

  .action-btn.switch {
    background: var(--accent-cyan-muted);
    border-color: var(--accent-cyan);
    color: var(--accent-cyan);
  }

  .action-btn.switch:hover {
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
