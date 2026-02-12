<script lang="ts">
  import { onMount } from "svelte";
  import {
    SvelteFlow,
    Controls,
    Background,
    type Node,
    type Edge,
  } from "@xyflow/svelte";
  import "@xyflow/svelte/dist/style.css";
  import {
    fetchWorldlineSchema,
    fetchSemanticOverrides,
    saveSemanticOverrides,
    invalidateSemanticCache,
    type SemanticOverride,
  } from "$lib/api/client";
  import {
    AlertCircle,
    ChevronDown,
    ChevronRight,
    Database,
    Loader2,
    Save,
    RotateCcw,
    Table,
  } from "lucide-svelte";

  // Types
  type ColumnRole = "dimension" | "measure" | "time" | "unknown";

  interface ColumnInfo {
    name: string;
    type: string;
    inferredRole: ColumnRole;
    currentRole: ColumnRole;
  }

  interface TableInfo {
    name: string;
    schema: string;
    columns: ColumnInfo[];
    expanded: boolean;
  }

  // State
  let worldlineId = "";
  let loading = true;
  let saving = false;
  let errorMessage = "";
  let successMessage = "";
  let tables: TableInfo[] = [];
  let originalOverrides: Map<string, ColumnRole> = new Map();
  let pendingChanges: Map<string, ColumnRole> = new Map();

  // Flow diagram state
  let nodes: Node[] = [];
  let edges: Edge[] = [];

  // Infer column role based on type and name (matching backend logic)
  function inferColumnRole(columnName: string, dataType: string): ColumnRole {
    const nameLower = columnName.toLowerCase();
    const typeLower = dataType.toLowerCase();

    // Time-related types
    const timestampTypes = ["timestamp", "date", "time", "interval"];
    if (timestampTypes.some((t) => typeLower.includes(t))) {
      return "time";
    }

    // Time-related names
    const timePatterns = ["date", "time", "timestamp", "created", "updated", "day", "month", "year"];
    if (timePatterns.some((p) => nameLower.includes(p))) {
      return "time";
    }

    // ID columns are dimensions
    if (nameLower.endsWith("_id") || nameLower === "id") {
      return "dimension";
    }

    // Numeric types are measures
    const numericTypes = [
      "tinyint", "smallint", "integer", "bigint", "hugeint",
      "real", "double", "float", "decimal", "numeric",
    ];
    if (numericTypes.some((t) => typeLower.includes(t))) {
      return "measure";
    }

    // Text types are dimensions
    const textTypes = ["varchar", "text", "string", "enum"];
    if (textTypes.some((t) => typeLower.includes(t))) {
      return "dimension";
    }

    return "unknown";
  }

  // Build key for override map
  function overrideKey(tableName: string, columnName: string): string {
    return `${tableName}::${columnName}`;
  }

  // Check if there are unsaved changes
  $: hasUnsavedChanges = pendingChanges.size > 0;

  // Build flow diagram from tables
  function buildFlowDiagram(tableList: TableInfo[]) {
    const newNodes: Node[] = [];
    const newEdges: Edge[] = [];

    // Calculate grid positions
    const columns = Math.ceil(Math.sqrt(tableList.length));
    const nodeWidth = 180;
    const nodeHeight = 60;
    const gapX = 100;
    const gapY = 80;

    tableList.forEach((table, index) => {
      const col = index % columns;
      const row = Math.floor(index / columns);

      newNodes.push({
        id: table.name,
        type: "default",
        position: {
          x: col * (nodeWidth + gapX) + 50,
          y: row * (nodeHeight + gapY) + 50,
        },
        data: { label: table.name.split(".").pop() || table.name },
        style: `
          background: var(--surface-1);
          border: 1px solid var(--border-soft);
          border-radius: 8px;
          padding: 10px 16px;
          font-family: var(--font-mono);
          font-size: 13px;
          color: var(--text-primary);
          cursor: pointer;
        `,
      });
    });

    // Detect foreign key relationships by naming convention
    tableList.forEach((table) => {
      table.columns.forEach((col) => {
        const colLower = col.name.toLowerCase();
        if (colLower.endsWith("_id") && colLower !== "id") {
          // Extract potential table name: customer_id -> customer
          const potentialTable = colLower.slice(0, -3);

          // Find matching table
          const targetTable = tableList.find((t) => {
            const tName = (t.name.split(".").pop() || t.name).toLowerCase();
            // Match singular/plural
            return (
              tName === potentialTable ||
              tName === potentialTable + "s" ||
              tName === potentialTable + "es" ||
              tName.replace(/s$/, "") === potentialTable ||
              tName.replace(/es$/, "") === potentialTable
            );
          });

          if (targetTable && targetTable.name !== table.name) {
            newEdges.push({
              id: `${table.name}-${targetTable.name}-${col.name}`,
              source: table.name,
              target: targetTable.name,
              type: "default",
              animated: false,
              style: "stroke: var(--text-dim); stroke-dasharray: 5,5;",
              label: "inferred",
              labelStyle: "font-size: 10px; fill: var(--text-dim);",
            });
          }
        }
      });
    });

    nodes = newNodes;
    edges = newEdges;
  }

  // Load data on mount
  onMount(() => {
    const saved = localStorage.getItem("textql_active_worldline");
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
    successMessage = "";

    try {
      // Fetch schema and overrides in parallel
      const [schemaData, overridesData] = await Promise.all([
        fetchWorldlineSchema(worldlineId),
        fetchSemanticOverrides(worldlineId),
      ]);

      // Build override map from saved overrides
      originalOverrides = new Map();
      overridesData.overrides.forEach((o) => {
        originalOverrides.set(overrideKey(o.table_name, o.column_name), o.role as ColumnRole);
      });

      // Clear pending changes
      pendingChanges = new Map();

      // Build table list with column info
      const tableList: TableInfo[] = [];

      // Add native tables
      for (const table of schemaData.native_tables) {
        const columns: ColumnInfo[] = table.columns.map((col) => {
          const key = overrideKey(table.name, col.name);
          const inferredRole = inferColumnRole(col.name, col.type);
          const savedRole = originalOverrides.get(key);

          return {
            name: col.name,
            type: col.type,
            inferredRole,
            currentRole: savedRole || inferredRole,
          };
        });

        tableList.push({
          name: table.name,
          schema: table.schema,
          columns,
          expanded: false,
        });
      }

      // Add imported tables (they should also appear in native_tables, but just in case)
      // Skip since they're already included

      tables = tableList;
      buildFlowDiagram(tableList);
    } catch (error) {
      errorMessage = error instanceof Error ? error.message : "Failed to load schema";
    } finally {
      loading = false;
    }
  }

  function handleRoleChange(tableName: string, columnName: string, newRole: ColumnRole) {
    const key = overrideKey(tableName, columnName);
    const table = tables.find((t) => t.name === tableName);
    if (!table) return;

    const column = table.columns.find((c) => c.name === columnName);
    if (!column) return;

    // Update the column's current role
    column.currentRole = newRole;

    // Track change if different from original
    const originalRole = originalOverrides.get(key) || column.inferredRole;
    if (newRole !== originalRole) {
      // Only save override if different from inferred
      if (newRole !== column.inferredRole) {
        pendingChanges.set(key, newRole);
      } else {
        // If set back to inferred, remove the override
        pendingChanges.delete(key);
        originalOverrides.delete(key);
      }
    } else {
      pendingChanges.delete(key);
    }

    // Trigger reactivity
    tables = [...tables];
    pendingChanges = new Map(pendingChanges);
  }

  async function handleSave() {
    if (!worldlineId) return;

    saving = true;
    errorMessage = "";
    successMessage = "";

    try {
      // Build list of all overrides (original + pending changes)
      const allOverrides: SemanticOverride[] = [];

      // Start with original overrides
      originalOverrides.forEach((role, key) => {
        const [tableName, columnName] = key.split("::");
        if (!pendingChanges.has(key)) {
          allOverrides.push({ table_name: tableName, column_name: columnName, role });
        }
      });

      // Add pending changes
      pendingChanges.forEach((role, key) => {
        const [tableName, columnName] = key.split("::");
        // Only add if it's different from inferred (actual override)
        const table = tables.find((t) => t.name === tableName);
        const column = table?.columns.find((c) => c.name === columnName);
        if (column && role !== column.inferredRole) {
          allOverrides.push({ table_name: tableName, column_name: columnName, role });
        }
      });

      // Save to backend
      await saveSemanticOverrides(worldlineId, allOverrides);

      // Invalidate semantic cache
      await invalidateSemanticCache(worldlineId);

      // Update original overrides and clear pending
      originalOverrides = new Map();
      allOverrides.forEach((o) => {
        originalOverrides.set(overrideKey(o.table_name, o.column_name), o.role as ColumnRole);
      });
      pendingChanges = new Map();

      successMessage = `Saved ${allOverrides.length} column role override${allOverrides.length !== 1 ? "s" : ""}`;
    } catch (error) {
      errorMessage = error instanceof Error ? error.message : "Failed to save overrides";
    } finally {
      saving = false;
    }
  }

  function handleReset() {
    // Reset all pending changes
    pendingChanges = new Map();

    // Reset column roles to original or inferred
    tables = tables.map((table) => ({
      ...table,
      columns: table.columns.map((col) => {
        const key = overrideKey(table.name, col.name);
        const savedRole = originalOverrides.get(key);
        return {
          ...col,
          currentRole: savedRole || col.inferredRole,
        };
      }),
    }));
  }

  function toggleTable(tableName: string) {
    tables = tables.map((t) =>
      t.name === tableName ? { ...t, expanded: !t.expanded } : t
    );
  }

  function handleNodeClick(event: CustomEvent<{ node: Node }>) {
    const nodeName = event.detail.node.id;
    // Find and expand the table, scroll to it
    tables = tables.map((t) => ({
      ...t,
      expanded: t.name === nodeName ? true : t.expanded,
    }));

    // Scroll to the table in the editor
    setTimeout(() => {
      const element = document.getElementById(`table-${nodeName}`);
      if (element) {
        element.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    }, 100);
  }

  function getRoleBadgeClass(role: ColumnRole): string {
    switch (role) {
      case "dimension":
        return "badge-dimension";
      case "measure":
        return "badge-measure";
      case "time":
        return "badge-time";
      default:
        return "badge-unknown";
    }
  }
</script>

<div class="schema-page">
  <header class="page-header">
    <div class="header-content">
      <div>
        <h1>Schema Editor</h1>
        <p class="subtitle">Configure column roles for the semantic layer.</p>
      </div>
      {#if worldlineId}
        <span class="worldline-badge">Worldline: {worldlineId.slice(0, 12)}</span>
      {/if}
    </div>
  </header>

  <main class="schema-content">
    {#if loading}
      <div class="loading-state">
        <Loader2 size={18} class="spin" />
        <span>Loading schema...</span>
      </div>
    {:else if errorMessage && tables.length === 0}
      <div class="error-state">
        <AlertCircle size={16} />
        <span>{errorMessage}</span>
      </div>
    {:else}
      <!-- Flow Diagram Section -->
      <section class="flow-section">
        <div class="section-header">
          <Database size={18} />
          <h2>Table Relationships</h2>
          <span class="info-badge">Click a table to edit its columns</span>
        </div>
        <div class="flow-container">
          <SvelteFlow
            {nodes}
            {edges}
            fitView
            on:nodeclick={handleNodeClick}
          >
            <Controls />
            <Background />
          </SvelteFlow>
        </div>
        <div class="flow-legend">
          <span class="legend-item">
            <span class="legend-line dashed"></span>
            Inferred relationship
          </span>
        </div>
      </section>

      <!-- Column Editor Section -->
      <section class="editor-section">
        <div class="section-header">
          <Table size={18} />
          <h2>Column Editor</h2>
          <span class="table-count">{tables.length} tables</span>
          <div class="header-actions">
            {#if hasUnsavedChanges}
              <span class="unsaved-indicator">Unsaved changes</span>
            {/if}
            <button
              class="action-btn"
              on:click={handleReset}
              disabled={!hasUnsavedChanges || saving}
              title="Reset changes"
            >
              <RotateCcw size={14} />
              <span>Reset</span>
            </button>
            <button
              class="action-btn primary"
              on:click={handleSave}
              disabled={!hasUnsavedChanges || saving}
            >
              {#if saving}
                <Loader2 size={14} class="spin" />
                <span>Saving...</span>
              {:else}
                <Save size={14} />
                <span>Save</span>
              {/if}
            </button>
          </div>
        </div>

        {#if errorMessage}
          <div class="alert error">
            <AlertCircle size={14} />
            <span>{errorMessage}</span>
          </div>
        {/if}

        {#if successMessage}
          <div class="alert success">
            <span>{successMessage}</span>
          </div>
        {/if}

        <div class="tables-list">
          {#each tables as table (table.name)}
            <div class="table-card" id="table-{table.name}">
              <button
                class="table-header"
                on:click={() => toggleTable(table.name)}
              >
                {#if table.expanded}
                  <ChevronDown size={16} />
                {:else}
                  <ChevronRight size={16} />
                {/if}
                <span class="table-name">{table.name}</span>
                <span class="column-count">{table.columns.length} columns</span>
              </button>

              {#if table.expanded}
                <div class="columns-table">
                  <div class="columns-header">
                    <span class="col-name">Column</span>
                    <span class="col-type">Type</span>
                    <span class="col-inferred">Inferred</span>
                    <span class="col-role">Role</span>
                  </div>
                  {#each table.columns as column (column.name)}
                    {@const key = overrideKey(table.name, column.name)}
                    {@const isModified = pendingChanges.has(key)}
                    <div class="column-row" class:modified={isModified}>
                      <span class="col-name" title={column.name}>{column.name}</span>
                      <span class="col-type" title={column.type}>{column.type}</span>
                      <span class="col-inferred">
                        <span class="badge {getRoleBadgeClass(column.inferredRole)}">
                          {column.inferredRole}
                        </span>
                      </span>
                      <span class="col-role">
                        <select
                          value={column.currentRole}
                          on:change={(e) =>
                            handleRoleChange(
                              table.name,
                              column.name,
                              e.currentTarget.value as ColumnRole
                            )
                          }
                          class:modified={isModified}
                        >
                          <option value="dimension">dimension</option>
                          <option value="measure">measure</option>
                          <option value="time">time</option>
                          <option value="unknown">unknown</option>
                        </select>
                      </span>
                    </div>
                  {/each}
                </div>
              {/if}
            </div>
          {/each}

          {#if tables.length === 0}
            <div class="empty-state">
              <Table size={24} />
              <span>No tables available. Import data first.</span>
            </div>
          {/if}
        </div>
      </section>
    {/if}
  </main>
</div>

<style>
  .schema-page {
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
    max-width: 1400px;
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

  .schema-content {
    padding: var(--space-6) var(--space-8);
    max-width: 1400px;
    display: flex;
    flex-direction: column;
    gap: var(--space-6);
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

  /* Flow Diagram Section */
  .flow-section {
    background: var(--surface-0);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-lg);
    overflow: hidden;
  }

  .section-header {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-4) var(--space-5);
    border-bottom: 1px solid var(--border-soft);
    color: var(--text-secondary);
  }

  .section-header h2 {
    margin: 0;
    font-size: 16px;
    font-weight: 600;
    color: var(--text-primary);
  }

  .info-badge {
    margin-left: auto;
    font-size: 12px;
    color: var(--text-dim);
    padding: 4px 8px;
    background: var(--surface-1);
    border-radius: var(--radius-sm);
  }

  .flow-container {
    height: 300px;
    background: var(--bg-0);
  }

  .flow-container :global(.svelte-flow) {
    background: var(--bg-0);
  }

  .flow-container :global(.svelte-flow__node) {
    background: var(--surface-1);
    border: 1px solid var(--border-soft);
    border-radius: 8px;
    padding: 10px 16px;
    font-family: var(--font-mono);
    font-size: 13px;
    color: var(--text-primary);
  }

  .flow-container :global(.svelte-flow__node:hover) {
    border-color: var(--accent-cyan);
  }

  .flow-container :global(.svelte-flow__edge-path) {
    stroke: var(--text-dim);
    stroke-dasharray: 5, 5;
  }

  .flow-container :global(.svelte-flow__controls) {
    background: var(--surface-1);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
  }

  .flow-container :global(.svelte-flow__controls button) {
    background: var(--surface-0);
    border-color: var(--border-soft);
    color: var(--text-secondary);
  }

  .flow-legend {
    display: flex;
    gap: var(--space-4);
    padding: var(--space-3) var(--space-5);
    border-top: 1px solid var(--border-soft);
    background: var(--surface-1);
  }

  .legend-item {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    font-size: 12px;
    color: var(--text-dim);
  }

  .legend-line {
    width: 24px;
    height: 2px;
    background: var(--text-dim);
  }

  .legend-line.dashed {
    background: transparent;
    border-top: 2px dashed var(--text-dim);
  }

  /* Editor Section */
  .editor-section {
    background: var(--surface-0);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-lg);
  }

  .table-count {
    font-size: 13px;
    color: var(--text-dim);
  }

  .header-actions {
    margin-left: auto;
    display: flex;
    align-items: center;
    gap: var(--space-3);
  }

  .unsaved-indicator {
    font-size: 12px;
    color: var(--accent-orange);
    padding: 4px 8px;
    background: var(--accent-orange-muted);
    border-radius: var(--radius-sm);
  }

  .action-btn {
    padding: var(--space-2) var(--space-3);
    background: var(--surface-1);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    color: var(--text-secondary);
    font-size: 13px;
    display: inline-flex;
    align-items: center;
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

  .alert {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    margin: var(--space-4) var(--space-5);
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

  .tables-list {
    padding: var(--space-4);
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
  }

  .table-card {
    background: var(--surface-1);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    overflow: hidden;
  }

  .table-header {
    width: 100%;
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-3) var(--space-4);
    background: transparent;
    border: none;
    color: var(--text-primary);
    font-size: 14px;
    cursor: pointer;
    transition: background var(--transition-fast);
  }

  .table-header:hover {
    background: var(--surface-hover);
  }

  .table-name {
    font-family: var(--font-mono);
    font-weight: 500;
  }

  .column-count {
    margin-left: auto;
    font-size: 12px;
    color: var(--text-dim);
  }

  .columns-table {
    border-top: 1px solid var(--border-soft);
  }

  .columns-header {
    display: grid;
    grid-template-columns: 1fr 120px 100px 120px;
    gap: var(--space-3);
    padding: var(--space-2) var(--space-4);
    background: var(--surface-0);
    font-size: 11px;
    font-weight: 600;
    color: var(--text-dim);
    text-transform: uppercase;
  }

  .column-row {
    display: grid;
    grid-template-columns: 1fr 120px 100px 120px;
    gap: var(--space-3);
    padding: var(--space-2) var(--space-4);
    font-size: 13px;
    border-top: 1px solid var(--border-soft);
    transition: background var(--transition-fast);
  }

  .column-row:hover {
    background: var(--surface-hover);
  }

  .column-row.modified {
    background: var(--accent-cyan-muted);
  }

  .col-name {
    font-family: var(--font-mono);
    color: var(--text-primary);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .col-type {
    font-family: var(--font-mono);
    color: var(--text-dim);
    font-size: 12px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .col-inferred {
    display: flex;
    align-items: center;
  }

  .col-role select {
    width: 100%;
    padding: 4px 8px;
    background: var(--surface-0);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-sm);
    color: var(--text-primary);
    font-size: 12px;
    cursor: pointer;
  }

  .col-role select:focus {
    outline: none;
    border-color: var(--accent-cyan);
  }

  .col-role select.modified {
    border-color: var(--accent-cyan);
    background: var(--accent-cyan-muted);
  }

  .badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: var(--radius-sm);
    font-size: 11px;
    font-weight: 500;
    text-transform: uppercase;
  }

  .badge-dimension {
    background: var(--accent-purple-muted, rgba(139, 92, 246, 0.15));
    color: var(--accent-purple, #a78bfa);
    border: 1px solid var(--accent-purple, #a78bfa);
  }

  .badge-measure {
    background: var(--accent-cyan-muted);
    color: var(--accent-cyan);
    border: 1px solid var(--accent-cyan);
  }

  .badge-time {
    background: var(--accent-orange-muted);
    color: var(--accent-orange);
    border: 1px solid var(--accent-orange);
  }

  .badge-unknown {
    background: var(--surface-0);
    color: var(--text-dim);
    border: 1px solid var(--border-soft);
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

  :global(.spin) {
    animation: spin 1s linear infinite;
  }

  @keyframes spin {
    from {
      transform: rotate(0deg);
    }
    to {
      transform: rotate(360deg);
    }
  }
</style>
