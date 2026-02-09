<script lang="ts">
  import { afterUpdate } from "svelte";
  import {
    FileText,
    Image,
    Sparkles,
    Download,
    ChevronLeft,
    ChevronRight,
  } from "lucide-svelte";

  import type { PythonArtifact, PythonResultPayload, TimelineEvent } from "$lib/types";

  interface ArtifactEntry {
    key: string;
    artifactId: string;
    type: string;
    name: string;
    createdAt: string;
  }

  export let events: TimelineEvent[] = [];
  export let collapsed = false;
  export let selectedArtifactId: string | null = null;

  let artifactListElement: HTMLDivElement | null = null;
  let lastFocusedArtifactId: string | null = null;

  $: artifactEntries = extractArtifactEntries(events);
  $: if (!selectedArtifactId) {
    lastFocusedArtifactId = null;
  }

  afterUpdate(() => {
    if (collapsed || !selectedArtifactId || selectedArtifactId === lastFocusedArtifactId) {
      return;
    }

    const target = artifactListElement?.querySelector<HTMLElement>(
      `[data-artifact-id="${selectedArtifactId}"]`,
    );
    if (!target) {
      return;
    }

    target.scrollIntoView({ behavior: "smooth", block: "nearest" });
    lastFocusedArtifactId = selectedArtifactId;
  });

  function toggleCollapsed(): void {
    collapsed = !collapsed;
  }

  function extractArtifactEntries(sourceEvents: TimelineEvent[]): ArtifactEntry[] {
    const collected: ArtifactEntry[] = [];

    for (const event of sourceEvents) {
      const artifacts = readArtifacts(event);
      for (const artifact of artifacts) {
        collected.push({
          key: `${event.id}:${artifact.artifact_id}`,
          artifactId: artifact.artifact_id,
          type: artifact.type,
          name: artifact.name,
          createdAt: event.created_at,
        });
      }
    }

    return collected.reverse();
  }

  function readArtifacts(event: TimelineEvent): PythonArtifact[] {
    if (event.type !== "tool_result_python") {
      return [];
    }

    const payload = event.payload as PythonResultPayload;
    const artifacts = payload.artifacts;
    if (!Array.isArray(artifacts)) {
      return [];
    }

    // Validate artifacts - only artifact_id is required, provide defaults for others
    return artifacts
      .filter((artifact): artifact is { artifact_id: string; name?: string; type?: string } =>
        typeof artifact?.artifact_id === "string" && artifact.artifact_id.length > 0
      )
      .map((artifact): PythonArtifact => ({
        artifact_id: artifact.artifact_id,
        name: artifact.name || "unnamed artifact",
        type: artifact.type || "file",
      }));
  }

  function isImageArtifact(type: string): boolean {
    return type === "image";
  }

  function formatTimestamp(value: string): string {
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) {
      return value;
    }
    return parsed.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  }
</script>

