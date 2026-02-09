<script lang="ts">
  import type { WorldlineItem } from "$lib/types";
  import { createEventDispatcher } from "svelte";

  export let worldlines: WorldlineItem[] = [];
  export let activeWorldlineId = "";

  const dispatch = createEventDispatcher<{ select: { id: string } }>();

  function handleChange(event: Event): void {
    const target = event.target as HTMLSelectElement;
    dispatch("select", { id: target.value });
  }
</script>

<label class="picker">
  <span class="picker-label">Worldline</span>
  <select value={activeWorldlineId} on:change={handleChange}>
    {#each worldlines as line}
      <option value={line.id}>
        {line.name || line.id}
      </option>
    {/each}
  </select>
</label>

<style>
  .picker {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    border: 1px solid var(--border-soft);
    border-radius: 8px;
    padding: 6px 10px;
    background: var(--surface-2);
  }

  .picker-label {
    color: var(--text-muted);
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }

  select {
    background: var(--surface-2);
    color: var(--text-primary);
    border: none;
    font-family: var(--font-body);
    font-size: 14px;
    min-width: 180px;
  }

  select:focus {
    outline: none;
  }
</style>
