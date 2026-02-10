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

  select {
    background: var(--surface-1);
    color: var(--text-primary);
    border: none;
    font-family: var(--font-mono);
    font-size: 13px;
    min-width: 160px;
  }

  select:focus {
    outline: none;
  }
</style>
