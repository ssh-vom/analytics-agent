<script lang="ts">
  import { onMount } from "svelte";

  import { groupEventsIntoCells } from "$lib/cells";
  import MessageCell from "$lib/components/MessageCell.svelte";
  import PythonCell from "$lib/components/PythonCell.svelte";
  import SqlCell from "$lib/components/SqlCell.svelte";
  import WorldlinePicker from "$lib/components/WorldlinePicker.svelte";
  import {
    branchWorldline,
    createThread,
    createWorldline,
    fetchThreadWorldlines,
    fetchWorldlineEvents,
    streamChatTurn,
  } from "$lib/api/client";
  import type { TimelineEvent, WorldlineItem } from "$lib/types";

  type Provider = "gemini" | "openai" | "openrouter";

  let threadId = "";
  let activeWorldlineId = "";
  let worldlines: WorldlineItem[] = [];
  let eventsByWorldline: Record<string, TimelineEvent[]> = {};
  let prompt = "";
  let provider: Provider = "gemini";
  let model = "";
  let statusText = "Initializing...";
  let isSending = false;
  let isReady = false;

  $: activeEvents = eventsByWorldline[activeWorldlineId] ?? [];
  $: cells = groupEventsIntoCells(activeEvents);

  onMount(async () => {
    await initializeSession();
  });

  function dedupePreserveOrder(events: TimelineEvent[]): TimelineEvent[] {
    const seenIds = new Set<string>();
    const output: TimelineEvent[] = [];
    for (const event of events) {
      if (seenIds.has(event.id)) {
        continue;
      }
      seenIds.add(event.id);
      output.push(event);
    }
    return output;
  }

  function setWorldlineEvents(worldlineId: string, events: TimelineEvent[]): void {
    eventsByWorldline = {
      ...eventsByWorldline,
      [worldlineId]: dedupePreserveOrder(events),
    };
  }

  function appendEvent(worldlineId: string, event: TimelineEvent): void {
    const existing = eventsByWorldline[worldlineId] ?? [];
    setWorldlineEvents(worldlineId, [...existing, event]);
  }

  function ensureWorldlineVisible(worldlineId: string): void {
    if (worldlines.some((line) => line.id === worldlineId)) {
      return;
    }
    worldlines = [
      ...worldlines,
      {
        id: worldlineId,
        name: worldlineId.slice(0, 12),
        parent_worldline_id: null,
        forked_from_event_id: null,
        head_event_id: null,
        created_at: new Date().toISOString(),
      },
    ];
  }

  async function initializeSession(): Promise<void> {
    try {
      const thread = await createThread("TextQL Session");
      threadId = thread.thread_id;
      const worldline = await createWorldline(threadId, "main");
      activeWorldlineId = worldline.worldline_id;
      await refreshWorldlines();
      await loadWorldline(activeWorldlineId);
      statusText = "Ready";
      isReady = true;
    } catch (error) {
      statusText = error instanceof Error ? error.message : "Initialization failed";
    }
  }

  async function refreshWorldlines(): Promise<void> {
    if (!threadId) {
      return;
    }
    const response = await fetchThreadWorldlines(threadId);
    worldlines = response.worldlines;
  }

  async function loadWorldline(worldlineId: string): Promise<void> {
    const events = await fetchWorldlineEvents(worldlineId);
    setWorldlineEvents(worldlineId, events);
  }

  async function selectWorldline(worldlineId: string): Promise<void> {
    activeWorldlineId = worldlineId;
    if (!eventsByWorldline[worldlineId]) {
      await loadWorldline(worldlineId);
    }
  }

  async function handleWorldlineSelect(
    event: CustomEvent<{ id: string }>,
  ): Promise<void> {
    await selectWorldline(event.detail.id);
  }

  async function branchFromEvent(eventId: string): Promise<void> {
    if (!activeWorldlineId || !eventId) {
      return;
    }
    try {
      statusText = "Branching worldline...";
      const response = await branchWorldline(
        activeWorldlineId,
        eventId,
        `branch-${worldlines.length + 1}`,
      );
      activeWorldlineId = response.new_worldline_id;
      await refreshWorldlines();
      await loadWorldline(activeWorldlineId);
      statusText = "Branch created";
    } catch (error) {
      statusText = error instanceof Error ? error.message : "Branch failed";
    }
  }

  async function sendPrompt(): Promise<void> {
    const message = prompt.trim();
    if (!message || !activeWorldlineId || isSending) {
      return;
    }

    isSending = true;
    prompt = "";
    statusText = "Agent is thinking...";

    try {
      await streamChatTurn({
        worldlineId: activeWorldlineId,
        message,
        provider,
        model: model.trim() || undefined,
        maxIterations: provider === "gemini" ? 3 : 6,
        onEvent: (frame) => {
          ensureWorldlineVisible(frame.worldline_id);
          appendEvent(frame.worldline_id, frame.event);
          activeWorldlineId = frame.worldline_id;

          if (frame.event.type === "tool_call_sql") {
            statusText = "Running SQL...";
          } else if (frame.event.type === "tool_call_python") {
            statusText = "Running Python...";
          } else if (frame.event.type === "assistant_message") {
            statusText = "Done";
          } else {
            statusText = "Working...";
          }
        },
        onDone: async (done) => {
          activeWorldlineId = done.worldline_id;
          await refreshWorldlines();
          statusText = "Done";
        },
        onError: (error) => {
          statusText = `Error: ${error}`;
        },
      });
    } catch (error) {
      statusText = error instanceof Error ? error.message : "Request failed";
    } finally {
      isSending = false;
    }
  }
