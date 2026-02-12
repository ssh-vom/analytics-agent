<script lang="ts">
  import { onMount } from "svelte";
  import {
    attachExternalDuckDB,
    attachExternalDuckDBUpload,
    detachExternalDuckDB,
    fetchWorldlineSchema,
  } from "$lib/api/client";
  import { AlertCircle } from "lucide-svelte";
  import { CheckCircle2 } from "lucide-svelte";
  import { Database } from "lucide-svelte";
  import { Loader2 } from "lucide-svelte";
  import { Plus } from "lucide-svelte";
  import { Trash2 } from "lucide-svelte";
  import { X } from "lucide-svelte";
  import { getActiveWorldlineFromStorage } from "$lib/chat/activeWorldline";

  type AttachedConnector = {
    alias: string;
    db_path: string;
    db_type: string;
    attached_at: string;
    tables: string[];
  };

  let worldlineId = "";
  let loading = true;
  let errorMessage = "";
  let connectors: AttachedConnector[] = [];

  let showModal = false;
  let newConnectorType: "duckdb" | "sqlite" | "postgres" | "mysql" = "duckdb";
  let newConnectorFile: File | null = null;
  let fileInputKey = 0;
  let newConnectorPath = "";
  let newConnectorAlias = "";
  let submitting = false;
  let formError = "";
  let formSuccess = "";

  onMount(() => {
    const saved = getActiveWorldlineFromStorage();
    if (!saved) {
      loading = false;
      errorMessage = "No active worldline selected. Open Chat and select a worldline first.";
      return;
    }

    worldlineId = saved;
    void loadConnectors();
  });

  async function loadConnectors(): Promise<void> {
    if (!worldlineId) {
      loading = false;
      return;
    }

    loading = true;
    errorMessage = "";
    try {
      const schema = await fetchWorldlineSchema(worldlineId);
      connectors = [...schema.attached_databases].sort((a, b) =>
        b.attached_at.localeCompare(a.attached_at),
      );
    } catch (error) {
      errorMessage = error instanceof Error ? error.message : "Failed to load connectors";
    } finally {
      loading = false;
    }
  }

  async function addConnector(): Promise<void> {
    if (!worldlineId || submitting) {
      return;
    }

    formError = "";
    formSuccess = "";

    if (newConnectorType !== "duckdb") {
      formError = "Only DuckDB is available right now. Other connector types are coming soon.";
      return;
    }

    submitting = true;
    try {
      const alias = newConnectorAlias.trim() || undefined;
      const result = newConnectorFile
        ? await attachExternalDuckDBUpload(worldlineId, newConnectorFile, alias)
        : newConnectorPath.trim()
          ? await attachExternalDuckDB(worldlineId, newConnectorPath.trim(), alias)
          : null;

      if (!result) {
        formError = "Choose a DuckDB file or provide a database path.";
        return;
      }

      formSuccess = `Attached as ${result.alias}`;
      showModal = false;
      newConnectorFile = null;
      fileInputKey += 1;
      newConnectorPath = "";
      newConnectorAlias = "";
      newConnectorType = "duckdb";
      await loadConnectors();
    } catch (error) {
      formError = error instanceof Error ? error.message : "Failed to attach connector";
    } finally {
      submitting = false;
    }
  }

  async function removeConnector(alias: string): Promise<void> {
    if (!worldlineId) {
      return;
    }

    if (!confirm(`Detach connector "${alias}" from this worldline?`)) {
      return;
    }

    errorMessage = "";
    formSuccess = "";
    try {
      await detachExternalDuckDB(worldlineId, alias);
      formSuccess = `Detached ${alias}`;
      await loadConnectors();
    } catch (error) {
      errorMessage = error instanceof Error ? error.message : "Failed to detach connector";
    }
  }

  function closeModal(): void {
    showModal = false;
    formError = "";
    newConnectorFile = null;
    fileInputKey += 1;
    newConnectorPath = "";
    newConnectorAlias = "";
  }

  function handleFileSelection(event: Event): void {
    const input = event.currentTarget as HTMLInputElement;
    newConnectorFile = input.files && input.files[0] ? input.files[0] : null;
  }

  function formatDate(isoDate: string): string {
    const date = new Date(isoDate);
    return (
      date.toLocaleDateString(undefined, {
        month: "numeric",
        day: "numeric",
        year: "numeric",
      }) +
      ", " +
      date.toLocaleTimeString(undefined, {
        hour: "numeric",
        minute: "2-digit",
      })
    );
  }

  function handleModalKeydown(event: KeyboardEvent): void {
    if (event.key === "Escape") {
      closeModal();
    }
  }
