<script lang="ts">
  import { onDestroy, onMount, tick } from "svelte";

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
    importCSV,
    fetchWorldlineTables,
  } from "$lib/api/client";
  import type {
    StreamDeltaPayload,
    Thread,
    TimelineEvent,
    WorldlineItem,
  } from "$lib/types";
  
  // Icons
  import { Database } from "lucide-svelte";
  import { Send } from "lucide-svelte";
  import { ChevronDown } from "lucide-svelte";
  import { Sparkles } from "lucide-svelte";
  import { Upload } from "lucide-svelte";
  import { FileSpreadsheet } from "lucide-svelte";
  import { X } from "lucide-svelte";

  type Provider = "gemini" | "openai" | "openrouter";
  type DraftToolKind = "sql" | "python";

  interface DraftToolCall {
    id: string;
    kind: DraftToolKind;
    code: string;
    createdAt: string;
  }

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
  let artifactsPanelCollapsed = false;
  let selectedArtifactId: string | null = null;
  let assistantDraftText = "";
  let assistantDraftCreatedAt = "";
  let toolCallDrafts: DraftToolCall[] = [];
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
  $: cells = groupEventsIntoCells(activeEvents);
  $: currentThread = $activeThread;
  $: hasDraftOutput = assistantDraftText.length > 0 || toolCallDrafts.length > 0;

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
    assistantDraftText = "";
    assistantDraftCreatedAt = "";
    toolCallDrafts = [];
  }

  function upsertToolCallDraft(
    kind: DraftToolKind,
    callId: string | undefined,
    delta: string,
  ): void {
    if (!delta) {
      return;
    }
    const draftId = callId && callId.trim() ? callId : `${kind}-draft`;
    const existingIndex = toolCallDrafts.findIndex((draft) => draft.id === draftId);
    if (existingIndex === -1) {
      toolCallDrafts = [
        ...toolCallDrafts,
        {
          id: draftId,
          kind,
          code: delta,
          createdAt: new Date().toISOString(),
        },
      ];
      return;
    }

    const existing = toolCallDrafts[existingIndex];
    const updated = {
      ...existing,
      code: existing.code + delta,
    };
    toolCallDrafts = [
      ...toolCallDrafts.slice(0, existingIndex),
      updated,
      ...toolCallDrafts.slice(existingIndex + 1),
    ];
  }

  function removeToolCallDraft(kind: DraftToolKind, callId: string | null): void {
    if (callId) {
      toolCallDrafts = toolCallDrafts.filter((draft) => draft.id !== callId);
      return;
    }
    const firstMatch = toolCallDrafts.find((draft) => draft.kind === kind);
    if (!firstMatch) {
      return;
    }
    toolCallDrafts = toolCallDrafts.filter((draft) => draft.id !== firstMatch.id);
  }

  function callIdFromPayload(payload: Record<string, unknown>): string | null {
    const callId = payload.call_id;
    if (typeof callId === "string" && callId.trim()) {
      return callId;
    }
    return null;
  }

  function clearDraftFromPersistedEvent(event: TimelineEvent): void {
    if (event.type === "assistant_message") {
      assistantDraftText = "";
      assistantDraftCreatedAt = "";
      return;
    }

    if (event.type === "tool_call_sql") {
      removeToolCallDraft("sql", callIdFromPayload(event.payload));
      return;
    }

    if (event.type === "tool_call_python") {
      removeToolCallDraft("python", callIdFromPayload(event.payload));
    }
  }

  function handleStreamDelta(delta: StreamDeltaPayload): void {
    if (delta.type === "assistant_text") {
      if (typeof delta.delta === "string" && delta.delta.length > 0) {
        if (!assistantDraftText) {
          assistantDraftCreatedAt = new Date().toISOString();
        }
        assistantDraftText += delta.delta;
        statusText = "Composing response...";
      }
      scrollFeedToBottom();
      return;
    }

    if (delta.type === "tool_call_sql") {
      upsertToolCallDraft("sql", delta.call_id, delta.delta ?? "");
      statusText = "Drafting SQL...";
      scrollFeedToBottom();
      return;
    }

    if (delta.type === "tool_call_python") {
      upsertToolCallDraft("python", delta.call_id, delta.delta ?? "");
      statusText = "Drafting Python...";
      scrollFeedToBottom();
    }
  }

  function toDraftCallEvent(draft: DraftToolCall): TimelineEvent {
    if (draft.kind === "sql") {
      return {
        id: `draft-sql-${draft.id}`,
        parent_event_id: null,
        type: "tool_call_sql",
        payload: {
          sql: draft.code,
          limit: 100,
          call_id: draft.id,
        },
        created_at: draft.createdAt,
      };
    }

    return {
      id: `draft-python-${draft.id}`,
      parent_event_id: null,
      type: "tool_call_python",
      payload: {
        code: draft.code,
        timeout: 30,
        call_id: draft.id,
      },
      created_at: draft.createdAt,
    };
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
          clearDraftFromPersistedEvent(frame.event);
          appendEvent(frame.worldline_id, frame.event);
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
          handleStreamDelta(frame.delta);
        },
        onDone: async (done) => {
          activeWorldlineId = done.worldline_id;
          await refreshWorldlines();
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
              onBranch={() => branchFromEvent(cell.result?.id ?? cell.call?.id ?? "")}
            />
          {:else if cell.kind === "python"}
            <PythonCell
              callEvent={cell.call}
              resultEvent={cell.result}
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

        {#each toolCallDrafts as draft (draft.id)}
          {@const callEvent = toDraftCallEvent(draft)}
          {#if draft.kind === "sql"}
            <SqlCell callEvent={callEvent} resultEvent={null} initialCollapsed={false} />
          {:else}
            <PythonCell
              callEvent={callEvent}
              resultEvent={null}
              initialCollapsed={false}
              showArtifacts={false}
              artifactLinkMode="panel"
              on:artifactselect={handleArtifactSelect}
            />
          {/if}
        {/each}

        {#if assistantDraftText}
          <MessageCell
            role="assistant"
            text={assistantDraftText}
            createdAt={assistantDraftCreatedAt}
          />
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
    background: var(--bg-0);
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
    overscroll-behavior: contain;
    scroll-behavior: smooth;
    contain: layout style;
  }

  /* When user is actively scrolling, disable smooth scroll for immediate response */
  .feed:active {
    scroll-behavior: auto;
  }

  /* Optimize message cells for better scroll performance */
  :global(.message),
  :global(.sql-cell),
  :global(.python-cell) {
    contain: layout style paint;
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

    .workspace.panel-collapsed {
      grid-template-rows: minmax(0, 1fr) 56px;
    }
  }

  /* Import Panel Styles */
  .import-panel {
    background: var(--surface-1);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-lg);
    margin-bottom: var(--space-3);
    overflow: hidden;
  }

  .import-panel-header {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-3) var(--space-4);
    background: var(--surface-2);
    border-bottom: 1px solid var(--border-soft);
    color: var(--text-secondary);
    font-size: 13px;
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
    transition: all var(--transition-fast);
  }

  .import-panel-header .close-btn:hover {
    color: var(--text-primary);
    background: var(--surface-hover);
  }

  .uploaded-files-list {
    padding: var(--space-2);
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }

  .uploaded-file-item {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-2) var(--space-3);
    background: var(--surface-0);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
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
    background: var(--accent-cyan-muted);
    border: 1px solid var(--accent-cyan);
    color: var(--accent-cyan);
  }

  .import-btn:hover:not(:disabled) {
    background: var(--accent-cyan);
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
    border-radius: var(--radius-md);
    font-size: 12px;
  }

  .import-success {
    background: rgba(0, 255, 100, 0.1);
    border: 1px solid rgba(0, 255, 100, 0.3);
    color: #2ecc71;
  }

  .import-error {
    background: var(--danger-muted);
    border: 1px solid var(--danger);
    color: var(--danger);
  }

  .existing-tables {
    padding: var(--space-3);
    border-top: 1px solid var(--border-soft);
  }

  .existing-tables-header {
    font-size: 12px;
    color: var(--text-dim);
    margin-bottom: var(--space-2);
  }

  .tables-list {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-1);
  }

  .table-badge {
    padding: 2px 8px;
    background: var(--surface-0);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-sm);
    font-size: 11px;
    color: var(--text-dim);
    font-family: var(--font-mono);
  }

  .table-badge.imported {
    background: var(--accent-cyan-muted);
    border-color: var(--accent-cyan);
    color: var(--accent-cyan);
  }

  .table-badge.more {
    background: transparent;
    border-style: dashed;
  }

  /* Upload Button Styles */
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
    width: 32px;
    height: 32px;
    background: var(--surface-0);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    color: var(--text-dim);
    cursor: pointer;
    transition: all var(--transition-fast);
    position: relative;
  }

  .upload-btn:hover {
    background: var(--surface-hover);
    border-color: var(--border-medium);
    color: var(--text-secondary);
  }

  .upload-btn.active {
    background: var(--accent-cyan-muted);
    border-color: var(--accent-cyan);
    color: var(--accent-cyan);
  }

  .upload-badge {
    position: absolute;
    top: -4px;
    right: -4px;
    width: 16px;
    height: 16px;
    background: var(--accent-orange);
    color: #111;
    font-size: 10px;
    font-weight: 600;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
  }
</style>
