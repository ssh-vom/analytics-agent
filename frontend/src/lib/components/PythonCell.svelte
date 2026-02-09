<script lang="ts">
  import CodeBlock from "$lib/components/CodeBlock.svelte";
  import type { TimelineEvent } from "$lib/types";
  import { readPythonResult } from "$lib/cells";

  export let callEvent: TimelineEvent | null = null;
  export let resultEvent: TimelineEvent | null = null;
  export let onBranch: (() => void) | null = null;
  let cellCollapsed = false;
  let codeCollapsed = false;
  let outputCollapsed = false;

  $: code =
    (callEvent?.payload?.code as string | undefined) ??
    "# code unavailable (result event arrived before call event)";
  $: result = readPythonResult(resultEvent);
  $: artifacts = result?.artifacts ?? [];
  $: imageArtifacts = artifacts.filter((artifact) => artifact.type === "image");
  $: fileArtifacts = artifacts.filter((artifact) => artifact.type !== "image");
  $: isRunning = Boolean(callEvent) && !resultEvent;
  $: statusLabel = isRunning
    ? "running"
    : result?.error
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
        aria-label={cellCollapsed ? "Expand Python cell" : "Collapse Python cell"}
      >
        {cellCollapsed ? "▸" : "▾"}
      </button>
      <strong>Python</strong>
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
        <span>Code</span>
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
          code={code}
          language="Python"
          animate={isRunning}
          placeholder="# waiting"
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
          {#if result.error}
            <p class="error">{result.error}</p>
          {/if}
          {#if result.stdout}
            <details open>
              <summary>stdout</summary>
              <pre class="plain">{result.stdout}</pre>
            </details>
          {/if}
          {#if result.stderr}
            <details>
              <summary>stderr</summary>
              <pre class="plain">{result.stderr}</pre>
            </details>
          {/if}
          {#if artifacts.length > 0}
            <section class="artifacts">
              {#if imageArtifacts.length > 0}
                <h4>Charts</h4>
                <ul class="chart-grid">
                  {#each imageArtifacts as artifact}
                    <li class="chart-card">
                      <img src={`/api/artifacts/${artifact.artifact_id}`} alt={artifact.name} />
                      <a href={`/api/artifacts/${artifact.artifact_id}`} target="_blank" rel="noreferrer">
                        {artifact.name}
                      </a>
                    </li>
                  {/each}
                </ul>
              {/if}

              {#if fileArtifacts.length > 0}
                <h4>Files</h4>
                <ul>
                  {#each fileArtifacts as artifact}
                    <li class="file-item">
                      <a href={`/api/artifacts/${artifact.artifact_id}`} target="_blank" rel="noreferrer">
                        {artifact.name}
                      </a>
                    </li>
                  {/each}
                </ul>
              {/if}
            </section>
          {/if}
        {:else}
          <p class="meta">Running python...</p>
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
    color: var(--accent-cyan);
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

  .plain {
    margin-top: 8px;
    margin-bottom: 0;
  }

  .meta {
    margin: 0;
    color: var(--text-dim);
  }

  .error {
    color: var(--danger);
    margin: 0 2px;
  }

  details {
    margin-top: 8px;
    border: 1px solid var(--border-soft);
    border-radius: 10px;
    padding: 8px 10px;
    background: var(--surface-0);
  }

  summary {
    color: var(--text-muted);
    cursor: pointer;
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

  .artifacts {
    margin-top: 12px;
  }

  .artifacts h4 {
    margin: 0 0 8px;
    font-family: var(--font-heading);
    color: var(--text-muted);
    font-size: 13px;
  }

  ul {
    list-style: none;
    padding: 0;
    margin: 0;
    display: grid;
    gap: 10px;
  }

  li {
    display: grid;
    gap: 8px;
  }

  .chart-grid {
    grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  }

  .chart-card {
    border: 1px solid var(--border-soft);
    border-radius: 12px;
    padding: 10px;
    background: var(--surface-0);
  }

  .file-item {
    border: 1px solid var(--border-soft);
    border-radius: 8px;
    padding: 8px 10px;
    background: var(--surface-0);
  }

  img {
    max-width: 100%;
    border-radius: 10px;
    border: 1px solid var(--border-soft);
  }

  a {
    color: var(--accent-blue);
    text-decoration: none;
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