</script>

<div class="connectors-page">
  <header class="page-header">
    <div class="header-content">
      <div>
        <h1>Connectors</h1>
        <p class="subtitle">Connect DuckDB files to the active worldline for analysis.</p>
      </div>
      <div class="header-actions">
        {#if worldlineId}
          <span class="worldline-badge">Worldline: {worldlineId.slice(0, 12)}</span>
        {/if}
        <button class="new-connector-btn" on:click={() => (showModal = true)}>
          <Plus size={18} />
          <span>New Connector</span>
        </button>
      </div>
    </div>
  </header>

  <main class="connectors-list">
    {#if loading}
      <div class="loading-state">
        <Loader2 size={18} class="spin" />
        <span>Loading connectors...</span>
      </div>
    {:else if errorMessage}
      <div class="error-state">
        <AlertCircle size={16} />
        <span>{errorMessage}</span>
      </div>
    {:else}
      {#if formSuccess}
        <div class="success-state">
          <CheckCircle2 size={16} />
          <span>{formSuccess}</span>
        </div>
      {/if}

      {#if connectors.length > 0}
        {#each connectors as connector}
          <div class="connector-card">
            <div class="connector-header">
              <div class="connector-info">
                <Database size={20} />
                <span class="connector-name">{connector.alias}</span>
                <span class="type-badge">{connector.db_type.toUpperCase()}</span>
                <span class="status-badge">
                  <span class="status-dot"></span>
                  Attached
                </span>
              </div>

              <button
                class="action-btn delete"
                on:click={() => removeConnector(connector.alias)}
                title="Detach connector"
              >
                <Trash2 size={16} />
              </button>
            </div>

            <div class="connector-details">
              <code class="connection-string">{connector.db_path}</code>
              <span class="last-connected">Attached: {formatDate(connector.attached_at)}</span>
            </div>
            <div class="connector-meta">{connector.tables.length} tables available</div>
          </div>
        {/each}
      {:else}
        <div class="empty-state">
          <Database size={48} />
          <h3>No connectors attached</h3>
          <p>Attach a DuckDB file to make it available to this worldline and chat analysis.</p>
          <button class="new-connector-btn" on:click={() => (showModal = true)}>
            <Plus size={18} />
            <span>Attach Connector</span>
          </button>
        </div>
      {/if}
    {/if}
  </main>
</div>

{#if showModal}
  <div
    class="modal-overlay"
    role="button"
    tabindex="0"
    on:click|self={closeModal}
    on:keydown={handleModalKeydown}
  >
    <div class="modal" role="dialog" aria-modal="true" aria-label="Attach connector">
      <header class="modal-header">
        <h2>New Connector</h2>
        <button class="close-btn" on:click={closeModal}>
          <X size={20} />
        </button>
      </header>

      <form class="modal-form" on:submit|preventDefault={addConnector}>
        <div class="form-group">
          <label for="type">Database Type</label>
          <select id="type" bind:value={newConnectorType} disabled={submitting}>
            <option value="duckdb">DuckDB</option>
            <option value="sqlite" disabled>SQLite (coming soon)</option>
            <option value="postgres" disabled>PostgreSQL (coming soon)</option>
            <option value="mysql" disabled>MySQL (coming soon)</option>
          </select>
        </div>

        <div class="form-group">
          <label for="file-picker">DuckDB file</label>
          {#key fileInputKey}
            <input
              id="file-picker"
              type="file"
              accept=".duckdb,.db"
              on:change={handleFileSelection}
              disabled={submitting}
            />
          {/key}
          {#if newConnectorFile}
            <span class="file-selected">Selected: {newConnectorFile.name}</span>
          {/if}
          <span class="form-hint">Preferred: use the picker to upload and attach.</span>
        </div>

        <div class="form-group">
          <label for="connection">Database file path</label>
          <input
            id="connection"
            type="text"
            bind:value={newConnectorPath}
            placeholder="Optional fallback: /absolute/path/to/database.duckdb"
            disabled={submitting}
          />
          <span class="form-hint">Optional fallback for files already on backend disk.</span>
        </div>

        <div class="form-group">
          <label for="alias">Alias (optional)</label>
          <input
            id="alias"
            type="text"
            bind:value={newConnectorAlias}
            placeholder="e.g., warehouse"
            disabled={submitting}
          />
        </div>

        {#if formError}
          <div class="alert error">
            <AlertCircle size={14} />
            <span>{formError}</span>
          </div>
        {/if}

        <div class="modal-actions">
          <button type="button" class="btn-secondary" on:click={closeModal} disabled={submitting}>
            Cancel
          </button>
          <button
            type="submit"
            class="btn-primary"
            disabled={(!newConnectorFile && !newConnectorPath.trim()) || submitting}
          >
            {#if submitting}
              <Loader2 size={14} class="spin" />
              <span>Attaching...</span>
            {:else}
              <span>Attach Connector</span>
            {/if}
          </button>
        </div>
      </form>
    </div>
  </div>
{/if}

<style>
  .connectors-page {
    height: 100vh;
    overflow-y: auto;
    background: var(--bg-0);
  }

  .page-header {
    padding: var(--space-6) var(--space-8);
    border-bottom: 1px solid var(--border-soft);
    background: var(--surface-0);
  }

  .header-content {
    max-width: 1200px;
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: var(--space-4);
  }

  .header-actions {
    display: flex;
    align-items: center;
    gap: var(--space-2);
  }

  h1 {
    margin: 0 0 var(--space-2);
    font-family: var(--font-heading);
    font-size: 28px;
    font-weight: 500;
    color: var(--text-primary);
  }

  .subtitle {
    margin: 0;
    color: var(--text-muted);
    font-size: 15px;
  }

  .worldline-badge {
    font-size: 12px;
    color: var(--text-secondary);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-full);
    padding: 6px 10px;
    background: var(--surface-1);
    font-family: var(--font-mono);
  }

  .new-connector-btn {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-3) var(--space-4);
    background: var(--accent-orange);
    border: none;
    border-radius: var(--radius-md);
    color: #111;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    transition: all var(--transition-fast);
  }

  .new-connector-btn:hover {
    background: var(--accent-orange-hover);
    transform: translateY(-1px);
  }

  .connectors-list {
    max-width: 1200px;
    padding: var(--space-6) var(--space-8);
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
  }

  .loading-state,
  .error-state,
  .success-state {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-lg);
    background: var(--surface-0);
    color: var(--text-muted);
    padding: var(--space-4);
  }

  .error-state {
    border-color: var(--danger);
    color: var(--danger);
    background: var(--danger-muted);
  }

  .success-state {
    border-color: var(--accent-cyan);
    color: var(--accent-cyan);
    background: var(--accent-cyan-muted);
  }

  .connector-card {
    padding: var(--space-4);
    background: var(--surface-0);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-lg);
    transition: all var(--transition-fast);
  }

  .connector-card:hover {
    border-color: var(--border-medium);
    box-shadow: var(--shadow-sm);
  }

  .connector-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: var(--space-3);
    gap: var(--space-2);
  }

  .connector-info {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    color: var(--text-secondary);
    min-width: 0;
  }

  .connector-name {
    font-family: var(--font-mono);
    font-size: 16px;
    color: var(--text-primary);
  }

  .type-badge {
    padding: 2px 8px;
    border-radius: var(--radius-sm);
    font-size: 11px;
    font-weight: 600;
    font-family: var(--font-mono);
    color: var(--accent-orange);
    background: var(--accent-orange-muted);
  }

  .status-badge {
    display: inline-flex;
    align-items: center;
    gap: var(--space-1);
    padding: var(--space-1) var(--space-2);
    background: var(--accent-cyan-muted);
    color: var(--accent-cyan);
    border-radius: var(--radius-md);
    font-size: 12px;
  }

  .status-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--accent-cyan);
  }

  .action-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: var(--space-1);
    padding: var(--space-2);
    background: transparent;
    border: 1px solid transparent;
    border-radius: var(--radius-md);
    color: var(--danger);
    cursor: pointer;
    transition: all var(--transition-fast);
  }

  .action-btn.delete:hover {
    background: var(--danger-muted);
    border-color: var(--danger);
  }

  .connector-details {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-3);
  }

  .connection-string {
    padding: var(--space-2) var(--space-3);
    background: var(--surface-1);
    border-radius: var(--radius-md);
    color: var(--text-muted);
    font-family: var(--font-mono);
    font-size: 13px;
    word-break: break-all;
  }

  .last-connected {
    color: var(--text-dim);
    font-size: 13px;
    white-space: nowrap;
  }

  .connector-meta {
    margin-top: var(--space-2);
    color: var(--text-dim);
    font-size: 13px;
  }

  .empty-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--space-4);
    padding: var(--space-8);
    text-align: center;
    border: 1px dashed var(--border-medium);
    border-radius: var(--radius-lg);
    color: var(--text-muted);
  }

  .empty-state h3 {
    margin: 0;
    color: var(--text-primary);
    font-size: 18px;
    font-weight: 500;
  }

  .empty-state p {
    margin: 0;
    max-width: 52ch;
  }

  .modal-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.7);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
    padding: var(--space-4);
    border: none;
  }

  .modal {
    width: 100%;
    max-width: 520px;
    background: var(--surface-0);
    border: 1px solid var(--border-medium);
    border-radius: var(--radius-xl);
    box-shadow: var(--shadow-lg);
    overflow: hidden;
  }

  .modal-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: var(--space-4) var(--space-5);
    border-bottom: 1px solid var(--border-soft);
  }

  .modal-header h2 {
    margin: 0;
    font-size: 18px;
    font-weight: 600;
    color: var(--text-primary);
  }

  .close-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    background: transparent;
    border: none;
    border-radius: var(--radius-md);
    color: var(--text-muted);
    cursor: pointer;
  }

  .close-btn:hover {
    background: var(--surface-hover);
    color: var(--text-primary);
  }

  .modal-form {
    padding: var(--space-4) var(--space-5);
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
  }

  .form-group {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }

  .form-group label {
    font-size: 13px;
    font-weight: 500;
    color: var(--text-secondary);
  }

  .form-group input,
  .form-group select {
    padding: var(--space-3);
    background: var(--surface-1);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    color: var(--text-primary);
    font-size: 14px;
  }

  .form-group input:focus,
  .form-group select:focus {
    outline: none;
    border-color: var(--accent-orange);
  }

  .file-selected {
    font-size: 13px;
    color: var(--text-secondary);
    font-family: var(--font-mono);
  }

  .form-hint {
    font-size: 12px;
    color: var(--text-dim);
  }

  .alert.error {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-3);
    border-radius: var(--radius-md);
    border: 1px solid var(--danger);
    color: var(--danger);
    background: var(--danger-muted);
    font-size: 13px;
  }

  .modal-actions {
    display: flex;
    justify-content: flex-end;
    gap: var(--space-3);
  }

  .btn-secondary,
  .btn-primary {
    padding: var(--space-2) var(--space-4);
    border-radius: var(--radius-md);
    font-size: 14px;
    cursor: pointer;
  }

  .btn-secondary {
    background: transparent;
    border: 1px solid var(--border-medium);
    color: var(--text-secondary);
  }

  .btn-secondary:hover {
    background: var(--surface-hover);
    color: var(--text-primary);
  }

  .btn-primary {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    border: none;
    background: var(--accent-orange);
    color: #111;
    font-weight: 600;
  }

  .btn-primary:hover:not(:disabled) {
    background: var(--accent-orange-hover);
  }

  .btn-primary:disabled,
  .btn-secondary:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }

  :global(svg.spin) {
    animation: spin 0.8s linear infinite;
  }

  @keyframes spin {
    from {
      transform: rotate(0deg);
    }
    to {
      transform: rotate(360deg);
    }
  }

  @media (max-width: 860px) {
    .page-header,
    .connectors-list {
      padding-left: var(--space-4);
      padding-right: var(--space-4);
    }

    .header-content {
      flex-direction: column;
      align-items: stretch;
    }

    .header-actions {
      justify-content: space-between;
    }

    .connector-details {
      flex-direction: column;
      align-items: flex-start;
    }

    .last-connected {
      white-space: normal;
    }
  }
</style>
