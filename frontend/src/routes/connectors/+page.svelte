<script lang="ts">
  import { onMount } from "svelte";
  import type { Connector } from "$lib/types";
  import { Plus } from "lucide-svelte";
  import { Database } from "lucide-svelte";
  import { Trash2 } from "lucide-svelte";
  import { Check } from "lucide-svelte";
  import { X } from "lucide-svelte";

  let connectors: Connector[] = [];
  let showModal = false;
  let newConnectorName = "";
  let newConnectorType: Connector["type"] = "duckdb";
  let newConnectorString = "";

  onMount(() => {
    // Load connectors from localStorage
    const saved = localStorage.getItem("textql_connectors");
    if (saved) {
      connectors = JSON.parse(saved);
    } else {
      // Default connector
      connectors = [
        {
          id: "default",
          name: "local.db",
          type: "duckdb",
          connectionString: "file:local.db",
          isActive: true,
          lastConnected: new Date().toISOString(),
        },
      ];
      saveConnectors();
    }
  });

  function saveConnectors() {
    localStorage.setItem("textql_connectors", JSON.stringify(connectors));
  }

  function addConnector() {
    if (!newConnectorName || !newConnectorString) return;

    const newConnector: Connector = {
      id: crypto.randomUUID(),
      name: newConnectorName,
      type: newConnectorType,
      connectionString: newConnectorString,
      isActive: false,
      lastConnected: new Date().toISOString(),
    };

    connectors = [...connectors, newConnector];
    saveConnectors();
    
    // Reset form
    newConnectorName = "";
    newConnectorString = "";
    showModal = false;
  }

  function deleteConnector(id: string) {
    connectors = connectors.filter((c) => c.id !== id);
    saveConnectors();
  }

  function setActiveConnector(id: string) {
    connectors = connectors.map((c) => ({
      ...c,
      isActive: c.id === id,
    }));
    saveConnectors();
  }

  function formatDate(dateString: string): string {
    const date = new Date(dateString);
    return date.toLocaleDateString(undefined, {
      month: "numeric",
      day: "numeric",
      year: "numeric",
    }) + ", " + 
    date.toLocaleTimeString(undefined, {
      hour: "numeric",
      minute: "2-digit",
    });
  }

  function getTypeBadgeColor(type: string): string {
    switch (type) {
      case "sqlite":
        return "var(--accent-blue)";
      case "postgres":
        return "var(--accent-purple)";
      case "duckdb":
        return "var(--accent-orange)";
      case "mysql":
        return "var(--accent-cyan)";
      default:
        return "var(--text-muted)";
    }
  }
</script>

