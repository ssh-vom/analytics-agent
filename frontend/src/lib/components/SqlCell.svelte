<script lang="ts">
  import CodeBlock from "$lib/components/CodeBlock.svelte";
  import ResultTable from "$lib/components/ResultTable.svelte";
  import type { TimelineEvent } from "$lib/types";
  import { readSqlResult } from "$lib/cells";

  export let callEvent: TimelineEvent | null = null;
  export let resultEvent: TimelineEvent | null = null;
  export let onBranch: (() => void) | null = null;
  let cellCollapsed = false;
  let codeCollapsed = false;
  let outputCollapsed = false;

  $: sql = (callEvent?.payload?.sql as string | undefined) ?? "-- waiting --";
  $: result = readSqlResult(resultEvent);
  $: hasError = Boolean(result?.error);
  $: isRunning = Boolean(callEvent) && !resultEvent;
  $: statusLabel = isRunning
    ? "running"
    : hasError
      ? "error"
      : result
        ? "done"
        : "queued";
</script>

<article class="cell">
  <header class="cell-header">
    <div class="left">
      <button
        type="button"
        class="toggle"
        on:click={() => (cellCollapsed = !cellCollapsed)}
        aria-label={cellCollapsed ? "Expand SQL cell" : "Collapse SQL cell"}
      >
        {cellCollapsed ? "▸" : "▾"}
      </button>
      <strong>SQL</strong>
      <span class={`status ${statusLabel}`}>
        <i></i>{statusLabel}
      </span>
    </div>
    {#if result?.execution_ms !== undefined}
      <span class="exec-time">{result.execution_ms}ms</span>
    {/if}
    {#if onBranch}
      <button type="button" class="branch" on:click={onBranch}>Branch from here</button>
    {/if}
  </header>

  {#if !cellCollapsed}
    <section class="section">
      <div class="section-header">
        <span>Query</span>
        <button
          type="button"
          class="section-toggle"
          on:click={() => (codeCollapsed = !codeCollapsed)}
        >
          {codeCollapsed ? "Show" : "Hide"}
        </button>
      </div>
      {#if !codeCollapsed}
        <CodeBlock
          code={sql}
          language="SQL"
          animate={isRunning}
          placeholder="-- waiting --"
        />
      {/if}
    </section>

    <section class="section">
      <div class="section-header">
        <span>Output</span>
        <button
          type="button"
          class="section-toggle"
          on:click={() => (outputCollapsed = !outputCollapsed)}
        >
          {outputCollapsed ? "Show" : "Hide"}
        </button>
      </div>
      {#if !outputCollapsed}
        {#if result}
          {#if hasError}
            <p class="error">{result?.error}</p>
          {:else}
            <p class="meta">
              {result?.preview_count ?? 0} preview rows / {result?.row_count ?? 0} total rows
            </p>
            <ResultTable
              columns={result?.columns ?? []}
              rows={result?.rows ?? []}
            />
          {/if}
        {:else}
          <p class="meta">Executing query...</p>
        {/if}
      {/if}
    </section>
  {/if}
</article>

<style>
  .cell {
    border: 1px solid var(--border-soft);
    border-radius: 12px;
    padding: 12px;
    background: var(--surface-1);
    display: grid;
    gap: 10px;
  }

  .cell-header {
    display: flex;
    gap: 8px;
    align-items: center;
  }

  .left {
    display: inline-flex;
    align-items: center;
    gap: 8px;
  }

  .toggle {
    width: 24px;
    height: 24px;
    border-radius: 7px;
    border: 1px solid var(--border-soft);
    color: var(--text-muted);
    background: var(--surface-0);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 0;
    font-size: 13px;
    line-height: 1;
  }

  strong {
    font-family: var(--font-heading);
    color: var(--accent-orange);
  }

  .exec-time {
    color: var(--text-dim);
    font-size: 12px;
  }

  .status {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--text-muted);
    border: 1px solid var(--border-soft);
    border-radius: 999px;
    padding: 2px 8px;
  }

  .status i {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--text-dim);
  }

  .status.running i {
    background: var(--accent-blue);
  }

  .status.done i {
    background: var(--accent-cyan);
  }

  .status.error i {
    background: var(--danger);
  }

  .meta {
    margin: 0;
    color: var(--text-dim);
    font-size: 13px;
  }

  .error {
    color: var(--danger);
    margin: 0 2px;
  }

  .section {
    border: 1px solid var(--border-soft);
    border-radius: 10px;
    background: rgb(255 255 255 / 1%);
    padding: 10px;
    display: grid;
    gap: 8px;
  }

  .section-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    color: var(--text-muted);
    font-family: var(--font-heading);
    font-size: 12px;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }

  .section-toggle {
    border: 1px solid var(--border-soft);
    background: var(--surface-0);
    color: var(--text-muted);
    border-radius: 8px;
    font-size: 11px;
    padding: 3px 8px;
    text-transform: none;
  }

  .branch {
    margin-left: auto;
    border: 1px solid var(--border-soft);
    background: transparent;
    color: var(--text-muted);
    border-radius: 8px;
    padding: 4px 8px;
    font-size: 12px;
  }
</style>
