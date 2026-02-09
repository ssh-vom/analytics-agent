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

<article class="sql-cell">
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
    border-radius: var(--radius-lg);
    overflow: hidden;
    transition: all var(--transition-fast);
  }

  .sql-cell:hover {
    border-color: var(--border-medium);
    box-shadow: var(--shadow-sm);
  }

  .cell-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-3);
    padding: var(--space-3) var(--space-4);
    background: var(--surface-1);
    border-bottom: 1px solid var(--border-soft);
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
    width: 24px;
    height: 24px;
    background: transparent;
    border: none;
    color: var(--text-muted);
    cursor: pointer;
    transition: all var(--transition-fast);
    flex-shrink: 0;
    border-radius: var(--radius-sm);
  }

  .collapse-btn:hover {
    color: var(--text-primary);
    background: var(--surface-hover);
  }

  .collapse-btn.collapsed {
    color: var(--accent-orange);
    background: var(--accent-orange-muted);
  }

  .cell-icon {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    background: var(--accent-orange-muted);
    color: var(--accent-orange);
    border-radius: var(--radius-md);
    flex-shrink: 0;
  }

  .cell-title {
    font-family: var(--font-heading);
    font-size: 13px;
    font-weight: 500;
    color: var(--text-primary);
    flex-shrink: 0;
  }

  .expand-hint {
    font-size: 11px;
    color: var(--accent-orange);
    margin-left: var(--space-2);
    pointer-events: none;
  }

  .row-count-badge {
    display: flex;
    align-items: center;
    gap: var(--space-1);
    padding: 2px 8px;
    border-radius: var(--radius-sm);
    font-size: 11px;
    font-weight: 500;
    background: var(--accent-orange-muted);
    color: var(--accent-orange);
    border: 1px solid var(--accent-orange);
    flex-shrink: 0;
    margin-left: var(--space-2);
  }

  .status-badge {
    display: flex;
    align-items: center;
    gap: var(--space-1);
    padding: 2px 8px;
    border-radius: var(--radius-sm);
    font-size: 11px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    border: 1px solid transparent;
    flex-shrink: 0;
  }

  .status-badge.running {
    background: var(--accent-blue-muted);
    color: var(--accent-blue);
    border-color: var(--accent-blue);
  }

  .status-badge.done {
    background: var(--accent-cyan-muted);
    color: var(--accent-cyan);
    border-color: var(--accent-cyan);
  }

  .status-badge.error {
    background: var(--danger-muted);
    color: var(--danger);
    border-color: var(--danger);
  }

  .status-badge.queued {
    background: var(--surface-2);
    color: var(--text-dim);
    border-color: var(--border-soft);
  }

  .execution-time {
    display: flex;
    align-items: center;
    gap: var(--space-1);
    color: var(--text-dim);
    font-size: 12px;
    margin-left: auto;
  }

  .branch-btn {
    display: flex;
    align-items: center;
    gap: var(--space-1);
    padding: var(--space-1) var(--space-2);
    background: transparent;
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    color: var(--text-muted);
    font-size: 12px;
    cursor: pointer;
    transition: all var(--transition-fast);
    opacity: 0;
    flex-shrink: 0;
  }

  .sql-cell:hover .branch-btn {
    opacity: 1;
  }

  .branch-btn:hover {
    background: var(--surface-hover);
    border-color: var(--accent-orange);
    color: var(--accent-orange);
  }

  .cell-content {
    display: flex;
    flex-direction: column;
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
    padding: var(--space-2) var(--space-4);
    background: var(--bg-1);
  }

  .section-title {
    font-family: var(--font-heading);
    font-size: 11px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-muted);
  }

  .section-toggle {
    padding: 2px var(--space-2);
    background: var(--surface-0);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-sm);
    color: var(--text-muted);
    font-size: 11px;
    cursor: pointer;
    transition: all var(--transition-fast);
  }

  .section-toggle:hover {
    background: var(--surface-hover);
    border-color: var(--border-medium);
    color: var(--text-primary);
  }

  .code-wrapper {
    padding: var(--space-3) var(--space-4);
  }

  .output-content {
    padding: var(--space-3) var(--space-4);
  }

  .error-message {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-3);
    background: var(--danger-muted);
    border: 1px solid var(--danger);
    border-radius: var(--radius-md);
    color: var(--danger);
    font-size: 14px;
  }

  .result-stats {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    margin-bottom: var(--space-3);
    color: var(--text-dim);
    font-size: 12px;
  }

  .stat-divider {
    color: var(--border-medium);
  }

  .loading-state {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--space-3);
    padding: var(--space-8);
    color: var(--text-muted);
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
