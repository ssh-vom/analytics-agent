<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import { createEventDispatcher } from "svelte";

  import type { WorldlineItem } from "$lib/types";

  interface TreeRow {
    line: WorldlineItem;
    depth: number;
    prefix: string;
  }

  export let worldlines: WorldlineItem[] = [];
  export let activeWorldlineId = "";

  const dispatch = createEventDispatcher<{ select: { id: string } }>();

  let isOpen = false;
  let containerElement: HTMLDivElement | null = null;

  $: activeWorldline =
    worldlines.find((line) => line.id === activeWorldlineId) ?? null;
  $: treeRows = buildTreeRows(worldlines);

  onMount(() => {
    document.addEventListener("mousedown", handleDocumentMouseDown);
  });

  onDestroy(() => {
    document.removeEventListener("mousedown", handleDocumentMouseDown);
  });

  function handleDocumentMouseDown(event: MouseEvent): void {
    if (!isOpen || !containerElement) {
      return;
    }
    const target = event.target;
    if (target instanceof Node && !containerElement.contains(target)) {
      isOpen = false;
    }
  }

  function handleSelectWorldline(id: string): void {
    isOpen = false;
    dispatch("select", { id });
  }

  function toggleOpen(): void {
    isOpen = !isOpen;
  }

  function sortWorldlines(lines: WorldlineItem[]): WorldlineItem[] {
    return [...lines].sort((a, b) => {
      const timeA = new Date(a.created_at).getTime();
      const timeB = new Date(b.created_at).getTime();
      if (timeA !== timeB) {
        return timeA - timeB;
      }
      return (a.name || a.id).localeCompare(b.name || b.id);
    });
  }

  function buildTreeRows(lines: WorldlineItem[]): TreeRow[] {
    const ordered = sortWorldlines(lines);
    const byId = new Map(ordered.map((line) => [line.id, line] as const));
    const childrenByParent = new Map<string, WorldlineItem[]>();
    const ROOT_KEY = "__root__";

    for (const line of ordered) {
      const parentId =
        line.parent_worldline_id && byId.has(line.parent_worldline_id)
          ? line.parent_worldline_id
          : ROOT_KEY;
      const bucket = childrenByParent.get(parentId) ?? [];
      bucket.push(line);
      childrenByParent.set(parentId, bucket);
    }

    const rows: TreeRow[] = [];
    const visited = new Set<string>();

    function traverse(nodes: WorldlineItem[], depth: number): void {
      nodes.forEach((line, index) => {
        if (visited.has(line.id)) {
          return;
        }
        visited.add(line.id);

        const isLast = index === nodes.length - 1;
        const prefix =
          depth === 0 ? "" : `${"  ".repeat(depth - 1)}${isLast ? "`- " : "|- "}`;
        rows.push({ line, depth, prefix });

        const children = childrenByParent.get(line.id) ?? [];
        if (children.length > 0) {
          traverse(children, depth + 1);
        }
      });
    }

    const roots = childrenByParent.get(ROOT_KEY) ?? [];
    traverse(roots, 0);

    // Safety pass for unexpected cycles/disconnected nodes.
    for (const line of ordered) {
      if (!visited.has(line.id)) {
        rows.push({ line, depth: 0, prefix: "" });
      }
    }

    return rows;
  }
</script>

<div class="picker" bind:this={containerElement}>
  <span class="picker-label">Worldline</span>
  <button type="button" class="picker-trigger" on:click={toggleOpen}>
    <span class="active-name">{activeWorldline?.name || activeWorldline?.id || "Select worldline"}</span>
    <span class="chevron" class:open={isOpen}>v</span>
  </button>

  {#if isOpen}
    <div class="picker-menu">
      {#if treeRows.length === 0}
        <div class="empty-state">No worldlines</div>
      {:else}
        {#each treeRows as row (row.line.id)}
          <button
            type="button"
            class="tree-row"
            class:active={row.line.id === activeWorldlineId}
            on:click={() => handleSelectWorldline(row.line.id)}
          >
            <span class="tree-prefix">{row.prefix}</span>
            <span class="tree-name">{row.line.name || row.line.id.slice(0, 12)}</span>
            {#if row.line.id === activeWorldlineId}
              <span class="active-pill">active</span>
            {/if}
          </button>
        {/each}
      {/if}
    </div>
  {/if}
</div>

<style>
  .picker {
    position: relative;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    padding: 4px 10px;
    background: var(--surface-1);
  }

  .picker-label {
    color: var(--text-dim);
    font-size: 10px;
    font-family: var(--font-mono);
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }

  .picker-trigger {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    background: transparent;
    border: none;
    color: var(--text-primary);
    font-family: var(--font-mono);
    font-size: 13px;
    min-width: 180px;
    cursor: pointer;
    text-align: left;
    padding: 0;
  }

  .active-name {
    flex: 1;
    min-width: 0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .chevron {
    color: var(--text-dim);
    transition: transform var(--transition-fast);
    font-size: 10px;
    line-height: 1;
  }

  .chevron.open {
    transform: rotate(180deg);
  }

  .picker-menu {
    position: absolute;
    top: calc(100% + 6px);
    left: 0;
    min-width: 320px;
    max-width: min(440px, 80vw);
    max-height: 340px;
    overflow-y: auto;
    background: var(--surface-0);
    border: 1px solid var(--border-medium);
    border-radius: var(--radius-md);
    box-shadow: var(--shadow-lg);
    padding: var(--space-2);
    z-index: 220;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .empty-state {
    padding: var(--space-2);
    color: var(--text-dim);
    font-size: 12px;
  }

  .tree-row {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    width: 100%;
    border: none;
    background: transparent;
    border-radius: var(--radius-sm);
    padding: 6px var(--space-2);
    color: var(--text-secondary);
    font-size: 13px;
    text-align: left;
    cursor: pointer;
    font-family: var(--font-mono);
  }

  .tree-row:hover {
    background: var(--surface-hover);
    color: var(--text-primary);
  }

  .tree-row.active {
    background: var(--accent-orange-muted);
    color: var(--text-primary);
  }

  .tree-prefix {
    color: var(--text-dim);
    white-space: pre;
    flex-shrink: 0;
  }

  .tree-name {
    flex: 1;
    min-width: 0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .active-pill {
    flex-shrink: 0;
    font-size: 10px;
    text-transform: uppercase;
    color: var(--accent-orange);
    border: 1px solid var(--accent-orange-muted);
    border-radius: var(--radius-sm);
    padding: 1px 6px;
  }
</style>
