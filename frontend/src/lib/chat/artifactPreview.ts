import { writable } from "svelte/store";
import { fetchArtifactPreview } from "$lib/api/client";
import type { ArtifactTablePreview as ArtifactTablePreviewData } from "$lib/types";

export type ArtifactPreviewState = {
  previewArtifactId: string | null;
  previewLoading: boolean;
  previewError: string;
  tablePreview: ArtifactTablePreviewData | null;
};

const INITIAL_STATE: ArtifactPreviewState = {
  previewArtifactId: null,
  previewLoading: false,
  previewError: "",
  tablePreview: null,
};

export function createArtifactPreviewStore() {
  const store = writable<ArtifactPreviewState>(INITIAL_STATE);
  let previewArtifactId: string | null = null;
  let previewRequestToken = 0;

  function clear(): void {
    previewArtifactId = null;
    previewRequestToken += 1;
    store.set(INITIAL_STATE);
  }

  async function load(artifactId: string, isTableArtifact: boolean): Promise<void> {
    if (previewArtifactId === artifactId) {
      return;
    }

    previewArtifactId = artifactId;
    previewRequestToken += 1;
    const token = previewRequestToken;

    if (!isTableArtifact) {
      store.set({
        previewArtifactId: artifactId,
        previewLoading: false,
        previewError: "",
        tablePreview: null,
      });
      return;
    }

    store.set({
      previewArtifactId: artifactId,
      previewLoading: true,
      previewError: "",
      tablePreview: null,
    });

    try {
      const response = await fetchArtifactPreview(artifactId, 120);
      if (token !== previewRequestToken || previewArtifactId !== artifactId) {
        return;
      }
      store.set({
        previewArtifactId: artifactId,
        previewLoading: false,
        previewError: "",
        tablePreview: response.preview,
      });
    } catch (error) {
      if (token !== previewRequestToken || previewArtifactId !== artifactId) {
        return;
      }
      store.set({
        previewArtifactId: artifactId,
        previewLoading: false,
        previewError:
          error instanceof Error
            ? error.message
            : "Failed to load table artifact preview",
        tablePreview: null,
      });
    }
  }

  return {
    subscribe: store.subscribe,
    clear,
    load,
  };
}
