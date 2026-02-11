<script lang="ts">
  import { ChevronDown } from "lucide-svelte";
  import { ChevronRight } from "lucide-svelte";
  import { GitBranch } from "lucide-svelte";
  import { Clock } from "lucide-svelte";
  import { AlertCircle } from "lucide-svelte";
  import { CheckCircle } from "lucide-svelte";
  import { Loader2 } from "lucide-svelte";

  export let collapsed = false;
  export let title = "";
  export let expandAriaLabel = "Expand tool cell";
  export let collapseAriaLabel = "Collapse tool cell";
  export let statusLabel: "running" | "error" | "done" | "queued" = "queued";
  export let executionMs: number | undefined;
  export let onBranch: (() => void) | null = null;
  export let accentColor = "var(--text-secondary)";
</script>

<header class="cell-header" style={`--tool-accent:${accentColor}`}>
  <div class="header-left">
    <button
      type="button"
      class="collapse-btn"
      class:collapsed={collapsed}
      on:click={() => (collapsed = !collapsed)}
      aria-label={collapsed ? expandAriaLabel : collapseAriaLabel}
    >
      {#if collapsed}
        <ChevronRight size={16} />
      {:else}
        <ChevronDown size={16} />
      {/if}
    </button>

    <div class="cell-icon">
      <slot name="icon" />
    </div>

    <span class="cell-title">{title}</span>

    {#if collapsed}
      <span class="expand-hint">Show content</span>
      <slot name="collapsed-meta" />
    {/if}

    <div class={`status-badge ${statusLabel}`}>
      {#if statusLabel === "running"}
        <Loader2 size={12} class="spin" />
      {:else if statusLabel === "error"}
        <AlertCircle size={12} />
      {:else if statusLabel === "done"}
        <CheckCircle size={12} />
      {/if}
      <span>{statusLabel}</span>
    </div>

    {#if executionMs !== undefined}
      <div class="execution-time">
        <Clock size={12} />
        <span>{executionMs}ms</span>
      </div>
    {/if}
  </div>

  {#if onBranch}
    <button type="button" class="branch-btn" on:click={onBranch}>
      <GitBranch size={12} />
      <span>Branch</span>
    </button>
  {/if}
</header>

<style>
  .cell-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-3);
    padding: var(--space-2) var(--space-3);
    border-bottom: 1px solid var(--border-soft);
    min-height: 40px;
    flex-shrink: 0;
  }

  .header-left {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    flex: 1;
    min-width: 0;
  }

  .collapse-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 22px;
    height: 22px;
    background: transparent;
    border: none;
    color: var(--text-dim);
    cursor: pointer;
    transition: color var(--transition-fast);
    flex-shrink: 0;
    border-radius: var(--radius-sm);
  }

  .collapse-btn:hover {
    color: var(--text-secondary);
  }

  .collapse-btn.collapsed {
    color: var(--tool-accent);
  }

  .cell-icon {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 24px;
    height: 24px;
    color: var(--tool-accent);
    flex-shrink: 0;
  }

  .cell-title {
    font-family: var(--font-heading);
    font-size: 12px;
    font-weight: 400;
    color: var(--text-secondary);
    flex-shrink: 0;
  }

  .expand-hint {
    font-size: 11px;
    color: var(--text-dim);
    margin-left: var(--space-2);
    pointer-events: none;
  }

  .status-badge {
    display: flex;
    align-items: center;
    gap: var(--space-1);
    padding: 1px 6px;
    border-radius: var(--radius-sm);
    font-size: 10px;
    font-weight: 500;
    font-family: var(--font-mono);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    flex-shrink: 0;
  }

  .status-badge.running {
    background: var(--accent-blue-muted);
    color: var(--accent-blue);
  }

  .status-badge.done {
    background: var(--accent-green-muted);
    color: var(--accent-green);
  }

  .status-badge.error {
    background: var(--danger-muted);
    color: var(--danger);
  }

  .status-badge.queued {
    color: var(--text-dim);
  }

  .execution-time {
    display: flex;
    align-items: center;
    gap: var(--space-1);
    color: var(--text-dim);
    font-size: 11px;
    font-family: var(--font-mono);
    margin-left: auto;
  }

  .branch-btn {
    display: flex;
    align-items: center;
    gap: var(--space-1);
    padding: 2px var(--space-2);
    background: transparent;
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-sm);
    color: var(--text-dim);
    font-size: 11px;
    cursor: pointer;
    transition: all var(--transition-fast);
    opacity: 0;
    flex-shrink: 0;
  }

  .cell-header:hover .branch-btn {
    opacity: 1;
  }

  .branch-btn:hover {
    border-color: var(--border-medium);
    color: var(--text-secondary);
  }

  .spin {
    animation: spin 1s linear infinite;
  }

  @keyframes spin {
    from {
      transform: rotate(0deg);
    }
    to {
      transform: rotate(360deg);
    }
  }

  @media (max-width: 768px) {
    .branch-btn {
      opacity: 1;
    }

    .execution-time {
      display: none;
    }
  }
</style>
