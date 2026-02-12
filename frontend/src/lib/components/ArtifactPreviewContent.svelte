<script lang="ts">
  import ArtifactTablePreview from "$lib/components/ArtifactTablePreview.svelte";
  import { AlertCircle, FileText, Image, Loader2, Table, FileType } from "lucide-svelte";
  import type { ArtifactTablePreview as ArtifactTablePreviewData } from "$lib/types";

  type ArtifactEntry = {
    artifactId: string;
    type: string;
    name: string;
  };

  export let artifact: ArtifactEntry;
  export let previewLoading = false;
  export let previewError = "";
  export let tablePreview: ArtifactTablePreviewData | null = null;

  function isImageArtifact(type: string): boolean {
    return type === "image";
  }

  function isPdfArtifact(value: ArtifactEntry): boolean {
    return value.type === "pdf" || value.name.toLowerCase().endsWith(".pdf");
  }

  function isTableArtifact(value: ArtifactEntry): boolean {
    return value.type === "csv" || value.name.toLowerCase().endsWith(".csv");
  }
</script>

<div
  class="floating-body"
  class:image-view={isImageArtifact(artifact.type)}
  class:pdf-view={isPdfArtifact(artifact)}
>
  {#if isImageArtifact(artifact.type)}
    <a
      class="floating-image"
      href={`/api/artifacts/${artifact.artifactId}`}
      target="_blank"
      rel="noreferrer"
      aria-label={`Open ${artifact.name}`}
    >
      <img src={`/api/artifacts/${artifact.artifactId}`} alt={artifact.name} loading="lazy" />
    </a>
  {:else if isPdfArtifact(artifact)}
    <div class="pdf-embed-wrapper">
      <iframe
        src={`/api/artifacts/${artifact.artifactId}?inline=true`}
        class="pdf-embed"
        title={artifact.name}
      >
        <div class="pdf-fallback">
          <FileType size={28} />
          <p>Your browser cannot display this PDF inline.</p>
          <a
            class="pdf-fallback-link"
            href={`/api/artifacts/${artifact.artifactId}`}
            target="_blank"
            rel="noreferrer"
          >
            Open PDF in new tab
          </a>
        </div>
      </iframe>
    </div>
  {:else if isTableArtifact(artifact)}
    {#if previewLoading}
      <div class="viewer-state loading">
        <Loader2 size={16} />
        <span>Loading table preview...</span>
      </div>
    {:else if previewError}
      <div class="viewer-state error">
        <AlertCircle size={15} />
        <span>{previewError}</span>
      </div>
    {:else if tablePreview}
      <ArtifactTablePreview
        artifactName={artifact.name}
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

<style>
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

  .floating-body.pdf-view {
    display: flex;
    flex-direction: column;
    padding: 0;
  }

  .pdf-embed-wrapper {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
  }

  .pdf-embed {
    flex: 1;
    width: 100%;
    min-height: 0;
    border: none;
    border-radius: 0 0 var(--radius-lg) var(--radius-lg);
    background: var(--bg-0);
  }

  .pdf-fallback {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: var(--space-3);
    padding: var(--space-6);
    color: var(--text-dim);
    text-align: center;
  }

  .pdf-fallback p {
    margin: 0;
    font-size: 13px;
    line-height: 1.5;
  }

  .pdf-fallback-link {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-2) var(--space-4);
    border: 1px solid var(--border-medium);
    border-radius: var(--radius-md);
    color: var(--accent-green);
    font-size: 13px;
    font-weight: 500;
    text-decoration: none;
    transition: all var(--transition-fast);
  }

  .pdf-fallback-link:hover {
    background: var(--surface-hover);
    border-color: var(--accent-green);
  }

  @media (max-width: 768px) {
    .floating-body.image-view {
      padding: var(--space-2);
    }
  }
</style>
