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
  import { chatJobs } from "$lib/stores/chatJobs";
  import {
    branchWorldline,
    createChatJob,
    createThread,
    createWorldline,
    fetchThreadWorldlines,
    fetchWorldlineEvents,
    fetchWorldlineSchema,
    streamChatTurn,
    importCSV,
    fetchWorldlineTables,
  } from "$lib/api/client";
  import {
    createOptimisticUserMessage,
    insertOptimisticEvent,
    replaceOptimisticWithReal,
  } from "$lib/chat/optimisticState";
  import {
    buildContextualMessage as buildContextualChatMessage,
    providerLabel,
    toggleSelectedId,
    type ContextSettings,
    type OutputType,
    type Provider,
    type StoredConnector,
  } from "$lib/chat/contextControls";
  import {
    rollbackOptimisticWorldlineEvent,
    withStreamingState,
    withWorldlineSending,
    withoutStreamingState,
  } from "$lib/chat/streamState";
  import {
    pickActiveJobWorldlineId,
    withAppendedWorldlineEvent,
    withVisibleWorldline,
    withWorldlineEvents,
  } from "$lib/chat/worldlineState";
  import {
    extractCsvFiles,
    removeUploadedFileByName,
  } from "$lib/chat/csvImportPanel";
  import type { Thread, TimelineEvent, WorldlineItem } from "$lib/types";
  
  // Icons
  import { Database } from "lucide-svelte";
  import { Send } from "lucide-svelte";
  import { ChevronDown } from "lucide-svelte";
  import { Sparkles } from "lucide-svelte";
  import { Plus } from "lucide-svelte";
  import { Wrench } from "lucide-svelte";
  import { Upload } from "lucide-svelte";
  import { FileText } from "lucide-svelte";
  import { X } from "lucide-svelte";

  let threadId = "";
  let activeWorldlineId = "";
  let worldlines: WorldlineItem[] = [];
  let eventsByWorldline: Record<string, TimelineEvent[]> = {};
  let prompt = "";
  let provider: Provider = "openrouter";
  let model = "";
  let statusText = "Initializing...";
  let sendingByWorldline: Record<string, boolean> = {};
  let isReady = false;
  let showProviderMenu = false;
  let composerExpanded = false;
  let isHydratingThread = false;
  let artifactsPanelCollapsed = false;
  let selectedArtifactId: string | null = null;
  let streamingStateByWorldline: Record<string, StreamingState> = {};
  let activeStreamingState: StreamingState = createStreamingState();
  let feedElement: HTMLDivElement | null = null;
  let shouldAutoScroll = true;
  let pendingScrollRaf = 0;
  let hasPendingScroll = false;
  let pendingScrollForce = false;
  let scrollAttemptsQueue: (() => void)[] = [];
  let activeWorldlineQueueDepth = 0;
  let activeWorldlineRunningJobs = 0;
  let activeWorldlineQueuedJobs = 0;
  type RuntimeStateTransition = {
    from_state: string | null;
    to_state: string;
    reason: string;
  };
  let stateTraceByWorldline: Record<string, RuntimeStateTransition[]> = {};

  // CSV Import state
  let uploadedFiles: File[] = [];
  let worldlineTables: Awaited<ReturnType<typeof fetchWorldlineTables>> | null = null;
  let outputType: OutputType = "none";
  let showOutputTypeMenu = false;
  let showConnectorsMenu = false;
  let showSettingsMenu = false;
  let showDataContextMenu = false;
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

  $: activeEvents = eventsByWorldline[activeWorldlineId] ?? [];
  $: activeStreamingState = streamingStateByWorldline[activeWorldlineId] ?? createStreamingState();
  $: displayItems = buildDisplayItems(activeEvents, activeStreamingState);
  $: cells = groupDisplayItemsIntoCells(displayItems);
  $: currentThread = $activeThread;
  $: isActiveWorldlineSending = Boolean(activeWorldlineId && sendingByWorldline[activeWorldlineId]);
  $: activeStateTrace = stateTraceByWorldline[activeWorldlineId] ?? [];
  $: activeStatePath = activeStateTrace.map((entry) => entry.to_state).join(" -> ");
  $: activeStateReasons = activeStateTrace
    .map((entry) => `${entry.to_state}: ${entry.reason}`)
    .join(" | ");
  $: hasDraftOutput =
    activeStreamingState.text.length > 0 || activeStreamingState.toolCalls.size > 0;
  $: isEmptyChat = cells.length === 0 && !hasDraftOutput && !isActiveWorldlineSending;
  $: activeWorldlineQueueDepth = activeWorldlineId
    ? Object.values($chatJobs.jobsById).filter(
        (job) =>
          job.worldline_id === activeWorldlineId &&
          (job.status === "queued" || job.status === "running"),
      ).length
    : 0;
  $: {
    let running = 0;
    let queued = 0;
    if (activeWorldlineId) {
      for (const job of Object.values($chatJobs.jobsById)) {
        if (job.worldline_id !== activeWorldlineId) {
          continue;
        }
        if (job.status === "running") {
          running += 1;
        } else if (job.status === "queued") {
          queued += 1;
        }
      }
    }
    activeWorldlineRunningJobs = running;
    activeWorldlineQueuedJobs = queued;
  }

  function handleOpenWorldlineEvent(event: Event): void {
    const detail = (event as CustomEvent<{ threadId?: string; worldlineId?: string }>).detail;
    if (!detail?.worldlineId) {
      return;
    }
    if (detail.threadId && detail.threadId !== threadId) {
      return;
    }
    void selectWorldline(detail.worldlineId).catch(() => undefined);
  }

  function handleVisibilityChange(): void {
    if (typeof document === "undefined") return;
    if (document.visibilityState === "visible") {
      void chatJobs.poll();
    }
  }

  function persistPreferredWorldline(worldlineId: string): void {
    if (!worldlineId || typeof localStorage === "undefined") {
      return;
    }
    localStorage.setItem("textql_active_worldline", worldlineId);
  }

  onMount(async () => {
    // Only run browser-specific code on the client
    if (typeof window === "undefined") return;
    
    window.addEventListener("textql:open-worldline", handleOpenWorldlineEvent as EventListener);
    document.addEventListener("visibilitychange", handleVisibilityChange);
    await threads.loadThreads();

    // Load from localStorage if available
    activeThread.loadFromStorage();
    
    // Check if we have a stored worldline from creating a new thread
    const storedWorldlineId = localStorage.getItem("textql_active_worldline");
    
    if ($activeThread) {
      await hydrateThread($activeThread.id, storedWorldlineId ?? undefined);
    } else {
      await initializeSession();
    }

    await tick();
    await refreshWorldlineContextTables();
    scrollFeedToBottom(true);
    void chatJobs.poll();
  });

  onDestroy(() => {
    if (pendingScrollRaf) {
      cancelAnimationFrame(pendingScrollRaf);
      pendingScrollRaf = 0;
    }
    if (typeof window !== "undefined") {
      window.removeEventListener(
        "textql:open-worldline",
        handleOpenWorldlineEvent as EventListener,
      );
    }
    if (typeof document !== "undefined") {
      document.removeEventListener("visibilitychange", handleVisibilityChange);
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

  function setStreamingState(worldlineId: string, state: StreamingState): void {
    streamingStateByWorldline = withStreamingState(
      streamingStateByWorldline,
      worldlineId,
      state,
    );
  }

  function setWorldlineSending(worldlineId: string, isSending: boolean): void {
    sendingByWorldline = withWorldlineSending(
      sendingByWorldline,
      worldlineId,
      isSending,
    );
  }

  function resetStreamingDrafts(worldlineId?: string): void {
    streamingStateByWorldline = withoutStreamingState(
      streamingStateByWorldline,
      worldlineId,
    );
  }

  function appendStateTransition(
    worldlineId: string,
    transition: RuntimeStateTransition,
  ): void {
    const existing = stateTraceByWorldline[worldlineId] ?? [];
    stateTraceByWorldline = {
      ...stateTraceByWorldline,
      [worldlineId]: [...existing, transition].slice(-24),
    };
  }

  async function queuePromptAsJob(message: string, worldlineId: string): Promise<void> {
    try {
      const contextualMessage = buildContextualMessage(message);
      const job = await createChatJob({
        worldlineId,
        message: contextualMessage,
        provider,
        model: model.trim() || undefined,
        maxIterations: 20,
      });
      statusText =
        job.queue_position && job.queue_position > 1
          ? `Queued request (${job.queue_position} in line)`
          : "Queued request";
      chatJobs.registerQueuedJob(job);
      prompt = "";
      closeContextMenus();
      void chatJobs.poll();
    } catch (error) {
      statusText = error instanceof Error ? error.message : "Failed to queue request";
    }
  }

  function rollbackOptimisticMessage(worldlineId: string, optimisticId: string | null): void {
    eventsByWorldline = rollbackOptimisticWorldlineEvent(
      eventsByWorldline,
      worldlineId,
      optimisticId,
    );
  }

  function closeContextMenus(): void {
    showOutputTypeMenu = false;
    showConnectorsMenu = false;
    showSettingsMenu = false;
    showDataContextMenu = false;
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
    if (!activeWorldlineId) {
      worldlineTables = null;
      availableConnectors = [];
      selectedConnectorIds = [];
      selectedContextTables = [];
      return;
    }
    try {
      const [tables, schema] = await Promise.all([
        fetchWorldlineTables(activeWorldlineId),
        fetchWorldlineSchema(activeWorldlineId),
      ]);
      worldlineTables = tables;

      const connectors = schema.attached_databases.map((database) => ({
        id: database.alias,
        name: database.alias,
        isActive: true,
      }));
      availableConnectors = connectors;

      const previousSelection = connectorSelectionByWorldline[activeWorldlineId];
      if (previousSelection) {
        selectedConnectorIds = previousSelection.filter((alias) =>
          connectors.some((connector) => connector.id === alias),
        );
      } else {
        selectedConnectorIds = connectors.map((connector) => connector.id);
      }
      connectorSelectionByWorldline = {
        ...connectorSelectionByWorldline,
        [activeWorldlineId]: selectedConnectorIds,
      };

      selectedContextTables = selectedContextTables.filter((selected) =>
        tables.tables.some((table) => table.name === selected),
      );
      if (selectedContextTables.length === 0 && tables.tables.length > 0) {
        selectedContextTables = [tables.tables[0].name];
      }
    } catch {
      worldlineTables = null;
      availableConnectors = [];
      selectedConnectorIds = [];
      selectedContextTables = [];
    }
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
      stateTraceByWorldline = {};

      await refreshWorldlines();

      if (threadId) {
        await chatJobs.poll();
      }

      const activeJobWorldlineId = pickActiveJobWorldlineId(
        worldlines,
        targetThreadId,
        $chatJobs.jobsById,
      );

      if (activeJobWorldlineId && worldlines.some((w) => w.id === activeJobWorldlineId)) {
        activeWorldlineId = activeJobWorldlineId;
      } else if (preferredWorldlineId && worldlines.some((w) => w.id === preferredWorldlineId)) {
        activeWorldlineId = preferredWorldlineId;
      } else if (worldlines.length > 0) {
        activeWorldlineId = worldlines[0].id;
      }

      if (activeWorldlineId) {
        persistPreferredWorldline(activeWorldlineId);
        await loadWorldline(activeWorldlineId);
        if (activeWorldlineRunningJobs > 0) {
          statusText = `Background job running (${activeWorldlineRunningJobs})`;
        } else if (activeWorldlineQueuedJobs > 0) {
          statusText = `Background jobs queued (${activeWorldlineQueuedJobs})`;
        } else {
          statusText = "Ready";
        }
        scrollFeedToBottom(true);
      } else {
        // No worldline yet - this is expected for new threads
        // Worldline will be created lazily on first message
        statusText = "Ready";
      }

      isReady = true;
    } catch (error) {
      statusText = error instanceof Error ? error.message : "Failed to load thread";
      isReady = false;
    } finally {
      isHydratingThread = false;
    }
  }

  function setWorldlineEvents(worldlineId: string, events: TimelineEvent[]): void {
    eventsByWorldline = withWorldlineEvents(eventsByWorldline, worldlineId, events);
  }

  function appendEvent(worldlineId: string, event: TimelineEvent): void {
    eventsByWorldline = withAppendedWorldlineEvent(eventsByWorldline, worldlineId, event);
  }

  function ensureWorldlineVisible(worldlineId: string): void {
    worldlines = withVisibleWorldline(worldlines, worldlineId);
  }

  async function initializeSession(): Promise<void> {
    try {
      statusText = "Creating thread...";
      resetStreamingDrafts();
      selectedArtifactId = null;
      
      // Create thread via API
      const thread = await createThread("AnalyticZ Session");
      threadId = thread.thread_id;
      
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
      
      // Don't create worldline here - it will be created lazily on first message
      // Just set up empty state
      worldlines = [];
      eventsByWorldline = {};
      activeWorldlineId = "";
      stateTraceByWorldline = {};
      
      statusText = "Ready";
      isReady = true;
      console.log("Session initialized successfully:", { threadId, activeWorldlineId: null });
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
    persistPreferredWorldline(worldlineId);
    selectedArtifactId = null;
    if (!eventsByWorldline[worldlineId]) {
      await loadWorldline(worldlineId);
    }
    await refreshWorldlineContextTables();
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
      persistPreferredWorldline(activeWorldlineId);
      await refreshWorldlines();
      await loadWorldline(activeWorldlineId);
      await refreshWorldlineContextTables();
      statusText = "Branch created";
    } catch (error) {
      statusText = error instanceof Error ? error.message : "Branch failed";
    }
  }

  function handleArtifactSelect(event: CustomEvent<{ artifactId: string }>): void {
    artifactsPanelCollapsed = false;
    selectedArtifactId = event.detail.artifactId;
  }

  async function ensureWorldline(): Promise<string | null> {
    // If we already have a worldline, use it
    if (activeWorldlineId) {
      return activeWorldlineId;
    }
    
    // If we have a thread but no worldline, create one lazily
    if (threadId) {
      statusText = "Creating worldline...";
      try {
        const worldline = await createWorldline(threadId, "main");
        activeWorldlineId = worldline.worldline_id;
        persistPreferredWorldline(activeWorldlineId);
        await refreshWorldlines();
        statusText = "Ready";
        return activeWorldlineId;
      } catch (error) {
        statusText = error instanceof Error ? error.message : "Failed to create worldline";
        return null;
      }
    }
    
    return null;
  }

  async function sendPrompt(): Promise<void> {
    const message = prompt.trim();
    if (!message) {
      return;
    }

    // Ensure we have a worldline (create lazily if needed)
    const requestWorldlineId = await ensureWorldline();
    if (!requestWorldlineId) {
      statusText = "Error: No active worldline. Please refresh the page.";
      return;
    }

    if (uploadedFiles.length > 0) {
      try {
        await importUploadedFilesBeforeSend(requestWorldlineId);
      } catch {
        return;
      }
    }

    const isCurrentWorldlineSending = Boolean(sendingByWorldline[requestWorldlineId]);
    const hasPendingWorldlineJobs = activeWorldlineQueueDepth > 0;

    if (isCurrentWorldlineSending || hasPendingWorldlineJobs) {
      await queuePromptAsJob(message, requestWorldlineId);
      return;
    }

    setWorldlineSending(requestWorldlineId, true);
    prompt = "";
    closeContextMenus();
    statusText = "Agent is thinking...";
    shouldAutoScroll = true;
    resetStreamingDrafts(requestWorldlineId);
    selectedArtifactId = null;
    stateTraceByWorldline = {
      ...stateTraceByWorldline,
      [requestWorldlineId]: [],
    };

    // Optimistic user message — show immediately in the feed
    const { id: optimisticId, event: optimisticEvent } = createOptimisticUserMessage(message);
    const currentEvents = eventsByWorldline[requestWorldlineId] ?? [];
    setWorldlineEvents(
      requestWorldlineId,
      insertOptimisticEvent(currentEvents, optimisticEvent)
    );
    scrollFeedToBottom(true);

    try {
      const contextualMessage = buildContextualMessage(message);
      await streamChatTurn({
        worldlineId: requestWorldlineId,
        message: contextualMessage,
        provider,
        model: model.trim() || undefined,
        maxIterations: 20,
        onEvent: (frame) => {
          const frameWorldlineId = frame.worldline_id;
          ensureWorldlineVisible(frameWorldlineId);
          const frameStreamingState = streamingStateByWorldline[frameWorldlineId] ?? createStreamingState();
          setStreamingState(frameWorldlineId, clearFromEvent(frameStreamingState, frame.event));

          // Remove optimistic user message when real one arrives
          if (frame.event.type === "user_message") {
            const existing = eventsByWorldline[frameWorldlineId] ?? [];
            const { events: updated } = replaceOptimisticWithReal(
              existing,
              optimisticId,
              frame.event
            );
            setWorldlineEvents(frameWorldlineId, updated);
          } else {
            appendEvent(frameWorldlineId, frame.event);
          }

          if (activeWorldlineId === frameWorldlineId) {
            scrollFeedToBottom();
          }

          if (activeWorldlineId === frameWorldlineId) {
            if (frame.event.type === "tool_call_sql") {
              statusText = "Running SQL...";
            } else if (frame.event.type === "tool_call_python") {
              statusText = "Running Python...";
            } else if (frame.event.type === "assistant_message") {
              statusText = "Done";
            } else {
              statusText = "Working...";
            }
          }
        },
        onDelta: (frame) => {
          const frameWorldlineId = frame.worldline_id;
          ensureWorldlineVisible(frameWorldlineId);
          const frameStreamingState = streamingStateByWorldline[frameWorldlineId] ?? createStreamingState();
          setStreamingState(frameWorldlineId, applyDelta(frameStreamingState, frame.delta));
          if (frame.delta.type === "state_transition") {
            const toState =
              typeof frame.delta.to_state === "string" ? frame.delta.to_state : "";
            if (toState) {
              const fromState =
                typeof frame.delta.from_state === "string" ? frame.delta.from_state : null;
              const reason =
                typeof frame.delta.reason === "string" && frame.delta.reason.length > 0
                  ? frame.delta.reason
                  : "unspecified";
              appendStateTransition(frameWorldlineId, {
                from_state: fromState,
                to_state: toState,
                reason,
              });
              if (activeWorldlineId === frameWorldlineId) {
                statusText = `State: ${toState.replace(/_/g, " ")}`;
              }
            }
            if (activeWorldlineId === frameWorldlineId) {
              scrollFeedToBottom();
            }
            return;
          }
          if (activeWorldlineId === frameWorldlineId) {
            if (frame.delta.skipped) {
              if (frame.delta.reason === "invalid_tool_payload") {
                statusText = "Retrying after invalid tool payload...";
              } else {
                statusText = "Skipped repeated tool call...";
              }
            } else if (frame.delta.type === "assistant_text" && !frame.delta.done) {
              statusText = "Composing response...";
            } else if (frame.delta.type === "tool_call_sql" && !frame.delta.done) {
              statusText = "Drafting SQL...";
            } else if (frame.delta.type === "tool_call_python" && !frame.delta.done) {
              statusText = "Drafting Python...";
            }
            scrollFeedToBottom();
          }
        },
        onDone: async (done) => {
          resetStreamingDrafts(done.worldline_id);
          await refreshWorldlines();
          if (done.worldline_id) {
            await loadWorldline(done.worldline_id);
          }
          if (activeWorldlineId === done.worldline_id) {
            statusText = "Done";
            scrollFeedToBottom();
          }
          
          // Update thread message count
          if ($activeThread) {
            threads.updateThread($activeThread.id, {
              messageCount: ($activeThread.messageCount || 0) + 1,
              lastActivity: new Date().toISOString(),
            });
          }
        },
        onError: (error) => {
          resetStreamingDrafts(requestWorldlineId);
          rollbackOptimisticMessage(requestWorldlineId, optimisticId);
          if (activeWorldlineId === requestWorldlineId) {
            statusText = `Error: ${error}`;
          }
        },
      });
    } catch (error) {
      resetStreamingDrafts(requestWorldlineId);
      rollbackOptimisticMessage(requestWorldlineId, optimisticId);
      if (activeWorldlineId === requestWorldlineId) {
        statusText = error instanceof Error ? error.message : "Request failed";
      }
    } finally {
      setWorldlineSending(requestWorldlineId, false);
    }
  }

  function getProviderIcon(provider: Provider) {
    return providerLabel(provider);
  }

  // CSV Import functions
  function handleFileSelect(event: Event) {
    const input = event.target as HTMLInputElement;
    const csvFiles = extractCsvFiles(input.files);
    if (csvFiles.length === 0) {
      return;
    }
    uploadedFiles = [...uploadedFiles, ...csvFiles];
    statusText =
      uploadedFiles.length === 1
        ? `Attached ${uploadedFiles[0].name}. It will import when you send.`
        : `Attached ${uploadedFiles.length} CSV files. They will import when you send.`;
    input.value = "";
  }

  function removeUploadedFile(filename: string) {
    uploadedFiles = removeUploadedFileByName(uploadedFiles, filename);
  }

  async function runCSVImport(
    worldlineId: string,
    file: File,
  ): Promise<Awaited<ReturnType<typeof importCSV>>> {
    try {
      const result = await importCSV(worldlineId, file);
      removeUploadedFile(file.name);

      if (activeWorldlineId === worldlineId) {
        selectedContextTables = [...new Set([...selectedContextTables, result.table_name])];
      }

      statusText = `Imported ${result.row_count} rows from ${file.name} into ${result.table_name}`;
      return result;
    } catch (error) {
      const detail = error instanceof Error ? error.message : "Import failed";
      statusText = `Import failed: ${detail}`;
      throw error instanceof Error ? error : new Error(detail);
    }
  }

  async function importUploadedFilesBeforeSend(worldlineId: string): Promise<void> {
    if (uploadedFiles.length === 0) {
      return;
    }

    const filesToImport = [...uploadedFiles];
    statusText =
      filesToImport.length === 1
        ? `Importing ${filesToImport[0].name}...`
        : `Importing ${filesToImport.length} files...`;

    for (const file of filesToImport) {
      await runCSVImport(worldlineId, file);
    }

    worldlineTables = await fetchWorldlineTables(worldlineId);
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
          on:click={() => {
            showOutputTypeMenu = !showOutputTypeMenu;
            showConnectorsMenu = false;
            showSettingsMenu = false;
            showDataContextMenu = false;
          }}
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
        {#if showOutputTypeMenu}
          <div class="context-menu">
            <div class="context-menu-title">Output Type</div>
            <button type="button" class="context-option" on:click={() => { outputType = "none"; showOutputTypeMenu = false; }}>
              {outputType === "none" ? "✓ " : ""}No strict format
            </button>
            <button type="button" class="context-option" on:click={() => { outputType = "report"; showOutputTypeMenu = false; }}>
              {outputType === "report" ? "✓ " : ""}Report
            </button>
            <button type="button" class="context-option" on:click={() => { outputType = "dashboard"; showOutputTypeMenu = false; }}>
              {outputType === "dashboard" ? "✓ " : ""}Dashboard
            </button>
          </div>
        {/if}
      </div>

      <div class="context-dropdown">
        <button
          type="button"
          class="context-btn"
          on:click={() => {
            showConnectorsMenu = !showConnectorsMenu;
            showOutputTypeMenu = false;
            showSettingsMenu = false;
            showDataContextMenu = false;
          }}
        >
          <Database size={14} />
          <span>{selectedConnectorIds.length > 0 ? `${selectedConnectorIds.length} connectors` : "Connectors"}</span>
          <ChevronDown size={13} />
        </button>
        {#if showConnectorsMenu}
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
          on:click={() => {
            showSettingsMenu = !showSettingsMenu;
            showOutputTypeMenu = false;
            showConnectorsMenu = false;
            showDataContextMenu = false;
          }}
        >
          <Wrench size={14} />
          <span>Settings</span>
          <ChevronDown size={13} />
        </button>
        {#if showSettingsMenu}
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
          on:click={() => {
            showDataContextMenu = !showDataContextMenu;
            showOutputTypeMenu = false;
            showConnectorsMenu = false;
            showSettingsMenu = false;
          }}
        >
          <Plus size={14} />
          <span>{selectedContextTables.length > 0 ? `${selectedContextTables.length} table(s)` : "Attach Context"}</span>
          <ChevronDown size={13} />
        </button>
        {#if showDataContextMenu}
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
    grid-template-columns: minmax(0, 1fr) 420px;
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
    padding: 8px var(--space-3);
    background: var(--surface-1);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-sm);
    color: var(--text-secondary);
    font-size: 14px;
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
    min-width: 160px;
    background: var(--surface-1);
    border: 1px solid var(--border-medium);
    border-radius: var(--radius-sm);
    box-shadow: var(--shadow-lg);
    z-index: 100;
    overflow: hidden;
  }

  .provider-option {
    display: block;
    width: 100%;
    padding: var(--space-3) var(--space-3);
    background: transparent;
    border: none;
    color: var(--text-secondary);
    font-size: 14px;
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
    padding: 8px var(--space-3);
    background: var(--surface-1);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-sm);
    color: var(--text-primary);
    font-size: 14px;
    font-family: var(--font-mono);
    min-width: 180px;
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
    font-size: 13px;
    font-family: var(--font-mono);
    color: var(--text-dim);
    padding: 5px var(--space-3);
    background: var(--surface-0);
    border-radius: var(--radius-sm);
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
    padding: 5px var(--space-2);
    border: 1px dashed var(--border-soft);
    border-radius: var(--radius-sm);
    background: var(--surface-0);
  }

  .queue-chip {
    margin-left: var(--space-2);
    padding: 2px 6px;
    border-radius: var(--radius-sm);
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
    border-radius: var(--radius-sm);
    color: var(--text-secondary);
    font-size: 14px;
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
    font-size: 11px;
    font-weight: 600;
    font-family: var(--font-mono);
    text-transform: uppercase;
    border-radius: var(--radius-sm);
  }

  .feed {
    overflow-y: auto;
    padding: var(--space-5) var(--space-6);
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
    gap: var(--space-3);
    padding: var(--space-8);
    text-align: center;
    color: var(--text-dim);
    animation: messageFadeIn 0.4s cubic-bezier(0.4, 0, 0.2, 1) forwards;
  }

  @keyframes messageFadeIn {
    from {
      opacity: 0;
    }
    to {
      opacity: 1;
    }
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
    margin-bottom: var(--space-6);
    animation: fadeSlideUp 0.5s cubic-bezier(0.4, 0, 0.2, 1) 0.1s both;
  }

  .welcome-icon {
    color: var(--accent-green);
    opacity: 0.6;
    margin-bottom: var(--space-4);
  }

  .welcome-title {
    margin: 0 0 var(--space-2) 0;
    font-family: var(--font-heading);
    font-size: 24px;
    font-weight: 500;
    color: var(--text-primary);
    letter-spacing: -0.02em;
  }

  .welcome-subtitle {
    margin: 0;
    font-size: 14px;
    color: var(--text-dim);
  }

  .context-toolbar {
    max-width: 900px;
    margin: 0 auto var(--space-2);
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
    padding: 8px var(--space-3);
    background: var(--surface-1);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-sm);
    color: var(--text-secondary);
    font-size: 14px;
    cursor: pointer;
    transition: all 0.15s cubic-bezier(0.4, 0, 0.2, 1);
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
    bottom: calc(100% + 6px);
    left: 0;
    min-width: 220px;
    background: var(--surface-0);
    border: 1px solid var(--border-medium);
    border-radius: var(--radius-sm);
    box-shadow: var(--shadow-lg);
    animation: panelSlideUp 0.2s cubic-bezier(0.4, 0, 0.2, 1) forwards;
    transform-origin: bottom center;
    z-index: 200;
    padding: var(--space-2);
    display: flex;
    flex-direction: column;
    gap: 2px;
    max-height: 280px;
    overflow-y: auto;
    max-width: min(320px, calc(100vw - 2 * var(--space-4)));
  }

  @keyframes panelSlideUp {
    from {
      opacity: 0;
      transform: translateY(8px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
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
    border-radius: var(--radius-sm);
    text-align: left;
    padding: 6px var(--space-2);
    color: var(--text-secondary);
    font-size: 13px;
    cursor: pointer;
    font-family: var(--font-body);
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
    padding: 6px var(--space-2);
    border-radius: var(--radius-sm);
    color: var(--text-secondary);
    font-size: 13px;
  }

  .toggle-row:hover {
    background: var(--surface-hover);
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

  textarea.with-attachments {
    padding-top: calc(var(--space-3) + 28px);
  }

  .send-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 40px;
    height: 40px;
    background: var(--accent-green);
    border: none;
    border-radius: var(--radius-sm);
    color: #111;
    cursor: pointer;
    transition: all 0.15s cubic-bezier(0.4, 0, 0.2, 1);
    flex-shrink: 0;
  }

  .send-btn:hover:not(:disabled) {
    opacity: 0.9;
    transform: translateY(-1px);
    box-shadow: 0 4px 12px rgba(62, 207, 142, 0.3);
  }

  .send-btn:active:not(:disabled) {
    transform: translateY(0);
    box-shadow: 0 2px 6px rgba(62, 207, 142, 0.2);
  }

  .send-btn:disabled {
    opacity: 0.3;
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
    padding: 4px 8px;
    background: var(--surface-1);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
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
    animation: messageSlideIn 0.3s cubic-bezier(0.4, 0, 0.2, 1) forwards;
  }

  .thinking-indicator.background {
    color: var(--text-secondary);
    background: color-mix(in srgb, var(--surface-1) 72%, transparent);
    border-top: 1px solid var(--border-soft);
    border-bottom: 1px solid var(--border-soft);
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
    gap: 3px;
  }

  .thinking-dots.muted .dot {
    background: var(--text-muted);
    opacity: 0.35;
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
