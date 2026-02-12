<script lang="ts">
  import { onMount } from "svelte";
  import {
    importCSV,
    attachExternalDuckDB,
    detachExternalDuckDB,
    fetchWorldlineSchema,
    fetchWorldlineTables,
  } from "$lib/api/client";
  import {
    AlertCircle,
    Database,
    FileSpreadsheet,
    Loader2,
    Plus,
    Table,
    Upload,
    X,
  } from "lucide-svelte";
  import { getActiveWorldlineFromStorage } from "$lib/chat/activeWorldline";

  let worldlineId = "";
  let loading = true;
  let errorMessage = "";
  let schema: Awaited<ReturnType<typeof fetchWorldlineSchema>> | null = null;
  let tables: Awaited<ReturnType<typeof fetchWorldlineTables>> | null = null;

  // Import CSV state
  let importFile: File | null = null;
  let importTableName = "";
  let importIfExists: "fail" | "replace" | "append" = "fail";
  let importing = false;
  let importError = "";
  let importSuccess: { table_name: string; row_count: number } | null = null;

  // Attach DB state
  let attachDbPath = "";
  let attachAlias = "";
  let attaching = false;
  let attachError = "";
  let attachSuccess: { alias: string } | null = null;

  onMount(() => {
    const saved = getActiveWorldlineFromStorage();
    if (saved) {
      worldlineId = saved;
      loadData();
    } else {
      loading = false;
      errorMessage = "No active worldline selected. Please switch to a worldline first.";
    }
  });

  async function loadData() {
    if (!worldlineId) return;

    loading = true;
    errorMessage = "";

    try {
      const [schemaData, tablesData] = await Promise.all([
        fetchWorldlineSchema(worldlineId),
        fetchWorldlineTables(worldlineId),
      ]);
      schema = schemaData;
      tables = tablesData;
    } catch (error) {
      errorMessage = error instanceof Error ? error.message : "Failed to load data";
    } finally {
      loading = false;
    }
  }

  function handleFileSelect(event: Event) {
    const input = event.target as HTMLInputElement;
    if (input.files && input.files[0]) {
      importFile = input.files[0];
      // Auto-suggest table name from filename
      if (!importTableName) {
        importTableName = input.files[0].name.replace(/\.csv$/i, "").replace(/[^a-zA-Z0-9_]/g, "_");
      }
    }
  }

  async function handleImport() {
    if (!importFile || !worldlineId) return;

    importing = true;
    importError = "";
    importSuccess = null;

    try {
      const result = await importCSV(
        worldlineId,
        importFile,
        importTableName || undefined,
        importIfExists
      );
      importSuccess = { table_name: result.table_name, row_count: result.row_count };
      importFile = null;
      importTableName = "";
      // Refresh data
      await loadData();
    } catch (error) {
      importError = error instanceof Error ? error.message : "Import failed";
    } finally {
      importing = false;
    }
  }

  async function handleAttach() {
    if (!attachDbPath || !worldlineId) return;

    attaching = true;
    attachError = "";
    attachSuccess = null;

    try {
      const result = await attachExternalDuckDB(
        worldlineId,
        attachDbPath,
        attachAlias || undefined
      );
      attachSuccess = { alias: result.alias };
      attachDbPath = "";
      attachAlias = "";
      // Refresh data
      await loadData();
    } catch (error) {
      attachError = error instanceof Error ? error.message : "Attach failed";
    } finally {
      attaching = false;
    }
  }

  async function handleDetach(alias: string) {
    if (!worldlineId || !confirm(`Detach database "${alias}"?`)) return;

    try {
      await detachExternalDuckDB(worldlineId, alias);
      await loadData();
    } catch (error) {
      errorMessage = error instanceof Error ? error.message : "Detach failed";
    }
  }

  function getTableIcon(type: string) {
    switch (type) {
      case "imported_csv":
        return FileSpreadsheet;
      case "external":
        return Database;
      default:
        return Table;
    }
  }
</script>

