<script lang="ts">
  import { onDestroy, onMount, tick } from "svelte";

  import { groupDisplayItemsIntoCells } from "$lib/cells";
  import { buildDisplayItems, createStreamingState, type StreamingState } from "$lib/streaming";
  import MessageCell from "$lib/components/MessageCell.svelte";
  import PythonCell from "$lib/components/PythonCell.svelte";
  import SqlCell from "$lib/components/SqlCell.svelte";
  import SubagentCell from "$lib/components/SubagentCell.svelte";
  import ArtifactsPanel from "$lib/components/ArtifactsPanel.svelte";
  import WorldlinePicker from "$lib/components/WorldlinePicker.svelte";
  import { activeThread, threads } from "$lib/stores/threads";
  import { chatJobs } from "$lib/stores/chatJobs";
  import { createThread, fetchWorldlineTables } from "$lib/api/client";
  import {
    buildContextualMessage as buildContextualChatMessage,
    providerLabel,
    toggleSelectedId,
    type ContextSettings,
    type OutputType,
    type Provider,
    type StoredConnector,
  } from "$lib/chat/contextControls";
  import { runtimeStatePath, runtimeStateReasons, type RuntimeStateTransition } from "$lib/chat/stateTrace";
  import { computeWorldlineQueueStats } from "$lib/chat/queueStats";
  import { removeUploadedFileByName } from "$lib/chat/csvImportPanel";
  import { refreshWorldlineContextSnapshot } from "$lib/chat/worldlineContext";
  import { createSessionStore } from "$lib/chat/sessionStore";
  import { createCSVImportController } from "$lib/chat/csvImportController";
  import { createScrollToBottom } from "$lib/chat/scrollToBottom";
  import { createMenuController, type MenuId } from "$lib/chat/menuController";
  import type { Thread, TimelineEvent, WorldlineItem } from "$lib/types";
  
  // Icons
  import {
    Database,
    Send,
    ChevronDown,
    Sparkles,
    Plus,
    Wrench,
    Upload,
    FileText,
    X,
  } from "lucide-svelte";

  const sessionStore = createSessionStore();

  let threadId = "";
  let activeWorldlineId = "";
  let worldlines: WorldlineItem[] = [];
  let eventsByWorldline: Record<string, TimelineEvent[]> = {};
  let streamingByWorldline: Record<string, StreamingState> = {};
  let sendingByWorldline: Record<string, boolean> = {};
  let stateTraceByWorldline: Record<string, RuntimeStateTransition[]> = {};
  let statusText = "Initializing...";
  let selectedArtifactId: string | null = null;
  let isReady = false;
  let isHydratingThread = false;

  let prompt = "";
  let provider: Provider = "openrouter";
  let model = "";
  let activeMenu: MenuId | null = null;
  let composerExpanded = false;
  let artifactsPanelCollapsed = false;
  let activeStreamingState: StreamingState = createStreamingState();
  let feedElement: HTMLDivElement | null = null;
  let activeWorldlineQueueDepth = 0;
  let activeWorldlineRunningJobs = 0;
  let activeWorldlineQueuedJobs = 0;
  let currentThread: Thread | null = null;

  // CSV Import state
  let uploadedFiles: File[] = [];
  let worldlineTables: Awaited<ReturnType<typeof fetchWorldlineTables>> | null = null;
  let outputType: OutputType = "none";
  let availableConnectors: StoredConnector[] = [];
  let selectedConnectorIds: string[] = [];
  let connectorSelectionByWorldline: Record<string, string[]> = {};
  let selectedContextTables: string[] = [];
  let contextSettings: ContextSettings = {
    webSearch: true,
    dashboards: false,
    textToSql: true,
    ontology: false,
  };

  $: ({
    threadId,
    activeWorldlineId,
    worldlines,
    eventsByWorldline,
    streamingByWorldline,
    sendingByWorldline,
    stateTraceByWorldline,
    statusText,
    selectedArtifactId,
    isReady,
    isHydratingThread,
  } = $sessionStore);

  $: activeEvents = eventsByWorldline[activeWorldlineId] ?? [];
  $: activeStreamingState = streamingByWorldline[activeWorldlineId] ?? createStreamingState();
  $: displayItems = buildDisplayItems(activeEvents, activeStreamingState);
  $: cells = groupDisplayItemsIntoCells(displayItems);
  $: currentThread = $activeThread;
  $: isActiveWorldlineSending = Boolean(activeWorldlineId && sendingByWorldline[activeWorldlineId]);
  $: activeStateTrace = stateTraceByWorldline[activeWorldlineId] ?? [];
  $: activeStatePath = runtimeStatePath(activeStateTrace);
  $: activeStateReasons = runtimeStateReasons(activeStateTrace);
  $: hasDraftOutput =
    activeStreamingState.text.length > 0 || activeStreamingState.toolCalls.size > 0;
  $: isEmptyChat = cells.length === 0 && !hasDraftOutput && !isActiveWorldlineSending;
  $: {
    const queueStats = computeWorldlineQueueStats(activeWorldlineId, $chatJobs.jobsById);
    activeWorldlineQueueDepth = queueStats.depth;
    activeWorldlineRunningJobs = queueStats.running;
    activeWorldlineQueuedJobs = queueStats.queued;
  }

  const csvController = createCSVImportController({
    get uploadedFiles() { return uploadedFiles; },
    setUploadedFiles: (f) => { uploadedFiles = f; },
    setStatusText: (s) => {
      sessionStore.setStatusText(s);
    },
    setSelectedContextTables: (t) => { selectedContextTables = Array.isArray(t) ? t : t(selectedContextTables); },
    setWorldlineTables: (t) => { worldlineTables = t; },
    get activeWorldlineId() { return activeWorldlineId; },
    removeUploadedFile: (filename) => {
      uploadedFiles = removeUploadedFileByName(uploadedFiles, filename);
    },
  });
  const scrollController = createScrollToBottom(() => feedElement);
  const menuController = createMenuController();

  sessionStore.configureRuntime({
    refreshContextTables: () => refreshWorldlineContextTables(),
    scrollToBottom: (force = false) => scrollFeedToBottom(force),
    onTurnCompleted: () => {
      if (currentThread) {
        threads.updateThread(currentThread.id, {
          messageCount: (currentThread.messageCount || 0) + 1,
          lastActivity: new Date().toISOString(),
        });
      }
    },
  });

  function handleOpenWorldlineEvent(event: Event): void {
    const detail = (event as CustomEvent<{ threadId?: string; worldlineId?: string }>).detail;
    if (!detail?.worldlineId) {
      return;
    }
    if (detail.threadId && detail.threadId !== threadId) {
      return;
    }
    void sessionStore.selectWorldline(detail.worldlineId).catch(() => undefined);
  }

  function handleSubagentOpenWorldline(
    event: CustomEvent<{ worldlineId: string }>,
  ): void {
    const worldlineId = event.detail?.worldlineId;
    if (!worldlineId) {
      return;
    }
    void sessionStore.selectWorldline(worldlineId).catch(() => undefined);
  }

  function handleVisibilityChange(): void {
    if (typeof document === "undefined") return;
    if (document.visibilityState === "visible") {
      void chatJobs.poll();
    }
  }

  onMount(async () => {
    // Only run browser-specific code on the client
    if (typeof window === "undefined") return;
    
    window.addEventListener("textql:open-worldline", handleOpenWorldlineEvent as EventListener);
    document.addEventListener("visibilitychange", handleVisibilityChange);
    await threads.loadThreads();

    // Load from localStorage if available
    activeThread.loadFromStorage();
    if (
      $activeThread &&
      $threads.threads.length > 0 &&
      !$threads.threads.some((thread) => thread.id === $activeThread?.id)
    ) {
      const fallbackThread = $threads.threads[0];
      activeThread.set(fallbackThread);
      activeThread.saveToStorage(fallbackThread);
    }
    if (!$activeThread && $threads.threads.length > 0) {
      const fallbackThread = $threads.threads[0];
      activeThread.set(fallbackThread);
      activeThread.saveToStorage(fallbackThread);
    }
    
    // Check if we have a stored worldline from creating a new thread
    const storedWorldlineId = localStorage.getItem("textql_active_worldline");
    
    if ($activeThread) {
      await sessionStore.hydrateThread($activeThread.id, storedWorldlineId ?? undefined);
    } else {
      await initializeSession();
    }

    await tick();
    await refreshWorldlineContextTables();
    scrollFeedToBottom(true);
    void chatJobs.poll();
  });

  onDestroy(() => {
    scrollController.dispose();
    if (typeof window !== "undefined") {
      window.removeEventListener(
        "textql:open-worldline",
        handleOpenWorldlineEvent as EventListener,
      );
    }
    if (typeof document !== "undefined") {
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    }
  });

  $: if ($activeThread?.id && isReady && $activeThread.id !== threadId && !isHydratingThread) {
    void sessionStore.hydrateThread($activeThread.id);
  }

  function handleFeedScroll(): void {
    scrollController.handleScroll();
  }

  function scrollFeedToBottom(force = false): void {
    scrollController.scrollToBottom(force);
  }

  function closeContextMenus(): void {
    activeMenu = menuController.closeAll();
  }

  function toggleConnector(id: string): void {
    selectedConnectorIds = toggleSelectedId(selectedConnectorIds, id);
    if (activeWorldlineId) {
      connectorSelectionByWorldline = {
        ...connectorSelectionByWorldline,
        [activeWorldlineId]: selectedConnectorIds,
      };
    }
  }

  function toggleContextTable(name: string): void {
    selectedContextTables = toggleSelectedId(selectedContextTables, name);
  }

  function contextTableLabel(table: {
    name: string;
    type: "native" | "imported_csv" | "external";
    source_filename?: string;
  }): string {
    if (table.type === "imported_csv" && table.source_filename) {
      return table.source_filename;
    }
    return table.name;
  }

  async function refreshWorldlineContextTables(): Promise<void> {
    const snapshot = await refreshWorldlineContextSnapshot({
      worldlineId: activeWorldlineId,
      selectedContextTables,
      connectorSelectionByWorldline,
    });

    worldlineTables = snapshot.worldlineTables;
    availableConnectors = snapshot.availableConnectors;
    selectedConnectorIds = snapshot.selectedConnectorIds;
    connectorSelectionByWorldline = snapshot.connectorSelectionByWorldline;
    selectedContextTables = snapshot.selectedContextTables;
  }

  function buildContextualMessage(message: string): string {
    return buildContextualChatMessage(message, {
      outputType,
      availableConnectors,
      selectedConnectorIds,
      selectedContextTables,
      contextSettings,
    });
  }

  async function initializeSession(): Promise<void> {
    try {
      sessionStore.setStatusText("Creating thread...");
      
      // Create thread via API
      const thread = await createThread("AnalyticZ Session");
      
      // Create local thread object
      const newThread = {
        id: thread.thread_id,
        name: "AnalyticZ Session",
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
      
      sessionStore.initializeThreadSession(thread.thread_id);
    } catch (error) {
      sessionStore.setStatusText(error instanceof Error ? error.message : "Initialization failed");
      console.error("Initialization error:", error);
    }
  }

  async function handleWorldlineSelect(
    event: CustomEvent<{ id: string }>,
  ): Promise<void> {
    await sessionStore.selectWorldline(event.detail.id);
  }

  async function branchFromEvent(eventId: string): Promise<void> {
    await sessionStore.branchFromEvent(eventId);
  }

  function handleArtifactSelect(event: CustomEvent<{ artifactId: string }>): void {
    artifactsPanelCollapsed = false;
    sessionStore.selectArtifact(event.detail.artifactId);
  }

  async function sendPrompt(): Promise<void> {
    const message = prompt.trim();
    if (!message) {
      return;
    }

    try {
      await sessionStore.sendPrompt({
        message,
        provider,
        model: model.trim() || undefined,
        maxIterations: 20,
        buildContextualMessage,
        beforeSend: async (worldlineId) => {
          if (uploadedFiles.length > 0) {
            await importUploadedFilesBeforeSend(worldlineId);
          }
        },
        onAccepted: () => {
          prompt = "";
          closeContextMenus();
        },
        onStreamingStart: () => {
          scrollController.setAutoScroll(true);
        },
      });
    } catch {
      return;
    }
  }

  function getProviderIcon(provider: Provider) {
    return providerLabel(provider);
  }

  // CSV Import functions delegated to controller
  function handleFileSelect(event: Event) {
    csvController.handleFileSelect(event);
  }

  function removeUploadedFile(filename: string) {
    uploadedFiles = removeUploadedFileByName(uploadedFiles, filename);
  }

  async function importUploadedFilesBeforeSend(worldlineId: string): Promise<void> {
    await csvController.importUploadedFilesBeforeSend(worldlineId);
  }
</script>

<div class="chat-container" class:empty-chat={isEmptyChat}>
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
          on:click={() => activeMenu = menuController.toggle("provider")}
        >
          <Sparkles size={14} />
          <span>{getProviderIcon(provider)}</span>
          <ChevronDown size={14} />
        </button>
        
        {#if activeMenu === "provider"}
          <div class="provider-menu">
            <button 
              class="provider-option"
              class:active={provider === "openai"}
              on:click={() => { provider = "openai"; activeMenu = menuController.close("provider"); }}
            >
              OpenAI
            </button>
            <button 
              class="provider-option"
              class:active={provider === "openrouter"}
              on:click={() => { provider = "openrouter"; activeMenu = menuController.close("provider"); }}
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
        {#if activeWorldlineQueueDepth > 0}
          <span class="queue-chip">{activeWorldlineQueueDepth} queued</span>
        {/if}
      </span>
      {#if activeStatePath}
        <span class="state-path" title={activeStateReasons}>{activeStatePath}</span>
      {/if}
      
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
          {:else if cell.kind === "subagents"}
            <SubagentCell
              callEvent={cell.call}
              resultEvent={cell.result}
              on:openworldline={handleSubagentOpenWorldline}
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

        {#if isActiveWorldlineSending && !hasDraftOutput}
          <div class="thinking-indicator">
            <div class="thinking-dots">
              <span class="dot"></span>
              <span class="dot"></span>
              <span class="dot"></span>
            </div>
            <span class="thinking-label">Thinking...</span>
          </div>
        {:else if activeWorldlineQueueDepth > 0}
          <div class="thinking-indicator background">
            <div class="thinking-dots muted">
              <span class="dot"></span>
              <span class="dot"></span>
              <span class="dot"></span>
            </div>
            <span class="thinking-label">
              {#if activeWorldlineRunningJobs > 0}
                {activeWorldlineRunningJobs} background running{#if activeWorldlineQueuedJobs > 0} · {activeWorldlineQueuedJobs} queued{/if}
              {:else}
                {activeWorldlineQueuedJobs} queued in background
              {/if}
            </span>
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
    {#if isEmptyChat}
      <div class="welcome-header">
        <div class="welcome-icon">
          <Database size={48} />
        </div>
        <h1 class="welcome-title">What can I help you analyze?</h1>
        <p class="welcome-subtitle">Ask questions, run SQL queries, or generate Python analysis</p>
      </div>
    {/if}
    <div class="context-toolbar">
      <div class="context-dropdown">
        <button
          type="button"
          class="context-btn"
          on:click={() => activeMenu = menuController.toggle("outputType")}
        >
          <span>
            {outputType === "report"
              ? "Report"
              : outputType === "dashboard"
                ? "Dashboard"
                : "No strict format"}
          </span>
          <ChevronDown size={13} />
        </button>
        {#if activeMenu === "outputType"}
          <div class="context-menu">
            <div class="context-menu-title">Output Type</div>
            <button type="button" class="context-option" on:click={() => { outputType = "none"; activeMenu = menuController.close("outputType"); }}>
              {outputType === "none" ? "✓ " : ""}No strict format
            </button>
            <button type="button" class="context-option" on:click={() => { outputType = "report"; activeMenu = menuController.close("outputType"); }}>
              {outputType === "report" ? "✓ " : ""}Report
            </button>
            <button type="button" class="context-option" on:click={() => { outputType = "dashboard"; activeMenu = menuController.close("outputType"); }}>
              {outputType === "dashboard" ? "✓ " : ""}Dashboard
            </button>
          </div>
        {/if}
      </div>

      <div class="context-dropdown">
        <button
          type="button"
          class="context-btn"
          on:click={() => activeMenu = menuController.toggle("connectors")}
        >
          <Database size={14} />
          <span>{selectedConnectorIds.length > 0 ? `${selectedConnectorIds.length} connectors` : "Connectors"}</span>
          <ChevronDown size={13} />
        </button>
        {#if activeMenu === "connectors"}
          <div class="context-menu connectors-menu">
            <div class="context-menu-title">Connectors</div>
            {#if availableConnectors.length === 0}
              <div class="context-empty">No connectors configured</div>
            {:else}
              {#each availableConnectors as connector}
                <button type="button" class="context-option" on:click={() => toggleConnector(connector.id)}>
                  {selectedConnectorIds.includes(connector.id) ? "✓ " : ""}{connector.name}
                </button>
              {/each}
            {/if}
          </div>
        {/if}
      </div>

      <div class="context-dropdown">
        <button
          type="button"
          class="context-btn"
          on:click={() => activeMenu = menuController.toggle("settings")}
        >
          <Wrench size={14} />
          <span>Settings</span>
          <ChevronDown size={13} />
        </button>
        {#if activeMenu === "settings"}
          <div class="context-menu settings-menu">
            <div class="context-menu-title">Settings</div>
            <label class="toggle-row">
              <span>Web Search</span>
              <input type="checkbox" bind:checked={contextSettings.webSearch} />
            </label>
            <label class="toggle-row">
              <span>Dashboards</span>
              <input type="checkbox" bind:checked={contextSettings.dashboards} />
            </label>
            <label class="toggle-row">
              <span>Text to SQL</span>
              <input type="checkbox" bind:checked={contextSettings.textToSql} />
            </label>
            <label class="toggle-row">
              <span>Ontology</span>
              <input type="checkbox" bind:checked={contextSettings.ontology} />
            </label>
          </div>
        {/if}
      </div>

      <div class="context-dropdown">
        <button
          type="button"
          class="context-btn"
          on:click={() => activeMenu = menuController.toggle("dataContext")}
        >
          <Plus size={14} />
          <span>{selectedContextTables.length > 0 ? `${selectedContextTables.length} table(s)` : "Attach Context"}</span>
          <ChevronDown size={13} />
        </button>
        {#if activeMenu === "dataContext"}
          <div class="context-menu data-menu align-right">
            <div class="context-menu-title">Datasets</div>
            <button type="button" class="context-option" on:click={() => document.getElementById('csv-upload')?.click()}>
              Attach a file
            </button>
            <div class="context-sep"></div>
            <div class="context-menu-title small">Worldline Tables</div>
            {#if worldlineTables && worldlineTables.tables.length > 0}
              {#each worldlineTables.tables.slice(0, 12) as table}
                <button
                  type="button"
                  class="context-option"
                  title={table.name}
                  on:click={() => toggleContextTable(table.name)}
                >
                  {selectedContextTables.includes(table.name) ? "✓ " : ""}{contextTableLabel(table)}
                </button>
              {/each}
            {:else}
              <div class="context-empty">No tables available</div>
            {/if}
          </div>
        {/if}
      </div>
    </div>

    <form class="composer" on:submit|preventDefault={sendPrompt}>
      <div class="composer-input-wrapper">
        {#if uploadedFiles.length > 0}
          <div class="attachment-chips">
            {#each uploadedFiles as file}
              <div class="attachment-chip" title={file.name}>
                <FileText size={14} />
                <span class="attachment-name">{file.name}</span>
                <button
                  type="button"
                  class="attachment-remove"
                  on:click={() => removeUploadedFile(file.name)}
                  title="Remove file"
                >
                  <X size={12} />
                </button>
              </div>
            {/each}
          </div>
        {/if}
        <textarea
          bind:value={prompt}
          placeholder="Ask a question, write SQL, or request Python analysis..."
          rows={composerExpanded ? 4 : 2}
          on:focus={() => composerExpanded = true}
          on:blur={() => composerExpanded = false}
          class:with-attachments={uploadedFiles.length > 0}
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
        </div>
      </div>
      <button 
        type="submit" 
        class="send-btn"
        disabled={!isReady || !prompt.trim()}
      >
        <Send size={18} />
      </button>
    </form>
  </div>
</div>

<style>
  .chat-container {
    display: flex;
    flex-direction: column;
    height: 100vh;
    transition: all 0.5s cubic-bezier(0.4, 0, 0.2, 1);
  }

  .chat-container.empty-chat .workspace {
    flex: 0 0 0;
    min-height: 0;
    opacity: 0;
    pointer-events: none;
    overflow: hidden;
  }

  .chat-container.empty-chat .feed {
    display: none;
  }

  .chat-container.empty-chat .composer-container {
    flex: 1;
    display: flex;
    flex-direction: column;
    justify-content: center;
    border-top: none;
    background: transparent;
    padding-top: 0;
    animation: fadeSlideUp 0.6s cubic-bezier(0.4, 0, 0.2, 1) forwards;
    max-width: 800px;
    margin: 0 auto;
    width: 100%;
  }

  .chat-container.empty-chat .composer {
    max-width: none;
    width: 100%;
  }

  .chat-container.empty-chat .context-toolbar {
    max-width: none;
    width: 100%;
    justify-content: center;
  }

  @keyframes fadeSlideUp {
    from {
      opacity: 0;
      transform: translateY(20px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }

  @keyframes fadeSlideDown {
    from {
      opacity: 0;
      transform: translateY(-10px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }

  .workspace {
    flex: 1;
    min-height: 0;
    display: grid;
    grid-template-columns: minmax(0, 1fr) 380px;
  }

  .workspace.panel-collapsed {
    grid-template-columns: minmax(0, 1fr) 52px;
  }

  .top-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-4);
    padding: var(--space-3) var(--space-5);
    background: var(--bg-1);
    border-bottom: 1px solid var(--border-soft);
    flex-shrink: 0;
    min-height: 52px;
  }

  .top-bar-left {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    flex: 1;
    min-width: 0;
  }

  .top-bar-right {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    flex-shrink: 0;
  }

  .provider-selector {
    position: relative;
  }

  .provider-btn {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: 8px var(--space-3);
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
    top: calc(100% + 6px);
    left: 0;
    min-width: 170px;
    background: var(--surface-0);
    border: 1px solid var(--border-medium);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow-lg);
    z-index: 100;
    overflow: hidden;
    padding: var(--space-1);
    animation: contextMenuIn 0.22s cubic-bezier(0.16, 1, 0.3, 1) forwards;
  }

  .provider-option {
    display: block;
    width: 100%;
    padding: 10px var(--space-3);
    background: transparent;
    border: none;
    border-radius: var(--radius-md);
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
    color: var(--accent-green);
  }

  .model-input {
    padding: 8px var(--space-3);
    background: var(--surface-1);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    color: var(--text-primary);
    font-size: 13px;
    font-family: var(--font-mono);
    min-width: 180px;
    transition: border-color var(--transition-fast), box-shadow var(--transition-fast);
  }

  .model-input:focus {
    outline: none;
    border-color: var(--accent-green);
    box-shadow: var(--shadow-focus);
  }

  .model-input::placeholder {
    color: var(--text-dim);
    font-family: var(--font-body);
  }

  .status {
    font-size: 12px;
    font-family: var(--font-mono);
    color: var(--text-dim);
    padding: 6px var(--space-3);
    background: var(--surface-0);
    border-radius: var(--radius-md);
    border: 1px solid var(--border-soft);
  }

  .status.ready {
    color: var(--success);
    border-color: var(--accent-green-muted);
  }

  .state-path {
    max-width: 360px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 11px;
    font-family: var(--font-mono);
    color: var(--text-dim);
    padding: 6px var(--space-3);
    border: 1px dashed var(--border-soft);
    border-radius: var(--radius-md);
    background: var(--surface-0);
  }

  .queue-chip {
    margin-left: var(--space-2);
    padding: 2px 8px;
    border-radius: var(--radius-full);
    background: var(--surface-2);
    color: var(--text-secondary);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }

  .db-selector {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: 8px var(--space-3);
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
    padding: 3px 8px;
    background: var(--accent-green-muted);
    color: var(--accent-green);
    font-size: 10px;
    font-weight: 600;
    font-family: var(--font-mono);
    text-transform: uppercase;
    border-radius: var(--radius-full);
    letter-spacing: 0.04em;
  }

  .feed {
    overflow-y: auto;
    padding: var(--space-6) var(--space-7);
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
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
    gap: var(--space-4);
    padding: var(--space-8);
    text-align: center;
    color: var(--text-dim);
    animation: messageFadeIn 0.5s cubic-bezier(0.4, 0, 0.2, 1) forwards;
  }

  @keyframes messageFadeIn {
    from {
      opacity: 0;
      transform: translateY(8px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }

  .empty-icon {
    color: var(--text-dim);
    opacity: 0.3;
  }

  .empty-state h3 {
    margin: 0;
    color: var(--text-secondary);
    font-family: var(--font-heading);
    font-size: 18px;
    font-weight: 400;
  }

  .empty-state p {
    margin: 0;
    font-size: 14px;
    color: var(--text-muted);
  }

  .meta-cell {
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    padding: var(--space-3);
    background: var(--surface-0);
    animation: messageSlideIn 0.3s cubic-bezier(0.4, 0, 0.2, 1) forwards;
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
    flex: 0 0 auto;
    transition: all 0.5s cubic-bezier(0.4, 0, 0.2, 1);
  }

  .welcome-header {
    display: flex;
    flex-direction: column;
    align-items: center;
    text-align: center;
    margin-bottom: var(--space-7);
    animation: fadeSlideUp 0.6s cubic-bezier(0.16, 1, 0.3, 1) 0.1s both;
  }

  .welcome-icon {
    color: var(--accent-green);
    opacity: 0.5;
    margin-bottom: var(--space-5);
    filter: drop-shadow(0 0 20px rgba(62, 207, 142, 0.2));
  }

  .welcome-title {
    margin: 0 0 var(--space-3) 0;
    font-family: var(--font-heading);
    font-size: 26px;
    font-weight: 500;
    color: var(--text-primary);
    letter-spacing: -0.02em;
  }

  .welcome-subtitle {
    margin: 0;
    font-size: 15px;
    color: var(--text-muted);
    max-width: 400px;
    line-height: 1.5;
  }

  .context-toolbar {
    max-width: 860px;
    margin: 0 auto var(--space-3);
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-2);
  }

  .context-dropdown {
    position: relative;
  }

  .context-btn {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    padding: 8px var(--space-4);
    background: var(--surface-1);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    color: var(--text-secondary);
    font-size: 13px;
    cursor: pointer;
    transition: all var(--transition-fast);
  }

  .context-btn:hover {
    border-color: var(--border-medium);
    color: var(--text-primary);
    transform: translateY(-1px);
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
  }

  .context-btn:active {
    transform: translateY(0);
    box-shadow: 0 1px 4px rgba(0, 0, 0, 0.1);
  }

  .context-menu {
    position: absolute;
    bottom: calc(100% + 8px);
    left: 0;
    min-width: 220px;
    background: var(--surface-0);
    border: 1px solid var(--border-medium);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow-lg);
    animation: contextMenuIn 0.22s cubic-bezier(0.16, 1, 0.3, 1) forwards;
    transform-origin: bottom center;
    z-index: 200;
    padding: var(--space-2);
    display: flex;
    flex-direction: column;
    gap: 2px;
    max-height: 320px;
    overflow-y: auto;
    max-width: min(340px, calc(100vw - 2 * var(--space-4)));
    backdrop-filter: blur(12px);
  }

  @keyframes contextMenuIn {
    from {
      opacity: 0;
      transform: translateY(6px) scale(0.97);
    }
    to {
      opacity: 1;
      transform: translateY(0) scale(1);
    }
  }

  .connectors-menu {
    min-width: 240px;
  }

  .settings-menu {
    min-width: 220px;
  }

  .data-menu {
    min-width: 260px;
  }

  .align-right {
    left: auto;
    right: 0;
  }

  .context-menu-title {
    padding: var(--space-1) var(--space-2);
    color: var(--text-dim);
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }

  .context-menu-title.small {
    margin-top: var(--space-1);
  }

  .context-option {
    border: none;
    background: transparent;
    border-radius: var(--radius-md);
    text-align: left;
    padding: 8px var(--space-3);
    color: var(--text-secondary);
    font-size: 13px;
    cursor: pointer;
    font-family: var(--font-body);
    transition: background var(--transition-fast), color var(--transition-fast);
  }

  .context-option:hover {
    background: var(--surface-hover);
    color: var(--text-primary);
  }

  .context-empty {
    padding: var(--space-2);
    color: var(--text-dim);
    font-size: 12px;
  }

  .context-sep {
    height: 1px;
    background: var(--border-soft);
    margin: var(--space-1) 0;
  }

  .toggle-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-3);
    padding: 8px var(--space-3);
    border-radius: var(--radius-md);
    color: var(--text-secondary);
    font-size: 13px;
    transition: background var(--transition-fast);
  }

  .toggle-row:hover {
    background: var(--surface-hover);
  }

  .composer {
    display: flex;
    gap: var(--space-3);
    align-items: flex-end;
    max-width: 860px;
    margin: 0 auto;
  }

  .composer-input-wrapper {
    flex: 1;
    position: relative;
  }

  textarea {
    width: 100%;
    min-height: 52px;
    padding: var(--space-4) var(--space-5);
    background: var(--surface-0);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-xl);
    color: var(--text-primary);
    font-family: var(--font-body);
    font-size: 14px;
    line-height: 1.5;
    resize: none;
    transition: border-color var(--transition-normal), box-shadow var(--transition-normal);
  }

  textarea:focus {
    outline: none;
    border-color: var(--accent-green);
    box-shadow: var(--shadow-focus);
  }

  textarea::placeholder {
    color: var(--text-dim);
  }

  textarea.with-attachments {
    padding-top: calc(var(--space-4) + 28px);
  }

  .send-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 44px;
    height: 44px;
    background: var(--accent-green);
    border: none;
    border-radius: var(--radius-lg);
    color: #111;
    cursor: pointer;
    transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
    flex-shrink: 0;
  }

  .send-btn:hover:not(:disabled) {
    transform: translateY(-2px);
    box-shadow: 0 6px 20px rgba(62, 207, 142, 0.35);
  }

  .send-btn:active:not(:disabled) {
    transform: translateY(0) scale(0.96);
    box-shadow: 0 2px 8px rgba(62, 207, 142, 0.25);
  }

  .send-btn:disabled {
    opacity: 0.25;
    cursor: not-allowed;
  }

  .attachment-chips {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-2);
    padding: var(--space-2) var(--space-3) 0;
    position: absolute;
    top: 0;
    left: 0;
    right: 40px;
    z-index: 1;
  }

  .attachment-chip {
    display: inline-flex;
    align-items: center;
    gap: var(--space-1);
    padding: 4px 10px;
    background: var(--accent-green-muted);
    border: 1px solid rgba(62, 207, 142, 0.2);
    border-radius: var(--radius-full);
    font-size: 12px;
    color: var(--text-secondary);
    max-width: 200px;
  }

  .attachment-chip :global(svg) {
    flex-shrink: 0;
    color: var(--accent-green);
  }

  .attachment-name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-family: var(--font-mono);
  }

  .attachment-remove {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 2px;
    background: transparent;
    border: none;
    color: var(--text-dim);
    cursor: pointer;
    border-radius: var(--radius-sm);
    transition: all var(--transition-fast);
    flex-shrink: 0;
  }

  .attachment-remove:hover {
    background: var(--danger-muted);
    color: var(--danger);
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
    width: 30px;
    height: 30px;
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
    background: var(--surface-hover);
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
    padding: var(--space-4) var(--space-5);
    color: var(--text-dim);
    animation: messageSlideIn 0.35s cubic-bezier(0.16, 1, 0.3, 1) forwards;
    border-radius: var(--radius-lg);
    margin: 0 var(--space-2);
  }

  .thinking-indicator.background {
    color: var(--text-secondary);
    background: var(--surface-1);
    border: 1px solid var(--border-soft);
  }

  @keyframes messageSlideIn {
    from {
      opacity: 0;
      transform: translateY(8px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }

  .thinking-dots {
    display: flex;
    gap: 4px;
  }

  .thinking-dots.muted .dot {
    background: var(--text-muted);
    opacity: 0.35;
  }

  .thinking-dots .dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--accent-green);
    opacity: 0.4;
    animation: thinking-pulse 1.4s ease-in-out infinite;
  }

  .thinking-dots .dot:nth-child(2) {
    animation-delay: 0.2s;
  }

  .thinking-dots .dot:nth-child(3) {
    animation-delay: 0.4s;
  }

  @keyframes thinking-pulse {
    0%, 80%, 100% { opacity: 0.2; transform: scale(0.85); }
    40% { opacity: 0.9; transform: scale(1); }
  }

  .thinking-label {
    font-size: 12px;
    font-family: var(--font-mono);
    color: var(--text-muted);
  }
</style>
