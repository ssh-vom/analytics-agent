<script lang="ts">
  import CodeBlock from "$lib/components/CodeBlock.svelte";
  import ResultTable from "$lib/components/ResultTable.svelte";
  import ToolCellHeader from "$lib/components/ToolCellHeader.svelte";
  import type { TimelineEvent } from "$lib/types";
  import { readSqlResult } from "$lib/cells";
  import { Database } from "lucide-svelte";
  import { AlertCircle } from "lucide-svelte";
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
  $: isSkipped = Boolean(callEvent?.payload?.skipped);
  $: skipReason = typeof callEvent?.payload?.skip_reason === "string" ? callEvent.payload.skip_reason : undefined;
  $: statusLabel = isSkipped
    ? "skipped"
    : isRunning
      ? "running"
      : hasError
        ? "error"
        : result
          ? "done"
          : "queued";
</script>

<article class="sql-cell message-entrance">
  <ToolCellHeader
    bind:collapsed={cellCollapsed}
    title="SQL Query"
    expandAriaLabel="Expand SQL cell"
    collapseAriaLabel="Collapse SQL cell"
    {statusLabel}
    {skipReason}
    executionMs={result?.execution_ms}
    {onBranch}
    accentColor="var(--accent-orange)"
  >
    <svelte:fragment slot="icon">
      <Database size={16} />
    </svelte:fragment>

    <svelte:fragment slot="collapsed-meta">
      {#if result && result.row_count !== undefined}
        <div class="row-count-badge">
          <Database size={12} />
          <span>{result.row_count} rows</span>
        </div>
      {/if}
    </svelte:fragment>
  </ToolCellHeader>

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
    transition: border-color var(--transition-normal), box-shadow var(--transition-normal);
    flex-shrink: 0;
  }

  .sql-cell:hover {
    border-color: var(--border-medium);
    box-shadow: var(--shadow-sm);
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
    padding: var(--space-2) var(--space-4);
    background: var(--bg-1);
  }

  .section-title {
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--text-dim);
  }

  .section-toggle {
    padding: 3px var(--space-3);
    background: transparent;
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    color: var(--text-dim);
    font-size: 10px;
    cursor: pointer;
    transition: all var(--transition-fast);
  }

  .section-toggle:hover {
    color: var(--text-secondary);
    border-color: var(--border-medium);
    background: var(--surface-hover);
  }

  .code-wrapper {
    padding: 0;
  }

  .code-wrapper :global(.code-block) {
    border: none;
    border-radius: 0;
  }

  .output-content {
    padding: var(--space-4);
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
    .row-count-badge {
      display: none;
    }
  }
</style>
