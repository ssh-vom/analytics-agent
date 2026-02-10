<script lang="ts">
  import CodeBlock from "$lib/components/CodeBlock.svelte";
  import ResultTable from "$lib/components/ResultTable.svelte";
  import type { TimelineEvent } from "$lib/types";
  import { readSqlResult } from "$lib/cells";
  import { Database } from "lucide-svelte";
  import { ChevronDown } from "lucide-svelte";
  import { ChevronRight } from "lucide-svelte";
  import { GitBranch } from "lucide-svelte";
  import { Clock } from "lucide-svelte";
  import { AlertCircle } from "lucide-svelte";
  import { CheckCircle } from "lucide-svelte";
  import { Loader2 } from "lucide-svelte";

  export let callEvent: TimelineEvent | null = null;
  export let resultEvent: TimelineEvent | null = null;
  export let onBranch: (() => void) | null = null;
  export let initialCollapsed: boolean = true;
  let cellCollapsed = initialCollapsed;
  let codeCollapsed = false;
  let outputCollapsed = false;

  $: sql = (callEvent?.payload?.sql as string | undefined) ?? "-- waiting --";
  $: result = readSqlResult(resultEvent);
  $: hasError = Boolean(result?.error);
  $: isRunning = Boolean(callEvent) && !resultEvent;
  $: isDraft = Boolean(callEvent) && !resultEvent;
  $: statusLabel = isRunning
    ? "running"
    : hasError
      ? "error"
      : result
        ? "done"
        : "queued";
</script>

<article class="sql-cell message-entrance">
  <header class="cell-header">
    <div class="header-left">
      <button
        type="button"
        class="collapse-btn"
        class:collapsed={cellCollapsed}
        on:click={() => (cellCollapsed = !cellCollapsed)}
        aria-label={cellCollapsed ? "Expand SQL cell" : "Collapse SQL cell"}
      >
        {#if cellCollapsed}
          <ChevronRight size={16} />
        {:else}
          <ChevronDown size={16} />
        {/if}
      </button>
      
      <div class="cell-icon">
        <Database size={16} />
      </div>
      
      <span class="cell-title">SQL Query</span>
      
      {#if cellCollapsed}
        <span class="expand-hint">Show content</span>
      {/if}

      {#if cellCollapsed && result && result.row_count !== undefined}
        <div class="row-count-badge">
          <Database size={12} />
          <span>{result.row_count} rows</span>
        </div>
      {/if}

      <div class="status-badge {statusLabel}">
        {#if statusLabel === "running"}
          <Loader2 size={12} class="spin" />
        {:else if statusLabel === "error"}
          <AlertCircle size={12} />
        {:else if statusLabel === "done"}
          <CheckCircle size={12} />
        {/if}
        <span>{statusLabel}</span>
      </div>
      
      {#if result?.execution_ms !== undefined}
        <div class="execution-time">
          <Clock size={12} />
          <span>{result.execution_ms}ms</span>
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

  {#if !cellCollapsed}
    <div class="cell-content">
      <section class="content-section">
        <div class="section-header">
          <span class="section-title">Query</span>
          <button
            type="button"
            class="section-toggle"
            on:click={() => (codeCollapsed = !codeCollapsed)}
          >
            {codeCollapsed ? "Show" : "Hide"}
          </button>
        </div>
        {#if !codeCollapsed}
          <div class="code-wrapper">
            <CodeBlock
              code={sql}
              language="SQL"
              animate={isDraft}
              placeholder="-- waiting --"
            />
          </div>
        {/if}
      </section>

      <section class="content-section">
        <div class="section-header">
          <span class="section-title">Results</span>
          <button
            type="button"
            class="section-toggle"
            on:click={() => (outputCollapsed = !outputCollapsed)}
          >
            {outputCollapsed ? "Show" : "Hide"}
          </button>
        </div>
        {#if !outputCollapsed}
          <div class="output-content">
            {#if result}
              {#if hasError}
                <div class="error-message">
                  <AlertCircle size={16} />
                  <span>{result?.error}</span>
                </div>
              {:else}
                <div class="result-stats">
                  <span class="stat">{result?.preview_count ?? 0} rows shown</span>
                  <span class="stat-divider">·</span>
                  <span class="stat">{result?.row_count ?? 0} total</span>
                  {#if result?.execution_ms}
                    <span class="stat-divider">·</span>
                    <span class="stat">{result.execution_ms}ms</span>
                  {/if}
                </div>
                <ResultTable
                  columns={result?.columns ?? []}
                  rows={result?.rows ?? []}
                />
              {/if}
            {:else}
              <div class="loading-state">
                <Loader2 size={20} class="spin" />
                <span>Executing query...</span>
              </div>
            {/if}
          </div>
        {/if}
      </section>
    </div>
  {/if}
</article>

<style>
  .sql-cell {
    background: var(--surface-0);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    overflow: hidden;
    transition: border-color var(--transition-fast);
    flex-shrink: 0;
  }

  .sql-cell:hover {
    border-color: var(--border-medium);
  }

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
    color: var(--accent-orange);
  }

  .cell-icon {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 24px;
    height: 24px;
    color: var(--accent-orange);
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

  .row-count-badge {
    display: flex;
    align-items: center;
    gap: var(--space-1);
    padding: 1px 6px;
    border-radius: var(--radius-sm);
    font-size: 11px;
    font-weight: 500;
    font-family: var(--font-mono);
    background: var(--accent-orange-muted);
    color: var(--accent-orange);
    flex-shrink: 0;
    margin-left: var(--space-1);
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

  .sql-cell:hover .branch-btn {
    opacity: 1;
  }

  .branch-btn:hover {
    border-color: var(--border-medium);
    color: var(--text-secondary);
  }

  .cell-content {
    display: flex;
    flex-direction: column;
    animation: messageFadeIn 0.25s cubic-bezier(0.4, 0, 0.2, 1) forwards;
  }

  .content-section {
    border-bottom: 1px solid var(--border-soft);
  }

  .content-section:last-child {
    border-bottom: none;
  }

  .section-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: var(--space-1) var(--space-3);
    background: var(--bg-1);
  }

  .section-title {
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--text-dim);
  }

  .section-toggle {
    padding: 1px var(--space-2);
    background: transparent;
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-sm);
    color: var(--text-dim);
    font-size: 10px;
    cursor: pointer;
    transition: color var(--transition-fast);
  }

  .section-toggle:hover {
    color: var(--text-secondary);
    border-color: var(--border-medium);
  }

  .code-wrapper {
    padding: var(--space-3);
  }

  .output-content {
    padding: var(--space-3);
  }

  .error-message {
    display: flex;
    align-items: flex-start;
    gap: var(--space-2);
    padding: var(--space-3);
    background: var(--danger-muted);
    border-radius: var(--radius-md);
    color: var(--danger);
    font-size: 13px;
  }

  .result-stats {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    margin-bottom: var(--space-3);
    color: var(--text-dim);
    font-size: 11px;
    font-family: var(--font-mono);
  }

  .stat-divider {
    color: var(--border-medium);
  }

  .loading-state {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--space-3);
    padding: var(--space-6);
    color: var(--text-dim);
    font-size: 13px;
  }

  .loading-state :global(.spin) {
    animation: spin 1s linear infinite;
  }

  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }

  @media (max-width: 640px) {
    .branch-btn {
      opacity: 1;
    }

    .execution-time {
      display: none;
    }
  }
</style>
