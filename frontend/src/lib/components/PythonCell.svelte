<script lang="ts">
  import { createEventDispatcher } from "svelte";
  import CodeBlock from "$lib/components/CodeBlock.svelte";
  import ToolCellHeader from "$lib/components/ToolCellHeader.svelte";
  import type { PythonArtifact, TimelineEvent } from "$lib/types";
  import { readPythonResult } from "$lib/cells";
  import { Terminal } from "lucide-svelte";
  import { AlertCircle } from "lucide-svelte";
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

  $: rawCode = callEvent?.payload?.code;
  $: code =
    typeof rawCode === "string" && rawCode.length > 0
      ? rawCode
      : resultEvent
        ? "# code unavailable (result event arrived before call event)"
        : "";
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

<article class="python-cell message-entrance">
  <ToolCellHeader
    bind:collapsed={cellCollapsed}
    title="Python"
    expandAriaLabel="Expand Python cell"
    collapseAriaLabel="Collapse Python cell"
    {statusLabel}
    executionMs={result?.execution_ms}
    {onBranch}
    accentColor="var(--accent-purple)"
  >
    <svelte:fragment slot="icon">
      <Terminal size={16} />
    </svelte:fragment>

    <svelte:fragment slot="collapsed-meta">
      {#if artifacts.length > 0}
        <div class="artifact-badge">
          <Image size={12} />
          <span>{artifacts.length}</span>
        </div>
      {/if}
    </svelte:fragment>
  </ToolCellHeader>

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
    border-radius: var(--radius-md);
    overflow: hidden;
    transition: border-color var(--transition-fast);
    flex-shrink: 0;
  }

  .python-cell:hover {
    border-color: var(--border-medium);
  }

  .artifact-badge {
    display: flex;
    align-items: center;
    gap: var(--space-1);
    padding: 1px 6px;
    border-radius: var(--radius-sm);
    font-size: 11px;
    font-weight: 500;
    font-family: var(--font-mono);
    background: var(--accent-purple-muted);
    color: var(--accent-purple);
    flex-shrink: 0;
    margin-left: var(--space-1);
  }

  .cell-content {
    display: flex;
    flex-direction: column;
    animation: messageFadeIn 0.25s cubic-bezier(0.4, 0, 0.2, 1) forwards;
  }

  @keyframes messageFadeIn {
    from {
      opacity: 0;
    }
    to {
      opacity: 1;
    }
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
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
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

  .output-stream {
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    overflow: hidden;
  }

  .output-stream.error {
    border-color: var(--danger);
  }

  .stream-header {
    padding: var(--space-1) var(--space-3);
    border-bottom: 1px solid var(--border-soft);
  }

  .output-stream.error .stream-header {
    background: var(--danger-muted);
  }

  .stream-label {
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--text-dim);
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
    font-size: 12px;
    line-height: 1.5;
    overflow-x: auto;
    white-space: pre-wrap;
    max-height: 300px;
    overflow-y: auto;
  }

  .artifacts-section {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
  }

  .artifacts-note {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-2) var(--space-3);
    color: var(--text-dim);
    font-size: 12px;
  }

  .artifact-group {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }

  .artifact-header {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    color: var(--text-dim);
    font-size: 12px;
    font-weight: 500;
  }

  .chart-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: var(--space-2);
  }

  .chart-card {
    background: var(--surface-1);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    overflow: hidden;
    transition: border-color var(--transition-fast);
  }

  .chart-card:hover {
    border-color: var(--border-medium);
  }

  .chart-image {
    padding: var(--space-2);
  }

  .chart-image img {
    width: 100%;
    height: auto;
    border-radius: var(--radius-sm);
    display: block;
  }

  .artifact-link {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-2);
    padding: var(--space-2) var(--space-3);
    color: var(--accent-blue);
    text-decoration: none;
    font-size: 12px;
    font-family: var(--font-mono);
    transition: color var(--transition-fast);
  }

  .artifact-link:hover {
    color: var(--text-primary);
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
    font-size: 12px;
    font-family: var(--font-mono);
    transition: border-color var(--transition-fast);
  }

  .file-card:hover {
    border-color: var(--border-medium);
    color: var(--text-primary);
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

  @media (max-width: 640px) {
    .artifact-badge {
      display: none;
    }

    .chart-grid {
      grid-template-columns: 1fr;
    }
  }
</style>
