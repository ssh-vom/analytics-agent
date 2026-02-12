<script lang="ts">
  export let columns: Array<{ name: string; type: string }> = [];
  export let rows: unknown[][] = [];
</script>

{#if columns.length > 0}
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          {#each columns as column}
            <th title={column.type}>{column.name}</th>
          {/each}
        </tr>
      </thead>
      <tbody>
        {#each rows as row}
          <tr>
            {#each row as value}
              <td>{value === null ? "NULL" : String(value)}</td>
            {/each}
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
{/if}

<style>
  .table-wrap {
    overflow-x: auto;
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-lg);
    background: var(--surface-0);
  }

  table {
    width: 100%;
    border-collapse: collapse;
    min-width: 500px;
    font-size: 13px;
  }

  th,
  td {
    padding: 10px 14px;
    border-bottom: 1px solid var(--border-soft);
    text-align: left;
    white-space: nowrap;
  }

  th {
    color: var(--text-dim);
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    background: var(--surface-1);
    position: sticky;
    top: 0;
    z-index: 1;
  }

  td {
    color: var(--text-primary);
    font-family: var(--font-mono);
    font-size: 13px;
  }

  tbody tr:last-child td {
    border-bottom: none;
  }

  tbody tr:hover td {
    background: var(--surface-hover);
  }

  tbody tr {
    transition: background var(--transition-fast);
  }
</style>
