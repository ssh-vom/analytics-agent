<script lang="ts">
  import { onDestroy, onMount, tick } from "svelte";

  import { groupDisplayItemsIntoCells } from "$lib/cells";
  import {
    buildDisplayItems,
    createStreamingState,
    applyDelta,
    clearFromEvent,
    type StreamingState,
  } from "$lib/streaming";
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
    importCSV,
    fetchWorldlineTables,
  } from "$lib/api/client";
  import type { Thread, TimelineEvent, WorldlineItem } from "$lib/types";
  
  // Icons
  import { Database } from "lucide-svelte";
  import { Send } from "lucide-svelte";
  import { ChevronDown } from "lucide-svelte";
  import { Sparkles } from "lucide-svelte";
  import { Upload } from "lucide-svelte";
  import { FileSpreadsheet } from "lucide-svelte";
  import { X } from "lucide-svelte";

  type Provider = "gemini" | "openai" | "openrouter";

  let threadId = "";
  let activeWorldlineId = "";
  let worldlines: WorldlineItem[] = [];
  let eventsByWorldline: Record<string, TimelineEvent[]> = {};
  let prompt = "";
  let provider: Provider = "openrouter";
  let model = "";
  let statusText = "Initializing...";
  let isSending = false;
  let isReady = false;
  let showProviderMenu = false;
  let composerExpanded = false;
  let isHydratingThread = false;
  let artifactsPanelCollapsed = false;
  let selectedArtifactId: string | null = null;
  let streamingState: StreamingState = createStreamingState();
  let feedElement: HTMLDivElement | null = null;
  let shouldAutoScroll = true;
  let pendingScrollRaf = 0;
  let hasPendingScroll = false;
  let pendingScrollForce = false;
  let scrollAttemptsQueue: (() => void)[] = [];

  // CSV Import state
  let uploadedFiles: File[] = [];
  let importingFile: string | null = null;
  let importError: string | null = null;
  let importSuccess: { filename: string; table: string; rows: number } | null = null;
  let showImportPanel = false;
  let worldlineTables: Awaited<ReturnType<typeof fetchWorldlineTables>> | null = null;

  $: activeEvents = eventsByWorldline[activeWorldlineId] ?? [];
  $: displayItems = buildDisplayItems(activeEvents, streamingState);
  $: cells = groupDisplayItemsIntoCells(displayItems);
  $: currentThread = $activeThread;
  $: hasDraftOutput =
    streamingState.text.length > 0 || streamingState.toolCalls.size > 0;

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

    await tick();
    scrollFeedToBottom(true);
  });

  onDestroy(() => {
    if (pendingScrollRaf) {
      cancelAnimationFrame(pendingScrollRaf);
      pendingScrollRaf = 0;
    }
    hasPendingScroll = false;
    pendingScrollForce = false;
    scrollAttemptsQueue = [];
  });

  $: if ($activeThread?.id && isReady && $activeThread.id !== threadId && !isHydratingThread) {
    void hydrateThread($activeThread.id);
  }

  function handleFeedScroll(): void {
    if (!feedElement) {
      return;
    }
    const bottomDistance =
      feedElement.scrollHeight - feedElement.scrollTop - feedElement.clientHeight;
    shouldAutoScroll = bottomDistance < 120;
  }

  function scrollFeedToBottom(force = false): void {
    if (force) {
      pendingScrollForce = true;
    }
    if (!force && !shouldAutoScroll) {
      return;
    }

    // Queue scroll attempt and batch process
    scrollAttemptsQueue.push(() => {
      const shouldScrollNow = pendingScrollForce || shouldAutoScroll;
      pendingScrollForce = false;
      if (!feedElement || !shouldScrollNow) {
        return;
      }
      feedElement.scrollTo({
        top: feedElement.scrollHeight,
        behavior: "auto",
      });
    });

    // Prevent multiple RAFs from being scheduled
    if (hasPendingScroll) {
      return;
    }
    hasPendingScroll = true;

    // Batch all queued scrolls in next frame
    void tick().then(() => {
      pendingScrollRaf = requestAnimationFrame(() => {
        pendingScrollRaf = 0;
        hasPendingScroll = false;

        // Execute only the last scroll attempt
        const lastAttempt = scrollAttemptsQueue.pop();
        scrollAttemptsQueue = []; // Clear queue

        if (lastAttempt) {
          lastAttempt();
        }
      });
    });
  }

  function resetStreamingDrafts(): void {
    streamingState = createStreamingState();
  }

  async function hydrateThread(targetThreadId: string, preferredWorldlineId?: string): Promise<void> {
    isHydratingThread = true;
    statusText = "Loading thread...";

    try {
      threadId = targetThreadId;
      worldlines = [];
      eventsByWorldline = {};
      activeWorldlineId = "";
      selectedArtifactId = null;
      resetStreamingDrafts();

      await refreshWorldlines();

      if (preferredWorldlineId && worldlines.some((w) => w.id === preferredWorldlineId)) {
        activeWorldlineId = preferredWorldlineId;
      } else if (worldlines.length > 0) {
        activeWorldlineId = worldlines[0].id;
      }

      if (activeWorldlineId) {
        await loadWorldline(activeWorldlineId);
        statusText = "Ready";
        scrollFeedToBottom(true);
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
      resetStreamingDrafts();
      selectedArtifactId = null;
      
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
      scrollFeedToBottom(true);
      
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
    scrollFeedToBottom(true);
  }

  async function selectWorldline(worldlineId: string): Promise<void> {
    activeWorldlineId = worldlineId;
    resetStreamingDrafts();
    selectedArtifactId = null;
    if (!eventsByWorldline[worldlineId]) {
      await loadWorldline(worldlineId);
    }
    scrollFeedToBottom(true);
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

  function handleArtifactSelect(event: CustomEvent<{ artifactId: string }>): void {
    artifactsPanelCollapsed = false;
    selectedArtifactId = event.detail.artifactId;
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
    shouldAutoScroll = true;
    resetStreamingDrafts();
    selectedArtifactId = null;

    // Optimistic user message — show immediately in the feed
    const optimisticId = `optimistic-user-${Date.now()}`;
    const optimisticEvent: TimelineEvent = {
      id: optimisticId,
      parent_event_id: null,
      type: "user_message",
      payload: { text: message },
      created_at: new Date().toISOString(),
    };
    appendEvent(activeWorldlineId, optimisticEvent);
    scrollFeedToBottom(true);

    try {
      await streamChatTurn({
        worldlineId: activeWorldlineId,
        message,
        provider,
        model: model.trim() || undefined,
        maxIterations: provider === "gemini" ? 10 : 20,
        onEvent: (frame) => {
          ensureWorldlineVisible(frame.worldline_id);
          streamingState = clearFromEvent(streamingState, frame.event);

          // Remove optimistic user message when real one arrives
          if (frame.event.type === "user_message") {
            const existing = eventsByWorldline[frame.worldline_id] ?? [];
            const filtered = existing.filter((e) => e.id !== optimisticId);
            setWorldlineEvents(frame.worldline_id, [...filtered, frame.event]);
          } else {
            appendEvent(frame.worldline_id, frame.event);
          }

          activeWorldlineId = frame.worldline_id;
          scrollFeedToBottom();

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
        onDelta: (frame) => {
          ensureWorldlineVisible(frame.worldline_id);
          activeWorldlineId = frame.worldline_id;
          streamingState = applyDelta(streamingState, frame.delta);
          if (frame.delta.skipped) {
            statusText = "Skipped repeated tool call...";
          } else if (frame.delta.type === "assistant_text" && !frame.delta.done) {
            statusText = "Composing response...";
          } else if (frame.delta.type === "tool_call_sql" && !frame.delta.done) {
            statusText = "Drafting SQL...";
          } else if (frame.delta.type === "tool_call_python" && !frame.delta.done) {
            statusText = "Drafting Python...";
          }
          scrollFeedToBottom();
        },
        onDone: async (done) => {
          activeWorldlineId = done.worldline_id;
          await refreshWorldlines();
          if (activeWorldlineId) {
            await loadWorldline(activeWorldlineId);
          }
          statusText = "Done";
          scrollFeedToBottom();
          
          // Update thread message count
          if ($activeThread) {
            threads.updateThread($activeThread.id, {
              messageCount: ($activeThread.messageCount || 0) + 1,
              lastActivity: new Date().toISOString(),
            });
          }
        },
        onError: (error) => {
          resetStreamingDrafts();
          statusText = `Error: ${error}`;
        },
      });
    } catch (error) {
      resetStreamingDrafts();
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

  // CSV Import functions
  function handleFileSelect(event: Event) {
    const input = event.target as HTMLInputElement;
    if (input.files) {
      const csvFiles = Array.from(input.files).filter(f => f.name.endsWith('.csv'));
      uploadedFiles = [...uploadedFiles, ...csvFiles];
      showImportPanel = uploadedFiles.length > 0;
    }
  }

  function removeUploadedFile(filename: string) {
    uploadedFiles = uploadedFiles.filter(f => f.name !== filename);
    if (uploadedFiles.length === 0) {
      showImportPanel = false;
    }
  }

  async function importCSVFile(file: File) {
    if (!activeWorldlineId) return;
    
    importingFile = file.name;
    importError = null;
    importSuccess = null;

    try {
      const result = await importCSV(activeWorldlineId, file);
      importSuccess = {
        filename: file.name,
        table: result.table_name,
        rows: result.row_count
      };
      // Remove from uploaded files after successful import
      removeUploadedFile(file.name);
      // Refresh tables list
      worldlineTables = await fetchWorldlineTables(activeWorldlineId);
      statusText = `Imported ${result.row_count} rows into ${result.table_name}`;
    } catch (error) {
      importError = error instanceof Error ? error.message : "Import failed";
      statusText = `Import failed: ${importError}`;
    } finally {
      importingFile = null;
    }
  }

  function toggleImportPanel() {
    showImportPanel = !showImportPanel;
    if (showImportPanel && activeWorldlineId) {
      fetchWorldlineTables(activeWorldlineId).then(tables => {
        worldlineTables = tables;
      });
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

  <div class="workspace" class:panel-collapsed={artifactsPanelCollapsed}>
    <!-- Chat Feed -->
    <div class="feed" bind:this={feedElement} on:scroll={handleFeedScroll}>
      {#if !isReady}
        <div class="empty-state">
          <div class="empty-icon">
            <Sparkles size={32} />
          </div>
          <p>Initializing session...</p>
        </div>
      {:else if cells.length === 0 && !hasDraftOutput}
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
              initialCollapsed={true}
              onBranch={() => branchFromEvent(cell.result?.id ?? cell.call?.id ?? "")}
            />
          {:else if cell.kind === "python"}
            <PythonCell
              callEvent={cell.call}
              resultEvent={cell.result}
              initialCollapsed={true}
              showArtifacts={true}
              artifactLinkMode="panel"
              on:artifactselect={handleArtifactSelect}
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

        {#if isSending && !hasDraftOutput}
          <div class="thinking-indicator">
            <div class="thinking-dots">
              <span class="dot"></span>
              <span class="dot"></span>
              <span class="dot"></span>
            </div>
            <span class="thinking-label">Thinking...</span>
          </div>
        {/if}
      {/if}
    </div>

    <ArtifactsPanel
      events={activeEvents}
      bind:collapsed={artifactsPanelCollapsed}
      {selectedArtifactId}
    />
  </div>

  <!-- Composer -->
  <div class="composer-container">
    <!-- CSV Import Panel -->
    {#if showImportPanel}
      <div class="import-panel">
        <div class="import-panel-header">
          <FileSpreadsheet size={16} />
          <span>CSV Files to Import</span>
          <button class="close-btn" on:click={() => showImportPanel = false}>
            <X size={14} />
          </button>
        </div>
        <div class="uploaded-files-list">
          {#each uploadedFiles as file}
            <div class="uploaded-file-item">
              <span class="filename">{file.name}</span>
              <span class="filesize">({(file.size / 1024).toFixed(1)} KB)</span>
              <div class="file-actions">
                {#if importingFile === file.name}
                  <span class="importing-indicator">Importing...</span>
                {:else}
                  <button 
                    class="import-btn"
                    on:click={() => importCSVFile(file)}
                    disabled={importingFile !== null}
                  >
                    Import
                  </button>
                  <button 
                    class="remove-btn"
                    on:click={() => removeUploadedFile(file.name)}
                    disabled={importingFile !== null}
                  >
                    <X size={12} />
                  </button>
                {/if}
              </div>
            </div>
          {/each}
        </div>
        {#if importSuccess}
          <div class="import-success">
            ✓ Imported {importSuccess.rows.toLocaleString()} rows into table "{importSuccess.table}"
          </div>
        {/if}
        {#if importError}
          <div class="import-error">
            ✗ {importError}
          </div>
        {/if}
        {#if worldlineTables && worldlineTables.tables.length > 0}
          <div class="existing-tables">
            <div class="existing-tables-header">Available Tables ({worldlineTables.count}):</div>
            <div class="tables-list">
              {#each worldlineTables.tables.slice(0, 5) as table}
                <span class="table-badge" class:imported={table.type === 'imported_csv'}>
                  {table.name}
                </span>
              {/each}
              {#if worldlineTables.tables.length > 5}
                <span class="table-badge more">+{worldlineTables.tables.length - 5} more</span>
              {/if}
            </div>
          </div>
        {/if}
      </div>
    {/if}

    <form class="composer" on:submit|preventDefault={sendPrompt}>
      <div class="composer-input-wrapper">
        <textarea
          bind:value={prompt}
          placeholder="Ask a question, write SQL, or request Python analysis..."
          rows={composerExpanded ? 4 : 2}
          on:focus={() => composerExpanded = true}
          on:blur={() => composerExpanded = false}
        ></textarea>
        <!-- File Upload Button -->
        <div class="upload-btn-wrapper">
          <input
            type="file"
            id="csv-upload"
            accept=".csv"
            multiple
            on:change={handleFileSelect}
            style="display: none;"
          />
          <button 
            type="button" 
            class="upload-btn"
            on:click={() => document.getElementById('csv-upload')?.click()}
            title="Upload CSV files"
          >
            <Upload size={16} />
            {#if uploadedFiles.length > 0}
              <span class="upload-badge">{uploadedFiles.length}</span>
            {/if}
          </button>
          {#if uploadedFiles.length > 0}
            <button 
              type="button" 
              class="upload-btn active"
              on:click={toggleImportPanel}
              title="Show import panel"
            >
              <FileSpreadsheet size={16} />
            </button>
          {/if}
        </div>
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
  }

  .workspace {
    flex: 1;
    min-height: 0;
    display: grid;
    grid-template-columns: minmax(0, 1fr) 340px;
  }

  .workspace.panel-collapsed {
    grid-template-columns: minmax(0, 1fr) 56px;
  }

  .top-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-4);
    padding: var(--space-2) var(--space-4);
    background: var(--bg-1);
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
    padding: 5px var(--space-3);
    background: var(--surface-1);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    color: var(--text-secondary);
    font-size: 13px;
    cursor: pointer;
    transition: all var(--transition-fast);
  }

  .provider-btn:hover {
    border-color: var(--border-medium);
    color: var(--text-primary);
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
    transition: color var(--transition-fast);
  }

  .provider-option:hover {
    background: var(--surface-hover);
    color: var(--text-primary);
  }

  .provider-option.active {
    color: var(--accent-green);
  }

  .model-input {
    padding: 5px var(--space-3);
    background: var(--surface-1);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    color: var(--text-primary);
    font-size: 13px;
    font-family: var(--font-mono);
    min-width: 160px;
    transition: border-color var(--transition-fast);
  }

  .model-input:focus {
    outline: none;
    border-color: var(--border-strong);
  }

  .model-input::placeholder {
    color: var(--text-dim);
    font-family: var(--font-body);
  }

  .status {
    font-size: 12px;
    font-family: var(--font-mono);
    color: var(--text-dim);
    padding: 3px var(--space-3);
    background: var(--surface-0);
    border-radius: var(--radius-full);
    border: 1px solid var(--border-soft);
  }

  .status.ready {
    color: var(--success);
    border-color: var(--accent-green-muted);
  }

  .db-selector {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: 5px var(--space-3);
    background: var(--surface-1);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    color: var(--text-secondary);
    font-size: 13px;
    cursor: pointer;
    transition: border-color var(--transition-fast);
  }

  .db-selector:hover {
    border-color: var(--border-medium);
  }

  .db-badge {
    padding: 2px 6px;
    background: var(--accent-green-muted);
    color: var(--accent-green);
    font-size: 10px;
    font-weight: 600;
    font-family: var(--font-mono);
    text-transform: uppercase;
    border-radius: var(--radius-sm);
  }

  .feed {
    overflow-y: auto;
    padding: var(--space-4) var(--space-6);
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    min-height: 0;
    overscroll-behavior: contain;
    scroll-behavior: smooth;
  }

  .feed:active {
    scroll-behavior: auto;
  }

  :global(.message),
  :global(.sql-cell),
  :global(.python-cell) {
    flex-shrink: 0;
  }

  .empty-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: var(--space-3);
    padding: var(--space-8);
    text-align: center;
    color: var(--text-dim);
  }

  .empty-icon {
    color: var(--text-dim);
    opacity: 0.4;
  }

  .empty-state h3 {
    margin: 0;
    color: var(--text-secondary);
    font-family: var(--font-heading);
    font-size: 16px;
    font-weight: 400;
  }

  .empty-state p {
    margin: 0;
    font-size: 14px;
  }

  .meta-cell {
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-lg);
    padding: var(--space-3);
    background: var(--surface-0);
  }

  .meta-cell header {
    display: flex;
    align-items: center;
    margin-bottom: var(--space-2);
  }

  .meta-cell strong {
    color: var(--text-muted);
    font-family: var(--font-heading);
    font-size: 12px;
  }

  .meta-cell button {
    margin-left: auto;
    border: 1px solid var(--border-soft);
    background: transparent;
    color: var(--text-dim);
    border-radius: var(--radius-md);
    padding: var(--space-1) var(--space-2);
    font-size: 11px;
    cursor: pointer;
    transition: color var(--transition-fast);
  }

  .meta-cell button:hover {
    color: var(--text-secondary);
    border-color: var(--border-medium);
  }

  .meta-cell pre {
    margin: 0;
    font-family: var(--font-mono);
    font-size: 12px;
    background: var(--bg-0);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    padding: var(--space-3);
    overflow-x: auto;
    color: var(--text-muted);
  }

  .composer-container {
    padding: var(--space-4);
    background: var(--bg-1);
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
    min-height: 56px;
    padding: var(--space-3) var(--space-4);
    background: var(--surface-0);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-lg);
    color: var(--text-primary);
    font-family: var(--font-body);
    font-size: 14px;
    line-height: 1.5;
    resize: none;
    transition: border-color var(--transition-fast);
  }

  textarea:focus {
    outline: none;
    border-color: var(--border-strong);
  }

  textarea::placeholder {
    color: var(--text-dim);
  }

  .send-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 40px;
    height: 40px;
    background: var(--accent-green);
    border: none;
    border-radius: var(--radius-md);
    color: #111;
    cursor: pointer;
    transition: opacity var(--transition-fast);
    flex-shrink: 0;
  }

  .send-btn:hover:not(:disabled) {
    opacity: 0.85;
  }

  .send-btn:disabled {
    opacity: 0.3;
    cursor: not-allowed;
  }

  .loading {
    width: 16px;
    height: 16px;
    border: 2px solid rgba(0, 0, 0, 0.2);
    border-top-color: #111;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }

  @media (max-width: 1100px) {
    .workspace {
      grid-template-columns: 1fr;
      grid-template-rows: minmax(0, 1fr) auto;
    }

    .workspace.panel-collapsed {
      grid-template-rows: minmax(0, 1fr) 56px;
    }
  }

  /* Import Panel */
  .import-panel {
    background: var(--surface-0);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-lg);
    margin-bottom: var(--space-3);
    overflow: hidden;
  }

  .import-panel-header {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-2) var(--space-3);
    border-bottom: 1px solid var(--border-soft);
    color: var(--text-muted);
    font-size: 12px;
    font-weight: 500;
  }

  .import-panel-header .close-btn {
    margin-left: auto;
    padding: var(--space-1);
    background: transparent;
    border: none;
    color: var(--text-dim);
    cursor: pointer;
    border-radius: var(--radius-sm);
    transition: color var(--transition-fast);
  }

  .import-panel-header .close-btn:hover {
    color: var(--text-primary);
  }

  .uploaded-files-list {
    padding: var(--space-2);
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
  }

  .uploaded-file-item {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-2) var(--space-3);
    font-size: 13px;
  }

  .uploaded-file-item .filename {
    color: var(--text-primary);
    font-family: var(--font-mono);
    font-size: 12px;
  }

  .uploaded-file-item .filesize {
    color: var(--text-dim);
    font-size: 11px;
  }

  .uploaded-file-item .file-actions {
    margin-left: auto;
    display: flex;
    gap: var(--space-1);
  }

  .import-btn, .remove-btn {
    padding: var(--space-1) var(--space-2);
    font-size: 11px;
    border-radius: var(--radius-sm);
    cursor: pointer;
    transition: all var(--transition-fast);
  }

  .import-btn {
    background: var(--accent-green-muted);
    border: 1px solid var(--accent-green);
    color: var(--accent-green);
  }

  .import-btn:hover:not(:disabled) {
    background: var(--accent-green);
    color: #111;
  }

  .remove-btn {
    background: transparent;
    border: 1px solid var(--border-soft);
    color: var(--text-dim);
    padding: var(--space-1);
  }

  .remove-btn:hover:not(:disabled) {
    background: var(--danger-muted);
    border-color: var(--danger);
    color: var(--danger);
  }

  .importing-indicator {
    font-size: 11px;
    color: var(--text-dim);
    font-style: italic;
  }

  .import-success, .import-error {
    padding: var(--space-2) var(--space-3);
    margin: 0 var(--space-2) var(--space-2);
    border-radius: var(--radius-sm);
    font-size: 12px;
  }

  .import-success {
    background: var(--accent-green-muted);
    color: var(--accent-green);
  }

  .import-error {
    background: var(--danger-muted);
    color: var(--danger);
  }

  .existing-tables {
    padding: var(--space-3);
    border-top: 1px solid var(--border-soft);
  }

  .existing-tables-header {
    font-size: 11px;
    color: var(--text-dim);
    margin-bottom: var(--space-2);
  }

  .tables-list {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-1);
  }

  .table-badge {
    padding: 2px 6px;
    background: var(--surface-1);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-sm);
    font-size: 11px;
    color: var(--text-dim);
    font-family: var(--font-mono);
  }

  .table-badge.imported {
    color: var(--accent-green);
  }

  .table-badge.more {
    background: transparent;
    border-style: dashed;
  }

  /* Upload */
  .upload-btn-wrapper {
    position: absolute;
    bottom: var(--space-2);
    right: var(--space-2);
    display: flex;
    gap: var(--space-1);
  }

  .upload-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    background: transparent;
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    color: var(--text-dim);
    cursor: pointer;
    transition: all var(--transition-fast);
    position: relative;
  }

  .upload-btn:hover {
    border-color: var(--border-medium);
    color: var(--text-secondary);
  }

  .upload-btn.active {
    color: var(--accent-green);
    border-color: var(--accent-green);
  }

  .upload-badge {
    position: absolute;
    top: -4px;
    right: -4px;
    width: 14px;
    height: 14px;
    background: var(--accent-green);
    color: #111;
    font-size: 9px;
    font-weight: 600;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  /* Thinking */
  .thinking-indicator {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    padding: var(--space-3) var(--space-4);
    color: var(--text-dim);
  }

  .thinking-dots {
    display: flex;
    gap: 3px;
  }

  .thinking-dots .dot {
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: var(--text-dim);
    opacity: 0.5;
    animation: thinking-pulse 1.4s ease-in-out infinite;
  }

  .thinking-dots .dot:nth-child(2) {
    animation-delay: 0.2s;
  }

  .thinking-dots .dot:nth-child(3) {
    animation-delay: 0.4s;
  }

  @keyframes thinking-pulse {
    0%, 80%, 100% { opacity: 0.3; }
    40% { opacity: 0.8; }
  }

  .thinking-label {
    font-size: 12px;
    font-family: var(--font-mono);
  }
</style>
