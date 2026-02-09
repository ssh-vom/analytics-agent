<script lang="ts">
  import { createEventDispatcher } from "svelte";
  import CodeBlock from "$lib/components/CodeBlock.svelte";
  import type { PythonArtifact, TimelineEvent } from "$lib/types";
  import { readPythonResult } from "$lib/cells";
  import { Terminal } from "lucide-svelte";
  import { ChevronDown } from "lucide-svelte";
  import { ChevronRight } from "lucide-svelte";
  import { GitBranch } from "lucide-svelte";
  import { Clock } from "lucide-svelte";
  import { AlertCircle } from "lucide-svelte";
  import { CheckCircle } from "lucide-svelte";
  import { Loader2 } from "lucide-svelte";
  import { Image } from "lucide-svelte";
  import { FileText } from "lucide-svelte";
  import { ExternalLink } from "lucide-svelte";

  export let callEvent: TimelineEvent | null = null;
  export let resultEvent: TimelineEvent | null = null;
  export let onBranch: (() => void) | null = null;
  export let showArtifacts = true;
  export let artifactLinkMode: "download" | "panel" = "download";
  export let initialCollapsed: boolean = true;
  let cellCollapsed = initialCollapsed;
  let codeCollapsed = false;
  let outputCollapsed = false;
  const dispatch = createEventDispatcher<{ artifactselect: { artifactId: string } }>();

  $: code =
    (callEvent?.payload?.code as string | undefined) ??
    "# code unavailable (result event arrived before call event)";
  $: result = readPythonResult(resultEvent);
  $: artifacts = result?.artifacts ?? [];
  $: imageArtifacts = artifacts.filter((artifact) => artifact.type === "image");
  $: fileArtifacts = artifacts.filter((artifact) => artifact.type !== "image");
  $: isRunning = Boolean(callEvent) && !resultEvent;
  $: isDraft = Boolean(callEvent) && !resultEvent;
  $: statusLabel = isRunning
    ? "running"
    : result?.error
      ? "error"
      : result
        ? "done"
        : "queued";

  function getArtifactTarget(): string | undefined {
    return artifactLinkMode === "download" ? "_blank" : undefined;
  }

  function getArtifactRel(): string | undefined {
    return artifactLinkMode === "download" ? "noreferrer" : undefined;
  }

  function handleArtifactClick(event: MouseEvent, artifact: PythonArtifact): void {
    if (artifactLinkMode !== "panel") {
      return;
    }
    event.preventDefault();
    dispatch("artifactselect", { artifactId: artifact.artifact_id });
  }
</script>

