<script lang="ts">
  export let role: "user" | "assistant" | "plan" = "assistant";
  export let text = "";
  export let createdAt = "";
  export let onBranch: (() => void) | null = null;

  $: roleLabel = role === "plan" ? "PLAN" : role.toUpperCase();
</script>

<article class="message">
  <header>
    <span class={`role ${role}`}>{roleLabel}</span>
    <time>{new Date(createdAt).toLocaleTimeString()}</time>
    {#if onBranch}
      <button type="button" class="branch" on:click={onBranch}>Branch from here</button>
    {/if}
  </header>
  <p>{text}</p>
</article>

<style>
  .message {
    border: 1px solid var(--border-soft);
    border-radius: 12px;
    background: var(--surface-1);
    padding: 14px 16px;
  }

  header {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 8px;
  }

  .role {
    font-family: var(--font-heading);
    letter-spacing: 0.05em;
  }

  .role.user {
    color: var(--accent-blue);
  }

  .role.assistant {
    color: var(--accent-orange);
  }

  .role.plan {
    color: var(--accent-cyan);
  }

  time {
    color: var(--text-dim);
    font-size: 12px;
  }

  p {
    margin: 0;
    line-height: 1.45;
    white-space: pre-wrap;
  }

  .branch {
    margin-left: auto;
    border: 1px solid var(--border-soft);
    background: transparent;
    color: var(--text-muted);
    border-radius: 8px;
    padding: 4px 8px;
    font-size: 12px;
  }

  .branch:hover {
    color: var(--text-primary);
    border-color: var(--accent-orange);
  }
</style>
