<script lang="ts">
  import { afterUpdate } from "svelte";
  import { fetchArtifactPreview } from "$lib/api/client";
  import ArtifactTablePreview from "$lib/components/ArtifactTablePreview.svelte";
  import {
    FileText,
    Image,
    Table,
    Sparkles,
    Download,
    Loader2,
    AlertCircle,
    X,
    ChevronLeft,
    ChevronRight,
  } from "lucide-svelte";

  import type {
    ArtifactTablePreview as ArtifactTablePreviewData,
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
  let previewArtifactId: string | null = null;
  let previewLoading = false;
  let previewError = "";
  let tablePreview: ArtifactTablePreviewData | null = null;
  let previewRequestToken = 0;
  let showFloatingViewer = false;
  let lastObservedSelectedArtifactId: string | null = null;

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
    void loadArtifactPreview(selectedArtifact);
  }
  $: if (!selectedArtifact) {
    clearPreviewState();
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

  function clearPreviewState(): void {
    previewArtifactId = null;
    previewLoading = false;
    previewError = "";
    tablePreview = null;
  }

  async function loadArtifactPreview(artifact: ArtifactEntry): Promise<void> {
    if (previewArtifactId === artifact.artifactId) {
      return;
    }

    previewArtifactId = artifact.artifactId;
    previewRequestToken += 1;
    const token = previewRequestToken;

    if (!isTableArtifact(artifact)) {
      previewLoading = false;
      previewError = "";
      tablePreview = null;
      return;
    }

    previewLoading = true;
    previewError = "";
    tablePreview = null;

    try {
      const response = await fetchArtifactPreview(artifact.artifactId, 120);
      if (token !== previewRequestToken || previewArtifactId !== artifact.artifactId) {
        return;
      }
      tablePreview = response.preview;
    } catch (error) {
      if (token !== previewRequestToken || previewArtifactId !== artifact.artifactId) {
        return;
      }
      previewError =
        error instanceof Error ? error.message : "Failed to load table artifact preview";
    } finally {
      if (token === previewRequestToken && previewArtifactId === artifact.artifactId) {
        previewLoading = false;
      }
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
  <div class="floating-layer" aria-live="polite">
    <div
      class="floating-panel"
      role="dialog"
      aria-modal="true"
      aria-label={`Artifact viewer for ${selectedArtifact.name}`}
    >
      <header class="floating-header">
        <div class="floating-title">
          {#if isImageArtifact(selectedArtifact.type)}
            <Image size={16} />
          {:else if isTableArtifact(selectedArtifact)}
            <Table size={16} />
          {:else}
            <FileText size={16} />
          {/if}
          <span title={selectedArtifact.name}>{selectedArtifact.name}</span>
        </div>
        <div class="floating-actions">
          <a
            class="viewer-download"
            href={`/api/artifacts/${selectedArtifact.artifactId}`}
            target="_blank"
            rel="noreferrer"
            aria-label={`Download ${selectedArtifact.name}`}
          >
            <Download size={14} />
          </a>
          <button
            type="button"
            class="floating-close"
            on:click={closeFloatingViewer}
            aria-label="Close expanded preview"
          >
            <X size={14} />
          </button>
        </div>
      </header>

      <div class="floating-body" class:image-view={isImageArtifact(selectedArtifact.type)}>
        {#if isImageArtifact(selectedArtifact.type)}
          <a
            class="floating-image"
            href={`/api/artifacts/${selectedArtifact.artifactId}`}
            target="_blank"
            rel="noreferrer"
            aria-label={`Open ${selectedArtifact.name}`}
          >
            <img src={`/api/artifacts/${selectedArtifact.artifactId}`} alt={selectedArtifact.name} loading="lazy" />
          </a>
        {:else if isTableArtifact(selectedArtifact)}
          {#if previewLoading}
            <div class="viewer-state loading">
              <Loader2 size={16} class="spin" />
              <span>Loading table preview...</span>
            </div>
          {:else if previewError}
            <div class="viewer-state error">
              <AlertCircle size={15} />
              <span>{previewError}</span>
            </div>
          {:else if tablePreview}
            <ArtifactTablePreview
              artifactName={selectedArtifact.name}
              columns={tablePreview.columns}
              rows={tablePreview.rows}
              rowCount={tablePreview.row_count}
              previewCount={tablePreview.preview_count}
              truncated={tablePreview.truncated}
              stickyHeader={true}
              variant="floating"
            />
          {:else}
            <div class="viewer-state">
              <Table size={16} />
              <span>No preview available.</span>
            </div>
          {/if}
        {:else}
          <div class="viewer-state">
            <FileText size={16} />
            <span>This artifact does not have an inline preview. Use download to open it.</span>
          </div>
        {/if}
      </div>
    </div>
  </div>
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

  .floating-close {
    width: 22px;
    height: 22px;
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-sm);
    background: transparent;
    color: var(--text-dim);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    transition: color var(--transition-fast);
  }

  .floating-close:hover {
    color: var(--text-primary);
    border-color: var(--border-medium);
  }

  .viewer-download {
    width: 22px;
    height: 22px;
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-sm);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    color: var(--text-dim);
    transition: color var(--transition-fast);
    text-decoration: none;
    flex-shrink: 0;
  }

  .viewer-download:hover {
    color: var(--text-primary);
    border-color: var(--border-medium);
  }

  .panel-note {
    padding: var(--space-2) var(--space-3);
    border-bottom: 1px solid var(--border-soft);
    color: var(--text-dim);
    font-size: 11px;
    font-family: var(--font-mono);
  }

  .viewer-state {
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--space-2);
    padding: var(--space-4);
    color: var(--text-dim);
    font-size: 12px;
    text-align: center;
  }

  .viewer-state.error {
    color: var(--danger);
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

  .floating-layer {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.35);
    backdrop-filter: blur(1px);
    z-index: 90;
  }

  .floating-panel {
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    width: min(1080px, calc(100vw - 80px));
    height: min(82vh, calc(100vh - 80px));
    background: var(--bg-1);
    border: 1px solid var(--border-medium);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow-lg);
    display: flex;
    flex-direction: column;
    min-height: 0;
  }

  .floating-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-2);
    padding: var(--space-2) var(--space-3);
    border-bottom: 1px solid var(--border-soft);
  }

  .floating-title {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    min-width: 0;
    color: var(--text-primary);
    font-family: var(--font-mono);
    font-size: 12px;
  }

  .floating-title span {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .floating-actions {
    display: flex;
    align-items: center;
    gap: var(--space-1);
  }

  .floating-body {
    flex: 1;
    min-height: 0;
    overflow: auto;
    padding: var(--space-3);
  }

  .floating-body.image-view {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: var(--space-4);
  }

  .floating-image {
    display: flex;
    align-items: center;
    justify-content: center;
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    overflow: hidden;
    background: var(--bg-0);
    max-width: 100%;
    max-height: 100%;
    margin: 0 auto;
  }

  .floating-image img {
    display: block;
    width: auto;
    height: auto;
    max-width: 100%;
    max-height: 100%;
    object-fit: contain;
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

    .floating-panel {
      width: calc(100vw - 36px);
      height: calc(100vh - 36px);
    }
  }

  @media (max-width: 768px) {
    .floating-panel {
      width: calc(100vw - 20px);
      height: calc(100vh - 20px);
    }

    .floating-body.image-view {
      padding: var(--space-2);
    }
  }
</style>
