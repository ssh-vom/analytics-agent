<script lang="ts">
  import { afterUpdate } from "svelte";
  import ArtifactFloatingViewer from "$lib/components/ArtifactFloatingViewer.svelte";
  import { createArtifactPreviewStore } from "$lib/chat/artifactPreview";
  import {
    FileText,
    Image,
    Table,
    Sparkles,
    Download,
    ChevronLeft,
    ChevronRight,
  } from "lucide-svelte";

  import type {
    PythonArtifact,
    PythonResultPayload,
    TimelineEvent,
  } from "$lib/types";

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
  let selectedArtifact: ArtifactEntry | null = null;
  let showFloatingViewer = false;
  let lastObservedSelectedArtifactId: string | null = null;
  const artifactPreview = createArtifactPreviewStore();

  $: artifactEntries = extractArtifactEntries(events);
  $: if (selectedArtifactId && !artifactEntries.some((entry) => entry.artifactId === selectedArtifactId)) {
    selectedArtifactId = null;
  }
  $: selectedArtifact =
    artifactEntries.find((entry) => entry.artifactId === selectedArtifactId) ?? null;
  $: if (selectedArtifactId && selectedArtifactId !== lastObservedSelectedArtifactId) {
    showFloatingViewer = true;
    lastObservedSelectedArtifactId = selectedArtifactId;
  }
  $: if (!selectedArtifactId) {
    lastObservedSelectedArtifactId = null;
  }
  $: if (selectedArtifact) {
    void artifactPreview.load(
      selectedArtifact.artifactId,
      isTableArtifact(selectedArtifact),
    );
  }
  $: if (!selectedArtifact) {
    artifactPreview.clear();
  }
  $: if (!selectedArtifact || collapsed) {
    showFloatingViewer = false;
  }
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

  function isTableArtifact(artifact: ArtifactEntry): boolean {
    return artifact.type === "csv" || artifact.name.toLowerCase().endsWith(".csv");
  }

  function openArtifactViewer(artifactId: string): void {
    selectedArtifactId = artifactId;
    showFloatingViewer = true;
  }

  function closeFloatingViewer(): void {
    showFloatingViewer = false;
  }

  function handleGlobalKeydown(event: KeyboardEvent): void {
    if (event.key === "Escape" && showFloatingViewer) {
      closeFloatingViewer();
    }
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

<svelte:window on:keydown={handleGlobalKeydown} />

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
    <div class="panel-note">Select an artifact to open it in the floating viewer.</div>
    <div class="artifact-list" bind:this={artifactListElement}>
      {#each artifactEntries as artifact (artifact.key)}
        <article
          class="artifact-card"
          class:selected={artifact.artifactId === selectedArtifactId}
          data-artifact-id={artifact.artifactId}
        >
          <button
            type="button"
            class="artifact-select"
            on:click={() => openArtifactViewer(artifact.artifactId)}
            aria-label={`Open ${artifact.name} in floating viewer`}
          >
            <div class="card-meta">
              <span class="type-tag">{artifact.type}</span>
              <time datetime={artifact.createdAt}>{formatTimestamp(artifact.createdAt)}</time>
            </div>

            {#if isImageArtifact(artifact.type)}
              <div class="image-preview">
                <img src={`/api/artifacts/${artifact.artifactId}`} alt={artifact.name} loading="lazy" />
              </div>
            {:else if isTableArtifact(artifact)}
              <div class="file-preview">
                <Table size={18} />
                <span>Table artifact</span>
              </div>
            {:else}
              <div class="file-preview">
                <FileText size={18} />
                <span>File artifact</span>
              </div>
            {/if}

            <div class="artifact-link" title={artifact.name}>
              <span>{artifact.name}</span>
            </div>
          </button>

          <a
            class="artifact-download"
            href={`/api/artifacts/${artifact.artifactId}`}
            target="_blank"
            rel="noreferrer"
            aria-label={`Download ${artifact.name}`}
          >
            <Download size={13} />
          </a>
        </article>
      {/each}
    </div>
  {/if}
</aside>

{#if showFloatingViewer && selectedArtifact}
  <ArtifactFloatingViewer
    artifact={selectedArtifact}
    previewLoading={$artifactPreview.previewLoading}
    previewError={$artifactPreview.previewError}
    tablePreview={$artifactPreview.tablePreview}
    onClose={closeFloatingViewer}
  />
{/if}

<style>
  .artifacts-panel {
    min-height: 0;
    display: flex;
    flex-direction: column;
    border-left: 1px solid var(--border-soft);
    background: var(--bg-1);
  }

  .panel-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-2);
    padding: var(--space-2) var(--space-3);
    border-bottom: 1px solid var(--border-soft);
    flex-shrink: 0;
  }

  .panel-title {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    color: var(--text-muted);
    min-width: 0;
  }

  .panel-title h2 {
    margin: 0;
    font-family: var(--font-heading);
    font-size: 12px;
    font-weight: 400;
    text-transform: uppercase;
    letter-spacing: 0.03em;
  }

  .panel-actions {
    display: flex;
    align-items: center;
    gap: var(--space-2);
  }

  .count-badge {
    min-width: 20px;
    height: 20px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border-radius: var(--radius-full);
    color: var(--text-dim);
    font-size: 11px;
    font-family: var(--font-mono);
    padding: 0 var(--space-1);
    background: var(--surface-2);
  }

  .collapse-btn {
    width: 22px;
    height: 22px;
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-sm);
    background: transparent;
    color: var(--text-dim);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    transition: color var(--transition-fast);
  }

  .collapse-btn:hover {
    color: var(--text-secondary);
    border-color: var(--border-medium);
  }

  .collapsed-body {
    flex: 1;
    border: none;
    background: transparent;
    cursor: pointer;
    color: var(--text-dim);
    display: flex;
    align-items: center;
    justify-content: center;
    padding: var(--space-2);
  }

  .collapsed-count {
    min-width: 26px;
    height: 26px;
    border-radius: var(--radius-full);
    border: 1px solid var(--border-soft);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-size: 11px;
    font-family: var(--font-mono);
  }

  .empty-state {
    margin: auto;
    max-width: 180px;
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    align-items: center;
    justify-content: center;
    color: var(--text-dim);
    text-align: center;
    font-size: 12px;
  }

  .empty-state p {
    margin: 0;
    line-height: 1.4;
  }

  .panel-note {
    padding: var(--space-2) var(--space-3);
    border-bottom: 1px solid var(--border-soft);
    color: var(--text-dim);
    font-size: 11px;
    font-family: var(--font-mono);
  }

  .artifact-list {
    min-height: 0;
    overflow-y: auto;
    padding: var(--space-3);
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }

  .artifact-card {
    position: relative;
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    background: var(--surface-0);
    transition: border-color var(--transition-fast);
  }

  .artifact-select {
    border: none;
    background: transparent;
    width: 100%;
    text-align: left;
    color: inherit;
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    padding: var(--space-2);
    padding-right: calc(var(--space-3) + 20px);
  }

  .artifact-select:focus-visible {
    outline: 1px solid var(--border-accent);
    outline-offset: -1px;
  }

  .artifact-card.selected {
    border-color: var(--accent-green);
  }

  .card-meta {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-2);
  }

  .card-meta time {
    color: var(--text-dim);
    font-size: 10px;
    font-family: var(--font-mono);
    white-space: nowrap;
  }

  .type-tag {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border-radius: var(--radius-sm);
    color: var(--text-dim);
    font-size: 10px;
    font-family: var(--font-mono);
    line-height: 1;
    padding: 2px 6px;
    text-transform: uppercase;
    background: var(--surface-2);
  }

  .image-preview {
    display: block;
    border-radius: var(--radius-sm);
    overflow: hidden;
    border: 1px solid var(--border-soft);
    background: var(--bg-0);
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
    border: 1px dashed var(--border-soft);
    border-radius: var(--radius-sm);
    padding: var(--space-2);
    color: var(--text-dim);
    font-size: 11px;
  }

  .artifact-link {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    color: var(--text-muted);
    font-size: 12px;
    font-family: var(--font-mono);
    padding: var(--space-1) var(--space-2);
  }

  .artifact-link span {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .artifact-download {
    position: absolute;
    right: var(--space-2);
    bottom: var(--space-2);
    width: 18px;
    height: 18px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    color: var(--text-muted);
    text-decoration: none;
    border-radius: var(--radius-sm);
    transition: color var(--transition-fast);
  }

  .artifact-download:hover {
    color: var(--text-primary);
  }

  @media (max-width: 1100px) {
    .artifacts-panel {
      border-left: none;
      border-top: 1px solid var(--border-soft);
      max-height: 460px;
    }

    .artifacts-panel.collapsed {
      max-height: 56px;
    }

  }

  @media (max-width: 768px) {
  }
</style>
