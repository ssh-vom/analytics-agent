<script lang="ts">
  import ArtifactPreviewContent from "$lib/components/ArtifactPreviewContent.svelte";
  import { Download, FileText, FileType, Image, Table, X } from "lucide-svelte";
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
  export let onClose: () => void = () => undefined;

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

<div class="floating-layer" aria-live="polite">
  <div
    class="floating-panel"
    role="dialog"
    aria-modal="true"
    aria-label={`Artifact viewer for ${artifact.name}`}
  >
    <header class="floating-header">
      <div class="floating-title">
        {#if isImageArtifact(artifact.type)}
          <Image size={16} />
        {:else if isPdfArtifact(artifact)}
          <FileType size={16} />
        {:else if isTableArtifact(artifact)}
          <Table size={16} />
        {:else}
          <FileText size={16} />
        {/if}
        <span title={artifact.name}>{artifact.name}</span>
      </div>
      <div class="floating-actions">
        <a
          class="viewer-download"
          href={`/api/artifacts/${artifact.artifactId}`}
          target="_blank"
          rel="noreferrer"
          aria-label={`Download ${artifact.name}`}
        >
          <Download size={14} />
        </a>
        <button
          type="button"
          class="floating-close"
          on:click={onClose}
          aria-label="Close expanded preview"
        >
          <X size={14} />
        </button>
      </div>
    </header>

    <ArtifactPreviewContent
      {artifact}
      {previewLoading}
      {previewError}
      {tablePreview}
    />
  </div>
</div>

<style>
  .floating-layer {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.4);
    backdrop-filter: blur(6px);
    z-index: 90;
    animation: overlayFadeIn 0.2s ease forwards;
  }

  @keyframes overlayFadeIn {
    from { opacity: 0; }
    to { opacity: 1; }
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
    border-radius: var(--radius-xl);
    box-shadow: var(--shadow-lg);
    display: flex;
    flex-direction: column;
    min-height: 0;
    animation: panelScaleIn 0.3s cubic-bezier(0.16, 1, 0.3, 1) forwards;
  }

  @keyframes panelScaleIn {
    from {
      opacity: 0;
      transform: translate(-50%, -50%) scale(0.95);
    }
    to {
      opacity: 1;
      transform: translate(-50%, -50%) scale(1);
    }
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

  .floating-close {
    width: 26px;
    height: 26px;
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    background: transparent;
    color: var(--text-dim);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    transition: all var(--transition-fast);
  }

  .floating-close:hover {
    color: var(--text-primary);
    border-color: var(--border-medium);
    background: var(--surface-hover);
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

  @media (max-width: 1100px) {
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
  }
</style>