<aside class="artifacts-panel" class:collapsed>
  <header class="panel-header">
    <div class="panel-title">
      <Sparkles size={14} />
      {#if !collapsed}
        <h2>Artifacts</h2>
      {/if}
    </div>

    <div class="panel-actions">
      {#if !collapsed}
        <span class="count-badge">{artifactEntries.length}</span>
      {/if}
      <button
        type="button"
        class="collapse-btn"
        on:click={toggleCollapsed}
        aria-label={collapsed ? "Expand artifacts panel" : "Collapse artifacts panel"}
      >
        {#if collapsed}
          <ChevronLeft size={14} />
        {:else}
          <ChevronRight size={14} />
        {/if}
      </button>
    </div>
  </header>

  {#if collapsed}
    <button
      type="button"
      class="collapsed-body"
      on:click={toggleCollapsed}
      aria-label="Open artifacts panel"
    >
      <span class="collapsed-count">{artifactEntries.length}</span>
    </button>
  {:else if artifactEntries.length === 0}
    <div class="empty-state">
      <Image size={20} />
      <p>Generated charts and files will appear here.</p>
    </div>
  {:else}
    <div class="artifact-list" bind:this={artifactListElement}>
      {#each artifactEntries as artifact (artifact.key)}
        <article
          class="artifact-card"
          class:selected={artifact.artifactId === selectedArtifactId}
          data-artifact-id={artifact.artifactId}
        >
          <div class="card-meta">
            <span class="type-tag">{artifact.type}</span>
            <time datetime={artifact.createdAt}>{formatTimestamp(artifact.createdAt)}</time>
          </div>

          {#if isImageArtifact(artifact.type)}
            <a
              class="image-preview"
              href={`/api/artifacts/${artifact.artifactId}`}
              target="_blank"
              rel="noreferrer"
              aria-label={`Open ${artifact.name}`}
            >
              <img src={`/api/artifacts/${artifact.artifactId}`} alt={artifact.name} loading="lazy" />
            </a>
          {:else}
            <div class="file-preview">
              <FileText size={18} />
              <span>File artifact</span>
            </div>
          {/if}

          <a
            class="artifact-link"
            href={`/api/artifacts/${artifact.artifactId}`}
            target="_blank"
            rel="noreferrer"
            title={artifact.name}
          >
            <span>{artifact.name}</span>
            <Download size={13} />
          </a>
        </article>
      {/each}
    </div>
  {/if}
</aside>

<style>
  .artifacts-panel {
    min-height: 0;
    display: flex;
    flex-direction: column;
    border-left: 1px solid var(--border-soft);
    background: var(--surface-0);
  }

  .panel-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-2);
    padding: var(--space-3) var(--space-3) var(--space-2);
    border-bottom: 1px solid var(--border-soft);
    background: var(--surface-1);
    flex-shrink: 0;
  }

  .panel-title {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    color: var(--text-secondary);
    min-width: 0;
  }

  .panel-title h2 {
    margin: 0;
    font-size: 13px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }

  .panel-actions {
    display: flex;
    align-items: center;
    gap: var(--space-2);
  }

  .count-badge {
    min-width: 24px;
    height: 24px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border-radius: var(--radius-full);
    border: 1px solid var(--border-medium);
    color: var(--text-secondary);
    font-size: 12px;
    padding: 0 var(--space-2);
    background: var(--surface-0);
  }

  .collapse-btn {
    width: 24px;
    height: 24px;
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    background: var(--surface-0);
    color: var(--text-muted);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
  }

  .collapse-btn:hover {
    color: var(--text-primary);
    border-color: var(--border-medium);
    background: var(--surface-hover);
  }

  .collapsed-body {
    flex: 1;
    border: none;
    background: transparent;
    cursor: pointer;
    color: var(--text-secondary);
    display: flex;
    align-items: center;
    justify-content: center;
    padding: var(--space-2);
  }

  .collapsed-count {
    min-width: 30px;
    height: 30px;
    border-radius: var(--radius-full);
    border: 1px solid var(--border-medium);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-size: 12px;
    background: var(--surface-1);
  }

  .empty-state {
    margin: auto;
    max-width: 200px;
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    align-items: center;
    justify-content: center;
    color: var(--text-dim);
    text-align: center;
    font-size: 13px;
  }

  .empty-state p {
    margin: 0;
    line-height: 1.4;
  }

  .artifact-list {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    padding: var(--space-3);
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
  }

  .artifact-card {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-lg);
    background: var(--surface-1);
    padding: var(--space-2);
  }

  .artifact-card.selected {
    border-color: var(--accent-orange);
    box-shadow: var(--glow-orange);
  }

  .card-meta {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-2);
  }

  .card-meta time {
    color: var(--text-dim);
    font-size: 11px;
    white-space: nowrap;
  }

  .type-tag {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border-radius: var(--radius-sm);
    border: 1px solid var(--border-medium);
    color: var(--text-secondary);
    font-size: 11px;
    line-height: 1;
    padding: 4px 8px;
    text-transform: uppercase;
  }

  .image-preview {
    display: block;
    border-radius: var(--radius-md);
    overflow: hidden;
    border: 1px solid var(--border-soft);
    background: var(--surface-0);
    aspect-ratio: 16 / 9;
  }

  .image-preview img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
  }

  .file-preview {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    border: 1px dashed var(--border-medium);
    border-radius: var(--radius-md);
    padding: var(--space-3);
    color: var(--text-dim);
    font-size: 12px;
  }

  .artifact-link {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-2);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    background: var(--surface-0);
    text-decoration: none;
    color: var(--text-secondary);
    font-size: 13px;
    padding: var(--space-2);
  }

  .artifact-link span {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .artifact-link:hover {
    color: var(--text-primary);
    border-color: var(--border-medium);
    background: var(--surface-hover);
  }

  @media (max-width: 1100px) {
    .artifacts-panel {
      border-left: none;
      border-top: 1px solid var(--border-soft);
      max-height: 280px;
    }

    .artifacts-panel.collapsed {
      max-height: 56px;
    }
  }
</style>