</script>

<main class="app-shell">
  <aside class="sidebar">
    <div class="brand">TextQL</div>
    <section class="panel">
      <h3>Thread</h3>
      <p>{threadId || "creating..."}</p>
    </section>
    <section class="panel">
      <h3>Worldlines</h3>
      <ul>
        {#each worldlines as line}
          <li class:active={line.id === activeWorldlineId}>
            <button type="button" on:click={() => selectWorldline(line.id)}>
              {line.name}
            </button>
          </li>
        {/each}
      </ul>
    </section>
  </aside>

  <section class="chat-area">
    <header class="top-bar">
      <WorldlinePicker
        {worldlines}
        {activeWorldlineId}
        on:select={handleWorldlineSelect}
      />
      <label class="provider-select">
        Provider
        <select bind:value={provider}>
          <option value="gemini">Gemini</option>
          <option value="openai">OpenAI</option>
          <option value="openrouter">OpenRouter</option>
        </select>
      </label>
      <input
        class="model-input"
        bind:value={model}
        placeholder="Model override (optional)"
      />
      <span class="status">{statusText}</span>
    </header>

    <div class="feed">
      {#if !isReady}
        <p class="empty">Initializing session...</p>
      {:else if cells.length === 0}
        <p class="empty">Start by asking about your data.</p>
      {:else}
        {#each cells as cell (cell.id)}
          {#if cell.kind === "message"}
            <MessageCell
              role={cell.role}
              text={cell.text}
              createdAt={cell.event.created_at}
              onBranch={() => branchFromEvent(cell.event.id)}
            />
          {:else if cell.kind === "sql"}
            <SqlCell
              callEvent={cell.call}
              resultEvent={cell.result}
              onBranch={() => branchFromEvent(cell.result?.id ?? cell.call?.id ?? "")}
            />
          {:else if cell.kind === "python"}
            <PythonCell
              callEvent={cell.call}
              resultEvent={cell.result}
              onBranch={() => branchFromEvent(cell.result?.id ?? cell.call?.id ?? "")}
            />
          {:else}
            <article class="meta-cell">
              <header>
                <strong>{cell.label}</strong>
                <button type="button" on:click={() => branchFromEvent(cell.event.id)}>
                  Branch from here
                </button>
              </header>
              <pre>{JSON.stringify(cell.event.payload, null, 2)}</pre>
            </article>
          {/if}
        {/each}
      {/if}
    </div>

    <form class="composer" on:submit|preventDefault={sendPrompt}>
      <textarea
        bind:value={prompt}
        placeholder="Ask a question, write SQL, or request Python analysis..."
        rows="2"
      ></textarea>
      <button type="submit" disabled={isSending || !isReady}>
        {isSending ? "Running..." : "Send"}
      </button>
    </form>
  </section>
</main>

<style>
  .app-shell {
    display: grid;
    grid-template-columns: 280px 1fr;
    min-height: 100vh;
  }

  .sidebar {
    border-right: 1px solid var(--border-soft);
    background: linear-gradient(180deg, rgb(255 159 67 / 5%) 0%, transparent 30%),
      var(--bg-1);
    padding: 20px 16px;
    display: grid;
    gap: 14px;
    align-content: start;
  }

  .brand {
    font-family: var(--font-heading);
    font-size: 24px;
    color: var(--text-primary);
  }

  .panel {
    border: 1px solid var(--border-soft);
    border-radius: 12px;
    padding: 10px;
    background: var(--surface-0);
  }

  .panel h3 {
    margin: 0 0 8px;
    color: var(--text-muted);
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-family: var(--font-heading);
  }

  .panel p {
    margin: 0;
    font-size: 12px;
    color: var(--text-dim);
    word-break: break-all;
  }

  ul {
    list-style: none;
    padding: 0;
    margin: 0;
    display: grid;
    gap: 8px;
  }

  li button {
    width: 100%;
    text-align: left;
    border: 1px solid var(--border-soft);
    border-radius: 10px;
    background: var(--surface-1);
    color: var(--text-muted);
    padding: 8px 10px;
  }

  li.active button {
    border-color: rgb(255 159 67 / 60%);
    color: var(--accent-orange);
    background: rgb(255 159 67 / 8%);
  }

  .chat-area {
    display: grid;
    grid-template-rows: auto 1fr auto;
    min-height: 100vh;
  }

  .top-bar {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    align-items: center;
    border-bottom: 1px solid var(--border-soft);
    padding: 12px 16px;
    background: var(--surface-0);
  }

  .provider-select {
    display: inline-flex;
    gap: 8px;
    align-items: center;
    color: var(--text-muted);
    font-size: 13px;
    border: 1px solid var(--border-soft);
    border-radius: 8px;
    padding: 6px 10px;
  }

  .provider-select select {
    border: none;
    background: transparent;
    color: var(--text-primary);
  }

  .model-input {
    border: 1px solid var(--border-soft);
    border-radius: 8px;
    background: var(--surface-1);
    color: var(--text-primary);
    padding: 6px 10px;
    min-width: 220px;
  }

  .status {
    margin-left: auto;
    color: var(--text-dim);
    font-size: 13px;
  }

  .feed {
    overflow-y: auto;
    padding: 16px;
    display: grid;
    gap: 12px;
    align-content: start;
  }

  .empty {
    color: var(--text-dim);
  }

  .composer {
    border-top: 1px solid var(--border-soft);
    padding: 12px 16px;
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 10px;
    background: var(--surface-0);
  }

  textarea {
    resize: vertical;
    min-height: 60px;
    border-radius: 10px;
    border: 1px solid var(--border-soft);
    background: var(--surface-1);
    color: var(--text-primary);
    padding: 10px 12px;
  }

  .composer button {
    border: none;
    border-radius: 10px;
    background: linear-gradient(135deg, var(--accent-orange), #c86f24);
    color: #111;
    font-weight: 600;
    padding: 0 18px;
  }

  .composer button:disabled {
    opacity: 0.6;
  }

  .meta-cell {
    border: 1px solid var(--border-soft);
    border-radius: 12px;
    padding: 12px;
    background: var(--surface-1);
  }

  .meta-cell header {
    display: flex;
    align-items: center;
    margin-bottom: 8px;
  }

  .meta-cell strong {
    color: var(--text-muted);
    font-family: var(--font-heading);
  }

  .meta-cell button {
    margin-left: auto;
    border: 1px solid var(--border-soft);
    background: transparent;
    color: var(--text-muted);
    border-radius: 8px;
    padding: 4px 8px;
    font-size: 12px;
  }

  .meta-cell pre {
    margin: 0;
    font-family: var(--font-mono);
    font-size: 12px;
    background: var(--surface-0);
    border: 1px solid var(--border-soft);
    border-radius: 8px;
    padding: 10px;
    overflow-x: auto;
  }

  @media (max-width: 980px) {
    .app-shell {
      grid-template-columns: 1fr;
    }

    .sidebar {
      border-right: none;
      border-bottom: 1px solid var(--border-soft);
    }
  }
</style>
