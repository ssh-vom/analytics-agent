<script lang="ts">
  import { onMount } from "svelte";

  import { groupEventsIntoCells } from "$lib/cells";
  import MessageCell from "$lib/components/MessageCell.svelte";
  import PythonCell from "$lib/components/PythonCell.svelte";
  import SqlCell from "$lib/components/SqlCell.svelte";
  import ArtifactsPanel from "$lib/components/ArtifactsPanel.svelte";
  import WorldlinePicker from "$lib/components/WorldlinePicker.svelte";
  import { activeThread, threads } from "$lib/stores/threads";
  import {
    branchWorldline,
    createThread,
    createWorldline,
    fetchThreadWorldlines,
    fetchWorldlineEvents,
    streamChatTurn,
  } from "$lib/api/client";
  import type { Thread, TimelineEvent, WorldlineItem } from "$lib/types";
  
  // Icons
  import { Database } from "lucide-svelte";
  import { Send } from "lucide-svelte";
  import { ChevronDown } from "lucide-svelte";
  import { Sparkles } from "lucide-svelte";

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
  let showProviderMenu = false;
  let composerExpanded = false;
  let isHydratingThread = false;

  $: activeEvents = eventsByWorldline[activeWorldlineId] ?? [];
  $: cells = groupEventsIntoCells(activeEvents);
  $: currentThread = $activeThread;

  onMount(async () => {
    await threads.loadThreads();

    // Load from localStorage if available
    activeThread.loadFromStorage();
    
    // Check if we have a stored worldline from creating a new thread
    const storedWorldlineId = localStorage.getItem("textql_active_worldline");
    
    if ($activeThread) {
      await hydrateThread($activeThread.id, storedWorldlineId ?? undefined);
      if (storedWorldlineId) {
        localStorage.removeItem("textql_active_worldline");
      }
    } else {
      await initializeSession();
    }
  });

  $: if ($activeThread?.id && isReady && $activeThread.id !== threadId && !isHydratingThread) {
    void hydrateThread($activeThread.id);
  }

  async function hydrateThread(targetThreadId: string, preferredWorldlineId?: string): Promise<void> {
    isHydratingThread = true;
    statusText = "Loading thread...";

    try {
      threadId = targetThreadId;
      worldlines = [];
      eventsByWorldline = {};
      activeWorldlineId = "";

      await refreshWorldlines();

      if (preferredWorldlineId && worldlines.some((w) => w.id === preferredWorldlineId)) {
        activeWorldlineId = preferredWorldlineId;
      } else if (worldlines.length > 0) {
        activeWorldlineId = worldlines[0].id;
      }

      if (activeWorldlineId) {
        await loadWorldline(activeWorldlineId);
        statusText = "Ready";
      } else {
        statusText = "Error: No worldline found";
      }

      isReady = true;
    } catch (error) {
      statusText = error instanceof Error ? error.message : "Failed to load thread";
      isReady = false;
    } finally {
      isHydratingThread = false;
    }
  }

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
      statusText = "Creating thread...";
      
      // Create thread via API
      const thread = await createThread("TextQL Session");
      threadId = thread.thread_id;
      
      // Create local thread object
      const newThread = {
        id: thread.thread_id,
        name: "TextQL Session",
        createdAt: new Date().toISOString(),
        lastActivity: new Date().toISOString(),
        messageCount: 0,
      };
      
      // Update store
      let currentThreads: Thread[] = [];
      const unsubscribe = threads.subscribe(s => { currentThreads = s.threads; });
      unsubscribe();
      threads.saveThreads([newThread, ...currentThreads]);
      activeThread.set(newThread);
      activeThread.saveToStorage(newThread);
      
      statusText = "Creating worldline...";
      
      // Create worldline for this thread
      const worldline = await createWorldline(threadId, "main");
      activeWorldlineId = worldline.worldline_id;
      
      statusText = "Loading worldline...";
      await refreshWorldlines();
      await loadWorldline(activeWorldlineId);
      
      statusText = "Ready";
      isReady = true;
      console.log("Session initialized successfully:", { threadId, activeWorldlineId });
    } catch (error) {
      statusText = error instanceof Error ? error.message : "Initialization failed";
      console.error("Initialization error:", error);
      isReady = false;
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
      if (!activeWorldlineId) {
        statusText = "Error: No active worldline. Please refresh the page.";
      }
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
          
          // Update thread message count
          if ($activeThread) {
            threads.updateThread($activeThread.id, {
              messageCount: ($activeThread.messageCount || 0) + 1,
              lastActivity: new Date().toISOString(),
            });
          }
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

  function getProviderIcon(provider: Provider) {
    switch (provider) {
      case "gemini":
        return "Gemini";
      case "openai":
        return "OpenAI";
      case "openrouter":
        return "OpenRouter";
    }
  }
</script>

<div class="chat-container">
  <!-- Top Bar -->
  <header class="top-bar">
    <div class="top-bar-left">
      <WorldlinePicker
        {worldlines}
        {activeWorldlineId}
        on:select={handleWorldlineSelect}
      />
      
      <div class="provider-selector">
        <button 
          class="provider-btn"
          on:click={() => showProviderMenu = !showProviderMenu}
        >
          <Sparkles size={14} />
          <span>{getProviderIcon(provider)}</span>
          <ChevronDown size={14} />
        </button>
        
        {#if showProviderMenu}
          <div class="provider-menu">
            <button 
              class="provider-option"
              class:active={provider === "gemini"}
              on:click={() => { provider = "gemini"; showProviderMenu = false; }}
            >
              Gemini
            </button>
            <button 
              class="provider-option"
              class:active={provider === "openai"}
              on:click={() => { provider = "openai"; showProviderMenu = false; }}
            >
              OpenAI
            </button>
            <button 
              class="provider-option"
              class:active={provider === "openrouter"}
              on:click={() => { provider = "openrouter"; showProviderMenu = false; }}
            >
              OpenRouter
            </button>
          </div>
        {/if}
      </div>

      <input
        class="model-input"
        bind:value={model}
        placeholder="Model (optional)"
      />
    </div>
    
    <div class="top-bar-right">
      <span class="status" class:ready={statusText === "Ready"}>
        {statusText}
      </span>
      
      <button class="db-selector">
        <Database size={16} />
        <span>local.db</span>
        <span class="db-badge">DUCKDB</span>
      </button>
    </div>
  </header>

  <div class="workspace">
    <!-- Chat Feed -->
    <div class="feed">
      {#if !isReady}
        <div class="empty-state">
          <div class="empty-icon">
            <Sparkles size={32} />
          </div>
          <p>Initializing session...</p>
        </div>
      {:else if cells.length === 0}
        <div class="empty-state">
          <div class="empty-icon">
            <Database size={32} />
          </div>
          <h3>Start analyzing your data</h3>
          <p>Ask a question, write SQL, or request Python analysis</p>
        </div>
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
              showArtifacts={false}
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

    <ArtifactsPanel events={activeEvents} />
  </div>

  <!-- Composer -->
  <div class="composer-container">
    <form class="composer" on:submit|preventDefault={sendPrompt}>
      <div class="composer-input-wrapper">
        <textarea
          bind:value={prompt}
          placeholder="Ask a question, write SQL, or request Python analysis..."
          rows={composerExpanded ? 4 : 2}
          on:focus={() => composerExpanded = true}
          on:blur={() => composerExpanded = false}
        ></textarea>
      </div>
      <button 
        type="submit" 
        class="send-btn"
        disabled={isSending || !isReady || !prompt.trim()}
      >
        {#if isSending}
          <span class="loading"></span>
        {:else}
          <Send size={18} />
        {/if}
      </button>
    </form>
  </div>
</div>

<style>
  .chat-container {
    display: flex;
    flex-direction: column;
    height: 100vh;
    background: var(--bg-0);
  }

  .workspace {
    flex: 1;
    min-height: 0;
    display: grid;
    grid-template-columns: minmax(0, 1fr) 340px;
  }

  .top-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-4);
    padding: var(--space-3) var(--space-4);
    background: var(--surface-0);
    border-bottom: 1px solid var(--border-soft);
    flex-shrink: 0;
  }

  .top-bar-left {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    flex: 1;
  }

  .top-bar-right {
    display: flex;
    align-items: center;
    gap: var(--space-3);
  }

  .provider-selector {
    position: relative;
  }

  .provider-btn {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-2) var(--space-3);
    background: var(--surface-1);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    color: var(--text-secondary);
    font-size: 13px;
    cursor: pointer;
    transition: all var(--transition-fast);
  }

  .provider-btn:hover {
    background: var(--surface-2);
    border-color: var(--border-medium);
  }

  .provider-menu {
    position: absolute;
    top: calc(100% + var(--space-1));
    left: 0;
    min-width: 140px;
    background: var(--surface-1);
    border: 1px solid var(--border-medium);
    border-radius: var(--radius-md);
    box-shadow: var(--shadow-lg);
    z-index: 100;
    overflow: hidden;
  }

  .provider-option {
    display: block;
    width: 100%;
    padding: var(--space-2) var(--space-3);
    background: transparent;
    border: none;
    color: var(--text-secondary);
    font-size: 13px;
    text-align: left;
    cursor: pointer;
    transition: all var(--transition-fast);
  }

  .provider-option:hover {
    background: var(--surface-hover);
    color: var(--text-primary);
  }

  .provider-option.active {
    background: var(--accent-orange-muted);
    color: var(--accent-orange);
  }

  .model-input {
    padding: var(--space-2) var(--space-3);
    background: var(--surface-1);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    color: var(--text-primary);
    font-size: 13px;
    min-width: 160px;
    transition: all var(--transition-fast);
  }

  .model-input:focus {
    outline: none;
    border-color: var(--accent-orange);
  }

  .model-input::placeholder {
    color: var(--text-dim);
  }

  .status {
    font-size: 13px;
    color: var(--text-dim);
    padding: var(--space-1) var(--space-3);
    background: var(--surface-1);
    border-radius: var(--radius-full);
    border: 1px solid var(--border-soft);
  }

  .status.ready {
    color: var(--success);
    border-color: var(--accent-cyan-muted);
    background: var(--accent-cyan-muted);
  }

  .db-selector {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-2) var(--space-3);
    background: var(--surface-1);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    color: var(--text-secondary);
    font-size: 13px;
    cursor: pointer;
    transition: all var(--transition-fast);
  }

  .db-selector:hover {
    background: var(--surface-2);
    border-color: var(--border-medium);
  }

  .db-badge {
    padding: 2px 6px;
    background: var(--accent-orange-muted);
    color: var(--accent-orange);
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    border-radius: var(--radius-sm);
  }

  .feed {
    overflow-y: auto;
    padding: var(--space-4);
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
    min-height: 0;
  }

  .empty-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: var(--space-3);
    padding: var(--space-8);
    text-align: center;
    color: var(--text-muted);
  }

  .empty-icon {
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

  .meta-cell {
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-lg);
    padding: var(--space-3);
    background: var(--surface-1);
  }

  .meta-cell header {
    display: flex;
    align-items: center;
    margin-bottom: var(--space-2);
  }

  .meta-cell strong {
    color: var(--text-muted);
    font-family: var(--font-heading);
    font-size: 13px;
  }

  .meta-cell button {
    margin-left: auto;
    border: 1px solid var(--border-soft);
    background: transparent;
    color: var(--text-muted);
    border-radius: var(--radius-md);
    padding: var(--space-1) var(--space-2);
    font-size: 12px;
    cursor: pointer;
    transition: all var(--transition-fast);
  }

  .meta-cell button:hover {
    color: var(--text-primary);
    border-color: var(--accent-orange);
  }

  .meta-cell pre {
    margin: 0;
    font-family: var(--font-mono);
    font-size: 12px;
    background: var(--surface-0);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    padding: var(--space-3);
    overflow-x: auto;
    color: var(--text-muted);
  }

  .composer-container {
    padding: var(--space-4);
    background: var(--surface-0);
    border-top: 1px solid var(--border-soft);
    flex-shrink: 0;
  }

  .composer {
    display: flex;
    gap: var(--space-3);
    align-items: flex-end;
    max-width: 900px;
    margin: 0 auto;
  }

  .composer-input-wrapper {
    flex: 1;
    position: relative;
  }

  textarea {
    width: 100%;
    min-height: 60px;
    padding: var(--space-3) var(--space-4);
    background: var(--surface-1);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-lg);
    color: var(--text-primary);
    font-family: var(--font-body);
    font-size: 15px;
    line-height: 1.5;
    resize: none;
    transition: all var(--transition-fast);
  }

  textarea:focus {
    outline: none;
    border-color: var(--accent-orange);
    box-shadow: var(--glow-orange);
  }

  textarea::placeholder {
    color: var(--text-dim);
  }

  .send-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 44px;
    height: 44px;
    background: linear-gradient(135deg, var(--accent-orange), #c86f24);
    border: none;
    border-radius: var(--radius-lg);
    color: #111;
    cursor: pointer;
    transition: all var(--transition-fast);
    flex-shrink: 0;
  }

  .send-btn:hover:not(:disabled) {
    transform: scale(1.05);
    box-shadow: var(--shadow-md);
  }

  .send-btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .loading {
    width: 18px;
    height: 18px;
    border: 2px solid rgba(0, 0, 0, 0.3);
    border-top-color: #111;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }

  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }

  @media (max-width: 1100px) {
    .workspace {
      grid-template-columns: 1fr;
      grid-template-rows: minmax(0, 1fr) auto;
    }
  }
</style>