<div class="connectors-page">
  <header class="page-header">
    <div class="header-content">
      <div>
        <h1>Connectors</h1>
        <p class="subtitle">Manage database connections for your analysis sessions</p>
      </div>
      <button class="new-connector-btn" on:click={() => showModal = true}>
        <Plus size={18} />
        <span>New Connector</span>
      </button>
    </div>
  </header>

  <main class="connectors-list">
    {#each connectors as connector}
      <div class="connector-card" class:active={connector.isActive}>
        <div class="connector-header">
          <div class="connector-info">
            <Database size={20} />
            <span class="connector-name">{connector.name}</span>
            <span 
              class="type-badge"
              style="background: {getTypeBadgeColor(connector.type)}20; color: {getTypeBadgeColor(connector.type)}"
            >
              {connector.type.toUpperCase()}
            </span>
            {#if connector.isActive}
              <span class="status-badge active">
                <span class="status-dot"></span>
                Active
              </span>
            {/if}
          </div>
          <div class="connector-actions">
            {#if !connector.isActive}
              <button 
                class="action-btn connect"
                on:click={() => setActiveConnector(connector.id)}
              >
                <Check size={16} />
                <span>Connect</span>
              </button>
            {:else}
              <button 
                class="action-btn disconnect"
                on:click={() => setActiveConnector("")}
              >
                <X size={16} />
                <span>Disconnect</span>
              </button>
            {/if}
            <button 
              class="action-btn delete"
              on:click={() => deleteConnector(connector.id)}
            >
              <Trash2 size={16} />
            </button>
          </div>
        </div>
        <div class="connector-details">
          <code class="connection-string">{connector.connectionString}</code>
          <span class="last-connected">
            Last connected: {formatDate(connector.lastConnected || connector.id)}
          </span>
        </div>
      </div>
    {/each}

    {#if connectors.length === 0}
      <div class="empty-state">
        <Database size={48} />
        <h3>No connectors configured</h3>
        <p>Add a database connection to start analyzing your data</p>
        <button class="new-connector-btn" on:click={() => showModal = true}>
          <Plus size={18} />
          <span>New Connector</span>
        </button>
      </div>
    {/if}
  </main>
</div>

{#if showModal}
  <div class="modal-overlay" on:click|self={() => showModal = false}>
    <div class="modal">
      <header class="modal-header">
        <h2>New Connector</h2>
        <button class="close-btn" on:click={() => showModal = false}>
          <X size={20} />
        </button>
      </header>
      
      <form class="modal-form" on:submit|preventDefault={addConnector}>
        <div class="form-group">
          <label for="name">Name</label>
          <input 
            id="name"
            type="text" 
            bind:value={newConnectorName}
            placeholder="e.g., Production DB"
            required
          />
        </div>
        
        <div class="form-group">
          <label for="type">Database Type</label>
          <select id="type" bind:value={newConnectorType}>
            <option value="duckdb">DuckDB</option>
            <option value="sqlite">SQLite</option>
            <option value="postgres">PostgreSQL</option>
            <option value="mysql">MySQL</option>
          </select>
        </div>
        
        <div class="form-group">
          <label for="connection">Connection String</label>
          <input 
            id="connection"
            type="text" 
            bind:value={newConnectorString}
            placeholder="e.g., file:mydb.db or postgresql://localhost:5432/mydb"
            required
          />
        </div>
        
        <div class="modal-actions">
          <button type="button" class="btn-secondary" on:click={() => showModal = false}>
            Cancel
          </button>
          <button type="submit" class="btn-primary">
            Add Connector
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
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: var(--space-4);
    max-width: 1200px;
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

  .new-connector-btn {
    display: flex;
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
    box-shadow: var(--shadow-md);
  }

  .connectors-list {
    padding: var(--space-6) var(--space-8);
    max-width: 1200px;
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
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

  .connector-card.active {
    border-color: var(--border-accent);
    background: linear-gradient(135deg, var(--surface-0) 0%, var(--accent-orange-muted) 100%);
  }

  .connector-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: var(--space-3);
  }

  .connector-info {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    color: var(--text-secondary);
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
  }

  .status-badge {
    display: flex;
    align-items: center;
    gap: var(--space-1);
    padding: var(--space-1) var(--space-2);
    background: var(--accent-cyan-muted);
    color: var(--accent-cyan);
    border-radius: var(--radius-md);
    font-size: 12px;
    font-weight: 500;
  }

  .status-badge.active .status-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--accent-cyan);
    animation: pulse 2s ease-in-out infinite;
  }

  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.5; }
  }

  .connector-actions {
    display: flex;
    align-items: center;
    gap: var(--space-2);
  }

  .action-btn {
    display: flex;
    align-items: center;
    gap: var(--space-1);
    padding: var(--space-2) var(--space-3);
    background: var(--surface-1);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    color: var(--text-secondary);
    font-size: 13px;
    cursor: pointer;
    transition: all var(--transition-fast);
  }

  .action-btn:hover {
    background: var(--surface-hover);
    border-color: var(--border-medium);
    color: var(--text-primary);
  }

  .action-btn.connect {
    background: var(--accent-cyan-muted);
    border-color: var(--accent-cyan);
    color: var(--accent-cyan);
  }

  .action-btn.connect:hover {
    background: var(--accent-cyan);
    color: #111;
  }

  .action-btn.disconnect {
    background: var(--border-soft);
  }

  .action-btn.delete {
    padding: var(--space-2);
    color: var(--danger);
  }

  .action-btn.delete:hover {
    background: var(--danger-muted);
    border-color: var(--danger);
  }

  .connector-details {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-4);
  }

  .connection-string {
    padding: var(--space-2) var(--space-3);
    background: var(--surface-1);
    border-radius: var(--radius-md);
    color: var(--text-muted);
    font-family: var(--font-mono);
    font-size: 13px;
  }

  .last-connected {
    color: var(--text-dim);
    font-size: 13px;
  }

  .empty-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: var(--space-4);
    padding: var(--space-8);
    text-align: center;
    color: var(--text-muted);
  }

  .empty-state :global(svg) {
    color: var(--accent-orange);
    opacity: 0.5;
  }

  .empty-state h3 {
    margin: 0;
    color: var(--text-primary);
    font-size: 18px;
    font-weight: 500;
  }

  .empty-state p {
    margin: 0;
    font-size: 14px;
  }

  /* Modal */
  .modal-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.7);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
    padding: var(--space-4);
  }

  .modal {
    width: 100%;
    max-width: 480px;
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
    display: flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    background: transparent;
    border: none;
    border-radius: var(--radius-md);
    color: var(--text-muted);
    cursor: pointer;
    transition: all var(--transition-fast);
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
    transition: all var(--transition-fast);
  }

  .form-group input:focus,
  .form-group select:focus {
    outline: none;
    border-color: var(--accent-orange);
  }

  .form-group input::placeholder {
    color: var(--text-dim);
  }

  .modal-actions {
    display: flex;
    justify-content: flex-end;
    gap: var(--space-3);
    padding-top: var(--space-2);
  }

  .btn-secondary {
    padding: var(--space-2) var(--space-4);
    background: transparent;
    border: 1px solid var(--border-medium);
    border-radius: var(--radius-md);
    color: var(--text-secondary);
    font-size: 14px;
    cursor: pointer;
    transition: all var(--transition-fast);
  }

  .btn-secondary:hover {
    background: var(--surface-hover);
    border-color: var(--border-strong);
    color: var(--text-primary);
  }

  .btn-primary {
    padding: var(--space-2) var(--space-4);
    background: var(--accent-orange);
    border: none;
    border-radius: var(--radius-md);
    color: #111;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    transition: all var(--transition-fast);
  }

  .btn-primary:hover {
    background: var(--accent-orange-hover);
    transform: translateY(-1px);
  }
</style>
