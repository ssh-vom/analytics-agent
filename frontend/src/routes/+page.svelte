<script lang="ts">
  import { goto } from "$app/navigation";
  import { onMount } from "svelte";

  onMount(() => {
    goto("/chat");
  });
</script>

<div class="redirect">
  <p>Redirecting to chat...</p>
</div>

<style>
  .redirect {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100vh;
    background: var(--bg-0);
    color: var(--text-muted);
  }
</style>