<div class="data-page">
  <header class="page-header">
    <div class="header-content">
      <div>
        <h1>Data Sources</h1>
        <p class="subtitle">Import CSV files and connect external databases to your worldline.</p>
      </div>
      {#if worldlineId}
        <span class="worldline-badge">Worldline: {worldlineId.slice(0, 12)}</span>
      {/if}
    </div>
  </header>

  <main class="data-content">
    {#if loading}
      <div class="loading-state">
        <Loader2 size={18} class="spin" />
        <span>Loading data sources...</span>
      </div>
    {:else if errorMessage}
      <div class="error-state">
        <AlertCircle size={16} />
        <span>{errorMessage}</span>
      </div>
    {:else}
      <div class="data-grid">
        <!-- CSV Import Section -->
        <section class="data-section">
          <div class="section-header">
            <FileSpreadsheet size={18} />
            <h2>Import CSV</h2>
          </div>

          <div class="import-form">
            <div class="form-group">
              <label for="csv-file">Select CSV file</label>
              <input
                type="file"
                id="csv-file"
                accept=".csv"
                on:change={handleFileSelect}
                disabled={importing}
              />
              {#if importFile}
                <span class="file-selected">{importFile.name}</span>
              {/if}
            </div>

            <div class="form-group">
              <label for="table-name">Table name (optional)</label>
              <input
                type="text"
                id="table-name"
                bind:value={importTableName}
                placeholder="Auto-generated from filename"
                disabled={importing}
              />
            </div>

            <div class="form-group">
              <label for="if-exists">If table exists</label>
              <select id="if-exists" bind:value={importIfExists} disabled={importing}>
                <option value="fail">Fail (don't overwrite)</option>
                <option value="replace">Replace existing</option>
                <option value="append">Append to existing</option>
              </select>
            </div>

            <button
              class="action-btn primary"
              on:click={handleImport}
              disabled={!importFile || importing}
            >
              {#if importing}
                <Loader2 size={16} class="spin" />
                <span>Importing...</span>
              {:else}
                <Upload size={16} />
                <span>Import CSV</span>
              {/if}
            </button>

            {#if importError}
              <div class="alert error">
                <AlertCircle size={14} />
                <span>{importError}</span>
              </div>
            {/if}

            {#if importSuccess}
              <div class="alert success">
                <span>
                  Imported {importSuccess.row_count.toLocaleString()} rows into table
                  <code>{importSuccess.table_name}</code>
                </span>
              </div>
            {/if}
          </div>
        </section>

        <!-- External DB Section -->
        <section class="data-section">
          <div class="section-header">
            <Database size={18} />
            <h2>Attach DuckDB</h2>
          </div>

          <div class="attach-form">
            <div class="form-group">
              <label for="db-path">Database file path</label>
              <input
                type="text"
                id="db-path"
                bind:value={attachDbPath}
                placeholder="/path/to/database.duckdb"
                disabled={attaching}
              />
            </div>

            <div class="form-group">
              <label for="db-alias">Alias (optional)</label>
              <input
                type="text"
                id="db-alias"
                bind:value={attachAlias}
                placeholder="Auto-generated from filename"
                disabled={attaching}
              />
            </div>

            <button
              class="action-btn primary"
              on:click={handleAttach}
              disabled={!attachDbPath || attaching}
            >
              {#if attaching}
                <Loader2 size={16} class="spin" />
                <span>Attaching...</span>
              {:else}
                <Plus size={16} />
                <span>Attach Database</span>
              {/if}
            </button>

            {#if attachError}
              <div class="alert error">
                <AlertCircle size={14} />
                <span>{attachError}</span>
              </div>
            {/if}

            {#if attachSuccess}
              <div class="alert success">
                <span>
                  Attached database as alias <code>{attachSuccess.alias}</code>
                </span>
              </div>
            {/if}
          </div>
        </section>
      </div>

      <!-- Tables List -->
      <section class="tables-section">
        <div class="section-header">
          <Table size={18} />
          <h2>Available Tables</h2>
          <span class="table-count">{tables?.count ?? 0} tables</span>
        </div>

        {#if tables && tables.tables.length > 0}
          <div class="tables-list">
            {#each tables.tables as table}
              {@const Icon = getTableIcon(table.type)}
              <div class="table-card">
                <div class="table-icon">
                  <Icon size={16} />
                </div>
                <div class="table-info">
                  <div class="table-name">{table.name}</div>
                  <div class="table-meta">
                    <span class="badge" class:imported={table.type === "imported_csv"} class:external={table.type === "external"}>
                      {table.type}
                    </span>
                    {#if table.row_count !== undefined}
                      <span>{table.row_count.toLocaleString()} rows</span>
                    {/if}
                    {#if table.source_filename}
                      <span>from {table.source_filename}</span>
                    {/if}
                    {#if table.source_db}
                      <span>via {table.source_db}</span>
                    {/if}
                  </div>
                  {#if table.columns && table.columns.length > 0}
                    <div class="table-columns">
                      {#each table.columns.slice(0, 5) as col}
                        <span class="column-tag">{col.name}</span>
                      {/each}
                      {#if table.columns.length > 5}
                        <span class="column-tag more">+{table.columns.length - 5} more</span>
                      {/if}
                    </div>
                  {/if}
                </div>
              </div>
            {/each}
          </div>
        {:else}
          <div class="empty-state">
            <Table size={24} />
            <span>No tables available yet. Import a CSV or attach a database to get started.</span>
          </div>
        {/if}
      </section>

      <!-- Attached Databases -->
      {#if schema && schema.attached_databases.length > 0}
        <section class="attached-section">
          <div class="section-header">
            <Database size={18} />
            <h2>Attached Databases</h2>
          </div>

          <div class="attached-list">
            {#each schema.attached_databases as db}
              <div class="db-card">
                <div class="db-info">
                  <div class="db-alias">{db.alias}</div>
                  <div class="db-path">{db.db_path}</div>
                  <div class="db-meta">
                    {db.tables.length} tables · attached {new Date(db.attached_at).toLocaleString()}
                  </div>
                </div>
                <button
                  class="action-btn danger"
                  on:click={() => handleDetach(db.alias)}
                  title="Detach database"
                >
                  <X size={14} />
                </button>
              </div>
            {/each}
          </div>
        </section>
      {/if}
    {/if}
  </main>
</div>

<style>
  .data-page {
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
    align-items: center;
    justify-content: space-between;
    gap: var(--space-3);
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

  .data-content {
    padding: var(--space-6) var(--space-8);
    max-width: 1200px;
    display: flex;
    flex-direction: column;
    gap: var(--space-8);
  }

  .loading-state,
  .error-state {
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

  .data-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
    gap: var(--space-6);
  }

  .data-section {
    padding: var(--space-5);
    background: var(--surface-0);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-lg);
  }

  .section-header {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    margin-bottom: var(--space-4);
    color: var(--text-secondary);
  }

  .section-header h2 {
    margin: 0;
    font-size: 16px;
    font-weight: 600;
    color: var(--text-primary);
  }

  .table-count {
    margin-left: auto;
    font-size: 13px;
    color: var(--text-dim);
  }

  .import-form,
  .attach-form {
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
  }

  .form-group {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
  }

  .form-group label {
    font-size: 13px;
    font-weight: 500;
    color: var(--text-secondary);
  }

  .form-group input,
  .form-group select {
    padding: var(--space-2) var(--space-3);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    background: var(--surface-1);
    color: var(--text-primary);
    font-size: 14px;
  }

  .form-group input:focus,
  .form-group select:focus {
    outline: none;
    border-color: var(--accent-cyan);
  }

  .file-selected {
    font-size: 13px;
    color: var(--text-muted);
  }

  .action-btn {
    padding: var(--space-2) var(--space-3);
    background: var(--surface-1);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    color: var(--text-secondary);
    font-size: 14px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: var(--space-2);
    cursor: pointer;
    transition: all var(--transition-fast);
  }

  .action-btn:hover:not(:disabled) {
    background: var(--surface-hover);
    border-color: var(--border-medium);
    color: var(--text-primary);
  }

  .action-btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .action-btn.primary {
    background: var(--accent-cyan-muted);
    border-color: var(--accent-cyan);
    color: var(--accent-cyan);
  }

  .action-btn.primary:hover:not(:disabled) {
    background: var(--accent-cyan);
    color: #111;
  }

  .action-btn.danger {
    background: transparent;
    border-color: transparent;
    color: var(--danger);
    padding: var(--space-1);
  }

  .action-btn.danger:hover {
    background: var(--danger-muted);
    border-color: var(--danger);
  }

  .alert {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-3);
    border-radius: var(--radius-md);
    font-size: 13px;
  }

  .alert.error {
    background: var(--danger-muted);
    border: 1px solid var(--danger);
    color: var(--danger);
  }

  .alert.success {
    background: var(--success-muted, var(--surface-1));
    border: 1px solid var(--success, var(--accent-cyan));
    color: var(--success, var(--accent-cyan));
  }

  .alert code {
    font-family: var(--font-mono);
    background: rgba(0, 0, 0, 0.1);
    padding: 2px 6px;
    border-radius: var(--radius-sm);
  }

  .tables-section,
  .attached-section {
    padding: var(--space-5);
    background: var(--surface-0);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-lg);
  }

  .tables-list,
  .attached-list {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
  }

  .table-card,
  .db-card {
    display: flex;
    align-items: flex-start;
    gap: var(--space-3);
    padding: var(--space-3);
    background: var(--surface-1);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
  }

  .table-icon {
    padding: var(--space-2);
    background: var(--surface-0);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-sm);
    color: var(--text-muted);
  }

  .table-info,
  .db-info {
    flex: 1;
    min-width: 0;
  }

  .table-name,
  .db-alias {
    font-weight: 500;
    color: var(--text-primary);
    font-family: var(--font-mono);
    font-size: 14px;
  }

  .table-meta,
  .db-meta {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    margin-top: var(--space-1);
    font-size: 13px;
    color: var(--text-dim);
  }

  .badge {
    padding: 2px 8px;
    background: var(--surface-0);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-sm);
    font-size: 11px;
    text-transform: uppercase;
    font-weight: 500;
  }

  .badge.imported {
    background: var(--accent-cyan-muted);
    border-color: var(--accent-cyan);
    color: var(--accent-cyan);
  }

  .badge.external {
    background: var(--accent-orange-muted);
    border-color: var(--accent-orange);
    color: var(--accent-orange);
  }

  .table-columns {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-1);
    margin-top: var(--space-2);
  }

  .column-tag {
    padding: 2px 6px;
    background: var(--surface-0);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-sm);
    font-size: 11px;
    color: var(--text-dim);
    font-family: var(--font-mono);
  }

  .column-tag.more {
    background: transparent;
    border-style: dashed;
  }

  .db-path {
    font-size: 12px;
    color: var(--text-dim);
    font-family: var(--font-mono);
    margin-top: var(--space-1);
    word-break: break-all;
  }

  .empty-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--space-3);
    padding: var(--space-8);
    color: var(--text-muted);
    text-align: center;
  }

</style>