<article class="python-cell">
  <header class="cell-header">
    <div class="header-left">
      <button
        type="button"
        class="collapse-btn"
        class:collapsed={cellCollapsed}
        on:click={() => (cellCollapsed = !cellCollapsed)}
        aria-label={cellCollapsed ? "Expand Python cell" : "Collapse Python cell"}
      >
        {#if cellCollapsed}
          <ChevronRight size={16} />
        {:else}
          <ChevronDown size={16} />
        {/if}
      </button>
      
      <div class="cell-icon">
        <Terminal size={16} />
      </div>
      
      <span class="cell-title">Python</span>
      
      {#if cellCollapsed}
        <span class="expand-hint">Show content</span>
      {/if}

      {#if cellCollapsed && artifacts.length > 0}
        <div class="artifact-badge">
          <Image size={12} />
          <span>{artifacts.length}</span>
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
          <span class="section-title">Code</span>
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
              code={code}
              language="Python"
              animate={isDraft}
              placeholder="# waiting"
            />
          </div>
        {/if}
      </section>

      <section class="content-section">
        <div class="section-header">
          <span class="section-title">Output</span>
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
              {#if result.error}
                <div class="error-message">
                  <AlertCircle size={16} />
                  <span>{result.error}</span>
                </div>
              {/if}
              
              {#if result.stdout}
                <div class="output-stream">
                  <div class="stream-header">
                    <span class="stream-label">stdout</span>
                  </div>
                  <pre class="stream-content">{result.stdout}</pre>
                </div>
              {/if}
              
              {#if result.stderr}
                <div class="output-stream error">
                  <div class="stream-header">
                    <span class="stream-label">stderr</span>
                  </div>
                  <pre class="stream-content">{result.stderr}</pre>
                </div>
              {/if}
              
              {#if showArtifacts && artifacts.length > 0}
                <div class="artifacts-section">
                  {#if imageArtifacts.length > 0}
                    <div class="artifact-group">
                      <div class="artifact-header">
                        <Image size={14} />
                        <span>Charts & Images ({imageArtifacts.length})</span>
                      </div>
                      <div class="chart-grid">
                        {#each imageArtifacts as artifact}
                          <div class="chart-card">
                            <a
                              class="chart-image"
                              href={`/api/artifacts/${artifact.artifact_id}`}
                              target={getArtifactTarget()}
                              rel={getArtifactRel()}
                              on:click={(event) => handleArtifactClick(event, artifact)}
                            >
                              <img src={`/api/artifacts/${artifact.artifact_id}`} alt={artifact.name} />
                            </a>
                            <a 
                              href={`/api/artifacts/${artifact.artifact_id}`} 
                              target={getArtifactTarget()}
                              rel={getArtifactRel()}
                              class="artifact-link"
                              on:click={(event) => handleArtifactClick(event, artifact)}
                            >
                              <span>{artifact.name}</span>
                              <ExternalLink size={12} />
                            </a>
                          </div>
                        {/each}
                      </div>
                    </div>
                  {/if}

                  {#if fileArtifacts.length > 0}
                    <div class="artifact-group">
                      <div class="artifact-header">
                        <FileText size={14} />
                        <span>Files ({fileArtifacts.length})</span>
                      </div>
                      <div class="file-list">
                        {#each fileArtifacts as artifact}
                          <a 
                            href={`/api/artifacts/${artifact.artifact_id}`} 
                            target={getArtifactTarget()}
                            rel={getArtifactRel()}
                            class="file-card"
                            on:click={(event) => handleArtifactClick(event, artifact)}
                          >
                            <FileText size={16} />
                            <span>{artifact.name}</span>
                            <ExternalLink size={12} />
                          </a>
                        {/each}
                      </div>
                    </div>
                  {/if}
                </div>
              {:else if !showArtifacts && artifacts.length > 0}
                <div class="artifacts-note">
                  <Image size={14} />
                  <span>{artifacts.length} artifacts available in the Artifacts panel.</span>
                </div>
              {/if}
            {:else}
              <div class="loading-state">
                <Loader2 size={20} class="spin" />
                <span>Running Python code...</span>
              </div>
            {/if}
          </div>
        {/if}
      </section>
    </div>
  {/if}
</article>

<style>
  .python-cell {
    background: var(--surface-0);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-lg);
    overflow: hidden;
    transition: all var(--transition-fast);
  }

  .python-cell:hover {
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
    color: var(--accent-cyan);
    background: var(--accent-cyan-muted);
  }

  .cell-icon {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    background: var(--accent-cyan-muted);
    color: var(--accent-cyan);
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
    color: var(--accent-cyan);
    margin-left: var(--space-2);
    pointer-events: none;
  }

  .artifact-badge {
    display: flex;
    align-items: center;
    gap: var(--space-1);
    padding: 2px 8px;
    border-radius: var(--radius-sm);
    font-size: 11px;
    font-weight: 500;
    background: var(--accent-cyan-muted);
    color: var(--accent-cyan);
    border: 1px solid var(--accent-cyan);
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

  .python-cell:hover .branch-btn {
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
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
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

  .output-stream {
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    overflow: hidden;
  }

  .output-stream.error {
    border-color: var(--danger);
  }

  .stream-header {
    padding: var(--space-2) var(--space-3);
    background: var(--surface-1);
    border-bottom: 1px solid var(--border-soft);
  }

  .output-stream.error .stream-header {
    background: var(--danger-muted);
    border-color: var(--danger);
  }

  .stream-label {
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-muted);
  }

  .output-stream.error .stream-label {
    color: var(--danger);
  }

  .stream-content {
    margin: 0;
    padding: var(--space-3);
    background: var(--bg-0);
    color: var(--text-secondary);
    font-family: var(--font-mono);
    font-size: 13px;
    line-height: 1.5;
    overflow-x: auto;
    white-space: pre-wrap;
    max-height: 300px;
    overflow-y: auto;
  }

  .artifacts-section {
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
  }

  .artifacts-note {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-2) var(--space-3);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    background: var(--surface-0);
    color: var(--text-dim);
    font-size: 12px;
  }

  .artifact-group {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
  }

  .artifact-header {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    color: var(--text-muted);
    font-size: 13px;
    font-weight: 500;
  }

  .chart-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
    gap: var(--space-3);
  }

  .chart-card {
    background: var(--surface-1);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-lg);
    overflow: hidden;
    transition: all var(--transition-fast);
  }

  .chart-card:hover {
    border-color: var(--border-medium);
    box-shadow: var(--shadow-sm);
  }

  .chart-image {
    padding: var(--space-2);
  }

  .chart-image img {
    width: 100%;
    height: auto;
    border-radius: var(--radius-md);
    display: block;
  }

  .artifact-link {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-2);
    padding: var(--space-2) var(--space-3);
    background: var(--bg-1);
    color: var(--accent-blue);
    text-decoration: none;
    font-size: 13px;
    transition: all var(--transition-fast);
  }

  .artifact-link:hover {
    background: var(--accent-blue-muted);
  }

  .file-list {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-2);
  }

  .file-card {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-2) var(--space-3);
    background: var(--surface-1);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    color: var(--text-secondary);
    text-decoration: none;
    font-size: 13px;
    transition: all var(--transition-fast);
  }

  .file-card:hover {
    background: var(--surface-hover);
    border-color: var(--border-medium);
    color: var(--text-primary);
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

    .chart-grid {
      grid-template-columns: 1fr;
    }
  }
</style>
