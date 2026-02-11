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
    streamChatTurn,
    importCSV,
    fetchWorldlineTables,
  } from "$lib/api/client";
  import {
    createOptimisticUserMessage,
    insertOptimisticEvent,
    removeOptimisticEvent,
    replaceOptimisticWithReal,
  } from "$lib/chat/optimisticState";
  import { getStoredJson } from "$lib/storage";
  import type { Thread, TimelineEvent, WorldlineItem } from "$lib/types";
  
  // Icons
  import { Database } from "lucide-svelte";
  import { Send } from "lucide-svelte";
  import { ChevronDown } from "lucide-svelte";
  import { Sparkles } from "lucide-svelte";
  import { Plus } from "lucide-svelte";
  import { Wrench } from "lucide-svelte";
  import { Upload } from "lucide-svelte";
  import { FileSpreadsheet } from "lucide-svelte";
  import { X } from "lucide-svelte";

  type Provider = "gemini" | "openai" | "openrouter";
  type OutputType = "report" | "dashboard";

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

  // CSV Import state
  let uploadedFiles: File[] = [];
  let importingFile: string | null = null;
  let importError: string | null = null;
  let importSuccess: { filename: string; table: string; rows: number } | null = null;
  let showImportPanel = false;
  let worldlineTables: Awaited<ReturnType<typeof fetchWorldlineTables>> | null = null;
  let outputType: OutputType = "report";
  let showOutputTypeMenu = false;
  let showConnectorsMenu = false;
  let showSettingsMenu = false;
  let showDataContextMenu = false;
  let availableConnectors: Array<{ id: string; name: string; isActive: boolean }> = [];
  let selectedConnectorIds: string[] = [];
  let selectedContextTables: string[] = [];
  let contextSettings = {
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
  $: hasDraftOutput =
    activeStreamingState.text.length > 0 || activeStreamingState.toolCalls.size > 0;
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
    if (document.visibilityState === "visible") {
      void chatJobs.poll();
    }
  }

  function persistPreferredWorldline(worldlineId: string): void {
    if (!worldlineId) {
      return;
    }
    localStorage.setItem("textql_active_worldline", worldlineId);
  }

  function pickActiveJobWorldlineId(
    threadWorldlines: WorldlineItem[],
    targetThreadId: string,
  ): string | null {
    if (threadWorldlines.length === 0) {
      return null;
    }
    const candidateIds = new Set(threadWorldlines.map((line) => line.id));
    const jobs = Object.values($chatJobs.jobsById).filter(
      (job) =>
        job.thread_id === targetThreadId &&
        candidateIds.has(job.worldline_id) &&
        (job.status === "running" || job.status === "queued"),
    );

    if (jobs.length === 0) {
      return null;
    }

    jobs.sort((left, right) => {
      const statusScore = (jobStatus: "running" | "queued") =>
        jobStatus === "running" ? 2 : 1;
      const leftScore = statusScore(left.status as "running" | "queued");
      const rightScore = statusScore(right.status as "running" | "queued");
      if (leftScore !== rightScore) {
        return rightScore - leftScore;
      }

      const leftTime = Date.parse(left.started_at ?? left.created_at ?? "") || 0;
      const rightTime = Date.parse(right.started_at ?? right.created_at ?? "") || 0;
      if (leftTime !== rightTime) {
        return rightTime - leftTime;
      }
      return right.id.localeCompare(left.id);
    });

    return jobs[0]?.worldline_id ?? null;
  }

  onMount(async () => {
    window.addEventListener("textql:open-worldline", handleOpenWorldlineEvent as EventListener);
    document.addEventListener("visibilitychange", handleVisibilityChange);
    await threads.loadThreads();
    loadConnectorsFromStorage();

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
    window.removeEventListener(
      "textql:open-worldline",
      handleOpenWorldlineEvent as EventListener,
    );
    document.removeEventListener("visibilitychange", handleVisibilityChange);
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
    streamingStateByWorldline = {
      ...streamingStateByWorldline,
      [worldlineId]: state,
    };
  }

  function setWorldlineSending(worldlineId: string, isSending: boolean): void {
    if (!worldlineId) {
      return;
    }
    if (isSending) {
      sendingByWorldline = {
        ...sendingByWorldline,
        [worldlineId]: true,
      };
      return;
    }
    if (!(worldlineId in sendingByWorldline)) {
      return;
    }
    const next = { ...sendingByWorldline };
    delete next[worldlineId];
    sendingByWorldline = next;
  }

  function resetStreamingDrafts(worldlineId?: string): void {
    if (!worldlineId) {
      streamingStateByWorldline = {};
      return;
    }
    if (!(worldlineId in streamingStateByWorldline)) {
      return;
    }
    const next = { ...streamingStateByWorldline };
    delete next[worldlineId];
    streamingStateByWorldline = next;
  }

  async function queuePromptAsJob(message: string, worldlineId: string): Promise<void> {
    try {
      const contextualMessage = buildContextualMessage(message);
      const job = await createChatJob({
        worldlineId,
        message: contextualMessage,
        provider,
        model: model.trim() || undefined,
        maxIterations: provider === "gemini" ? 10 : 20,
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

  function loadConnectorsFromStorage(): void {
    const parsed = getStoredJson<
      Array<{ id: string; name: string; isActive: boolean }>
    >(
      "textql_connectors",
      (value): value is Array<{ id: string; name: string; isActive: boolean }> =>
        Array.isArray(value) &&
        value.every(
          (item) =>
            typeof item === "object" &&
            item !== null &&
            typeof (item as { id?: unknown }).id === "string" &&
            typeof (item as { name?: unknown }).name === "string" &&
            typeof (item as { isActive?: unknown }).isActive === "boolean",
        ),
    );

    if (parsed) {
      availableConnectors = parsed;
      selectedConnectorIds = parsed.filter((c) => c.isActive).map((c) => c.id);
      return;
    }

    if (localStorage.getItem("textql_connectors") !== null) {
      availableConnectors = [];
      selectedConnectorIds = [];
    }
  }

  function rollbackOptimisticMessage(worldlineId: string, optimisticId: string | null): void {
    const currentEvents = eventsByWorldline[worldlineId] ?? [];
    setWorldlineEvents(worldlineId, removeOptimisticEvent(currentEvents, optimisticId));
  }

  function closeContextMenus(): void {
    showOutputTypeMenu = false;
    showConnectorsMenu = false;
    showSettingsMenu = false;
    showDataContextMenu = false;
  }

  function toggleConnector(id: string): void {
    if (selectedConnectorIds.includes(id)) {
      selectedConnectorIds = selectedConnectorIds.filter((connectorId) => connectorId !== id);
      return;
    }
    selectedConnectorIds = [...selectedConnectorIds, id];
  }

  function toggleContextTable(name: string): void {
    if (selectedContextTables.includes(name)) {
      selectedContextTables = selectedContextTables.filter((table) => table !== name);
      return;
    }
    selectedContextTables = [...selectedContextTables, name];
  }

  async function refreshWorldlineContextTables(): Promise<void> {
    if (!activeWorldlineId) {
      worldlineTables = null;
      selectedContextTables = [];
      return;
    }
    try {
      const tables = await fetchWorldlineTables(activeWorldlineId);
      worldlineTables = tables;
      selectedContextTables = selectedContextTables.filter((selected) =>
        tables.tables.some((table) => table.name === selected),
      );
      if (selectedContextTables.length === 0 && tables.tables.length > 0) {
        selectedContextTables = [tables.tables[0].name];
      }
    } catch {
      worldlineTables = null;
      selectedContextTables = [];
    }
  }

  function buildContextualMessage(message: string): string {
    const selectedConnectors = availableConnectors
      .filter((connector) => selectedConnectorIds.includes(connector.id))
      .map((connector) => connector.name);
    const contextLines: string[] = [
      `output_type=${outputType}`,
    ];

    if (selectedContextTables.length > 0) {
      contextLines.push(`tables=${selectedContextTables.join(",")}`);
    }
    if (selectedConnectors.length > 0) {
      contextLines.push(`connectors=${selectedConnectors.join(",")}`);
    }

    const enabledSettings = Object.entries(contextSettings)
      .filter(([, enabled]) => enabled)
      .map(([key]) => key);
    if (enabledSettings.length > 0) {
      contextLines.push(`settings=${enabledSettings.join(",")}`);
    }

    if (contextLines.length === 0) {
      return message;
    }

    return `${message}\n\n<context>\n${contextLines.map((line) => `- ${line}`).join("\n")}\n</context>`;
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

      if (threadId) {
        await chatJobs.poll();
      }

      const activeJobWorldlineId = pickActiveJobWorldlineId(worldlines, targetThreadId);

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
      
      statusText = "Creating worldline...";
      
      // Create worldline for this thread
      const worldline = await createWorldline(threadId, "main");
      activeWorldlineId = worldline.worldline_id;
      persistPreferredWorldline(activeWorldlineId);
      
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
    const isCurrentWorldlineSending = Boolean(activeWorldlineId && sendingByWorldline[activeWorldlineId]);
    const hasPendingWorldlineJobs = activeWorldlineQueueDepth > 0;
    if (!message || !activeWorldlineId) {
      if (!activeWorldlineId) {
        statusText = "Error: No active worldline. Please refresh the page.";
      }
      return;
    }

    if (isCurrentWorldlineSending || hasPendingWorldlineJobs) {
      await queuePromptAsJob(message, activeWorldlineId);
      return;
    }

    const requestWorldlineId = activeWorldlineId;
    setWorldlineSending(requestWorldlineId, true);
    prompt = "";
    closeContextMenus();
    statusText = "Agent is thinking...";
    shouldAutoScroll = true;
    resetStreamingDrafts(requestWorldlineId);
    selectedArtifactId = null;

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
        maxIterations: provider === "gemini" ? 10 : 20,
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
          if (activeWorldlineId === frameWorldlineId) {
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
      selectedContextTables = [...new Set([...selectedContextTables, result.table_name])];
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
        {#if activeWorldlineQueueDepth > 0}
          <span class="queue-chip">{activeWorldlineQueueDepth} queued</span>
        {/if}
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
          <span>{outputType === "report" ? "Report" : "Dashboard"}</span>
          <ChevronDown size={13} />
        </button>
        {#if showOutputTypeMenu}
          <div class="context-menu">
            <div class="context-menu-title">Output Type</div>
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
                <button type="button" class="context-option" on:click={() => toggleContextTable(table.name)}>
                  {selectedContextTables.includes(table.name) ? "✓ " : ""}{table.name}
                </button>
              {/each}
            {:else}
              <div class="context-empty">No tables available</div>
            {/if}
          </div>
        {/if}
      </div>
    </div>

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
    flex-shrink: 0;
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
