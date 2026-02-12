<script lang="ts">
  import ArtifactTablePreview from "$lib/components/ArtifactTablePreview.svelte";
  import { AlertCircle, FileText, Image, Loader2, Table } from "lucide-svelte";
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

  function isTableArtifact(value: ArtifactEntry): boolean {
    return value.type === "csv" || value.name.toLowerCase().endsWith(".csv");
  }
</script>

<div class="floating-body" class:image-view={isImageArtifact(artifact.type)}>
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

  @media (max-width: 768px) {
    .floating-body.image-view {
      padding: var(--space-2);
    }
  }
</style>
