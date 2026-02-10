<script lang="ts">
  import { User } from "lucide-svelte";
  import { Bot } from "lucide-svelte";
  import { GitBranch } from "lucide-svelte";
  import { Sparkles } from "lucide-svelte";
  import { formatDistanceToNow } from "date-fns";

  export let role: "user" | "assistant" | "plan" = "assistant";
  export let text = "";
  export let createdAt = "";
  export let onBranch: (() => void) | null = null;

  $: roleLabel = role === "plan" ? "PLAN" : role.toUpperCase();
  
  function formatTime(dateString: string): string {
    try {
      return formatDistanceToNow(new Date(dateString), { addSuffix: true });
    } catch {
      return new Date(dateString).toLocaleTimeString();
    }
  }
</script>

<article class="message" class:user={role === "user"} class:assistant={role === "assistant"} class:plan={role === "plan"}>
  <div class="message-gutter">
    <div class="avatar" class:user={role === "user"} class:assistant={role === "assistant"} class:plan={role === "plan"}>
      {#if role === "user"}
        <User size={14} />
      {:else if role === "plan"}
        <Sparkles size={14} />
      {:else}
        <Bot size={14} />
      {/if}
    </div>
  </div>
  
  <div class="message-content">
    <header>
      <div class="role-info">
        <span class="role-name">{roleLabel}</span>
        <time>{formatTime(createdAt)}</time>
      </div>
      {#if onBranch}
        <button type="button" class="branch-btn" on:click={onBranch}>
          <GitBranch size={12} />
          <span>Branch</span>
        </button>
      {/if}
    </header>
    <div class="message-body">
      <p>{text}</p>
    </div>
  </div>
</article>

<style>
  .message {
    display: flex;
    gap: var(--space-3);
    padding: var(--space-4);
    background: var(--surface-0);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-lg);
    transition: border-color var(--transition-fast);
  }

  .message:hover {
    border-color: var(--border-medium);
  }

  .message.user {
    background: var(--surface-1);
  }

  .message.plan {
    border-left: 2px solid var(--accent-cyan);
  }

  .message-gutter {
    flex-shrink: 0;
  }

  .avatar {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    border-radius: var(--radius-md);
    background: var(--surface-2);
    color: var(--text-dim);
  }

  .avatar.user {
    background: var(--accent-blue-muted);
    color: var(--accent-blue);
  }

  .avatar.assistant {
    background: var(--accent-green-muted);
    color: var(--accent-green);
  }

  .avatar.plan {
    background: var(--accent-cyan-muted);
    color: var(--accent-cyan);
  }

  .message-content {
    flex: 1;
    min-width: 0;
  }

  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-3);
    margin-bottom: var(--space-2);
  }

  .role-info {
    display: flex;
    align-items: baseline;
    gap: var(--space-2);
  }

  .role-name {
    font-family: var(--font-heading);
    font-size: 12px;
    font-weight: 400;
    letter-spacing: 0.04em;
    color: var(--text-muted);
    text-transform: uppercase;
  }

  .message.user .role-name {
    color: var(--accent-blue);
  }

  .message.assistant .role-name {
    color: var(--accent-green);
  }

  .message.plan .role-name {
    color: var(--accent-cyan);
  }

  time {
    color: var(--text-dim);
    font-size: 11px;
    font-family: var(--font-mono);
  }

  .message-body {
    color: var(--text-primary);
  }

  .message-body p {
    margin: 0;
    line-height: 1.65;
    white-space: pre-wrap;
    font-size: 14px;
  }

  .branch-btn {
    display: flex;
    align-items: center;
    gap: var(--space-1);
    padding: 2px var(--space-2);
    background: transparent;
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-sm);
    color: var(--text-dim);
    font-size: 11px;
    cursor: pointer;
    transition: all var(--transition-fast);
    opacity: 0;
  }

  .message:hover .branch-btn {
    opacity: 1;
  }

  .branch-btn:hover {
    border-color: var(--border-medium);
    color: var(--text-secondary);
  }

  @media (max-width: 640px) {
    .message {
      gap: var(--space-2);
      padding: var(--space-3);
    }

    .branch-btn {
      opacity: 1;
    }
  }
</style>
