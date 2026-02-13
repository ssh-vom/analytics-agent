<script lang="ts">
  import { createEventDispatcher } from "svelte";
  import ToolCellHeader from "$lib/components/ToolCellHeader.svelte";
  import type { TimelineEvent } from "$lib/types";

  export let callEvent: TimelineEvent | null = null;
  export let resultEvent: TimelineEvent | null = null;
  export let onBranch: () => void = () => {};
  const dispatch = createEventDispatcher<{ openworldline: { worldlineId: string } }>();

  type LifecycleStatus = "queued" | "running" | "retrying" | "failed" | "completed";

  type TaskRow = {
    task_index: number;
    task_label: string;
    status: string;
    child_worldline_id: string;
    result_worldline_id: string;
    assistant_preview: string;
    error: string;
    retry_count: number;
    failure_code: string;
    recovered: boolean;
    terminal_reason: string;
  };

  function asTaskRows(payload: Record<string, unknown> | null): TaskRow[] {
    if (!payload) return [];
    const raw = payload.tasks;
    if (!Array.isArray(raw)) return [];
    return raw
      .filter((entry): entry is Record<string, unknown> => !!entry && typeof entry === "object")
      .map((entry) => ({
        task_index: typeof entry.task_index === "number" ? entry.task_index : 0,
        task_label: typeof entry.task_label === "string" ? entry.task_label : "",
        status: typeof entry.status === "string" ? entry.status : "unknown",
        child_worldline_id:
          typeof entry.child_worldline_id === "string" ? entry.child_worldline_id : "",
        result_worldline_id:
          typeof entry.result_worldline_id === "string" ? entry.result_worldline_id : "",
        assistant_preview:
          typeof entry.assistant_preview === "string" ? entry.assistant_preview : "",
        error: typeof entry.error === "string" ? entry.error : "",
        retry_count:
          typeof entry.retry_count === "number" && Number.isFinite(entry.retry_count)
            ? Math.max(0, Math.floor(entry.retry_count))
            : 0,
        failure_code:
          typeof entry.failure_code === "string" ? entry.failure_code : "",
        recovered: entry.recovered === true,
        terminal_reason:
          typeof entry.terminal_reason === "string" ? entry.terminal_reason : "",
      }));
  }

  function readFailureSummaryCount(
    payload: Record<string, unknown> | null,
    key: string,
  ): number {
    if (!payload) return 0;
    const summary = payload.failure_summary;
    if (!summary || typeof summary !== "object") {
      return 0;
    }
    const value = (summary as Record<string, unknown>)[key];
    if (typeof value !== "number" || !Number.isFinite(value)) {
      return 0;
    }
    return Math.max(0, Math.floor(value));
  }

  function taskLifecycleStatus(task: TaskRow): LifecycleStatus {
    if (task.status === "completed") {
      return "completed";
    }
    if (task.status === "running") {
      return task.retry_count > 0 ? "retrying" : "running";
    }
    if (task.status === "queued") {
      return "queued";
    }
    return "failed";
  }

  function taskOutcomeSummary(task: TaskRow): string {
    const parts: string[] = [];
    if (task.retry_count > 0) {
      parts.push(`retries=${task.retry_count}`);
    }
    if (task.recovered) {
      parts.push("recovered=true");
    }
    if (task.terminal_reason) {
      parts.push(`terminal=${task.terminal_reason}`);
    }
    if (task.failure_code) {
      parts.push(`failure=${task.failure_code}`);
    }
    return parts.join(" | ");
  }

  function plannedTasksFromCall(
    payload: Record<string, unknown> | null,
  ): Array<{
    task_label: string;
    task_message: string;
    child_worldline_id: string;
    ordering_key: string;
  }> {
    if (!payload) return [];
    const accepted = payload.accepted_tasks;
    if (Array.isArray(accepted)) {
      return accepted
        .filter(
          (entry): entry is Record<string, unknown> =>
            !!entry && typeof entry === "object",
        )
        .map((entry, idx) => ({
          task_label:
            typeof entry.task_label === "string" && entry.task_label.trim()
              ? entry.task_label
              : `task-${idx + 1}`,
          task_message: "",
          child_worldline_id:
            typeof entry.child_worldline_id === "string"
              ? entry.child_worldline_id
              : "",
          ordering_key:
            typeof entry.ordering_key === "string" ? entry.ordering_key : "",
        }))
        .sort((left, right) => left.ordering_key.localeCompare(right.ordering_key));
    }
    const raw = payload.tasks;
    if (!Array.isArray(raw)) return [];
    return raw
      .filter(
        (entry): entry is Record<string, unknown> =>
          !!entry && typeof entry === "object",
      )
      .map((entry, idx) => ({
        task_label:
          typeof entry.task_label === "string" && entry.task_label.trim()
            ? entry.task_label
            : typeof entry.label === "string" && entry.label.trim()
              ? entry.label
            : `task-${idx + 1}`,
        task_message:
          typeof entry.message === "string" ? entry.message : "",
        child_worldline_id:
          typeof entry.child_worldline_id === "string"
            ? entry.child_worldline_id
            : "",
        ordering_key:
          typeof entry.ordering_key === "string" && entry.ordering_key
            ? entry.ordering_key
            : typeof entry.task_index === "number"
              ? `task:${entry.task_index}`
              : `task:${idx}`,
      }))
      .sort((left, right) => left.ordering_key.localeCompare(right.ordering_key));
  }

  $: callPayload = (callEvent?.payload ?? null) as Record<string, unknown> | null;
  $: resultPayload = (resultEvent?.payload ?? null) as Record<string, unknown> | null;
  $: tasks = asTaskRows(resultPayload);
  $: plannedTasks = plannedTasksFromCall(callPayload);

  $: requestedTaskCount =
    typeof resultPayload?.requested_task_count === "number"
      ? resultPayload.requested_task_count
      : plannedTasks.length > 0
        ? plannedTasks.length
        : tasks.length;
  $: acceptedTaskCount =
    typeof resultPayload?.accepted_task_count === "number"
      ? resultPayload.accepted_task_count
      : typeof resultPayload?.task_count === "number"
        ? resultPayload.task_count
        : plannedTasks.length > 0
          ? plannedTasks.length
          : tasks.length;
  $: truncatedTaskCount =
    typeof resultPayload?.truncated_task_count === "number"
      ? resultPayload.truncated_task_count
      : Math.max(0, requestedTaskCount - acceptedTaskCount);
  $: taskCount =
    typeof resultPayload?.task_count === "number" ? resultPayload.task_count : acceptedTaskCount;
  $: completedCount =
    typeof resultPayload?.completed_count === "number" ? resultPayload.completed_count : 0;
  $: failedCount = typeof resultPayload?.failed_count === "number" ? resultPayload.failed_count : 0;
  $: timedOutCount =
    typeof resultPayload?.timed_out_count === "number" ? resultPayload.timed_out_count : 0;
  $: partialFailure =
    typeof resultPayload?.partial_failure === "boolean"
      ? resultPayload.partial_failure
      : failedCount > 0 || timedOutCount > 0;
  $: maxSubagents =
    typeof resultPayload?.max_subagents === "number" ? resultPayload.max_subagents : null;
  $: maxParallelSubagents =
    typeof resultPayload?.max_parallel_subagents === "number"
      ? resultPayload.max_parallel_subagents
      : null;
  $: fanoutGroup =
    typeof resultPayload?.fanout_group_id === "string" ? resultPayload.fanout_group_id : "";
  $: fromEventId =
    typeof callPayload?.from_event_id === "string" ? callPayload.from_event_id : "";
  $: goal = typeof callPayload?.goal === "string" ? callPayload.goal : "";
  $: isStreamingResult = resultPayload?._streaming === true;
  $: hasResult = Boolean(resultEvent);
  $: hasTerminalResult = hasResult && !isStreamingResult;
  $: phase = typeof resultPayload?.phase === "string" ? resultPayload.phase : "";
  $: retryCount =
    typeof resultPayload?.retry_count === "number" && Number.isFinite(resultPayload.retry_count)
      ? Math.max(0, Math.floor(resultPayload.retry_count))
      : 0;
  $: failureCode = typeof resultPayload?.failure_code === "string" ? resultPayload.failure_code : "";
  $: queuedCount =
    typeof resultPayload?.queued_count === "number" ? resultPayload.queued_count : 0;
  $: runningCount =
    typeof resultPayload?.running_count === "number" ? resultPayload.running_count : 0;
  $: loopLimitFailureCount =
    typeof resultPayload?.loop_limit_failure_count === "number"
      ? resultPayload.loop_limit_failure_count
      : readFailureSummaryCount(resultPayload, "subagent_loop_limit");
  $: retriedTaskCount =
    typeof resultPayload?.retried_task_count === "number"
      ? resultPayload.retried_task_count
      : tasks.filter((task) => task.retry_count > 0).length;
  $: recoveredTaskCount =
    typeof resultPayload?.recovered_task_count === "number"
      ? resultPayload.recovered_task_count
      : tasks.filter((task) => task.recovered).length;
  $: resultError =
    typeof resultPayload?.error === "string" ? resultPayload.error.trim() : "";
  $: resultErrorType =
    typeof resultPayload?.error_type === "string" ? resultPayload.error_type.trim() : "";
  $: hasRetryingTask = tasks.some((task) => taskLifecycleStatus(task) === "retrying");
  $: isRetrying = phase === "retrying" || retryCount > 0 || hasRetryingTask;
  $: isQueued =
    phase === "queued" ||
    (queuedCount > 0 &&
      runningCount === 0 &&
      completedCount === 0 &&
      failedCount === 0 &&
      timedOutCount === 0);
  $: lifecycleStatus = !hasResult
    ? "queued"
    : isStreamingResult
      ? isRetrying
        ? "retrying"
        : isQueued
          ? "queued"
          : "running"
      : resultErrorType === "CancelledError"
        ? "failed"
        : resultError
          ? "failed"
          : timedOutCount > 0 || failureCode
            ? "failed"
            : partialFailure
              ? "failed"
              : "completed";
  $: statusLabel =
    lifecycleStatus === "failed" && loopLimitFailureCount > 0
      ? "failed (loop-limit)"
      : lifecycleStatus;

  function openWorldline(worldlineId: string): void {
    if (!worldlineId) {
      return;
    }
    dispatch("openworldline", { worldlineId });
  }
</script>

<article class="subagents-cell">
  <ToolCellHeader
    label="Subagent Fan-out"
    timestamp={callEvent?.created_at ?? resultEvent?.created_at ?? null}
    onBranch={onBranch}
  />

  <div class="summary">
    <span class="chip">tasks {taskCount}</span>
    <span class="chip" data-status={lifecycleStatus}>{statusLabel}</span>
    {#if requestedTaskCount !== acceptedTaskCount}
      <span class="chip">accepted {acceptedTaskCount}/{requestedTaskCount}</span>
    {/if}
    {#if truncatedTaskCount > 0}
      <span class="chip warn">truncated {truncatedTaskCount}</span>
    {/if}
    {#if completedCount > 0}
      <span class="chip success">completed {completedCount}</span>
    {/if}
    {#if failedCount > 0}
      <span class="chip error">failed {failedCount}</span>
    {/if}
    {#if timedOutCount > 0}
      <span class="chip warn">timeout {timedOutCount}</span>
    {/if}
    {#if retriedTaskCount > 0}
      <span class="chip warn">retried {retriedTaskCount}</span>
    {/if}
    {#if recoveredTaskCount > 0}
      <span class="chip success">recovered {recoveredTaskCount}</span>
    {/if}
    {#if loopLimitFailureCount > 0}
      <span class="chip error">loop-limit {loopLimitFailureCount}</span>
    {/if}
  </div>

  {#if fanoutGroup}
    <div class="meta-line">fanout group: <code>{fanoutGroup}</code></div>
  {/if}
  {#if fromEventId}
    <div class="meta-line">forked from event: <code>{fromEventId}</code></div>
  {/if}
  {#if maxSubagents || maxParallelSubagents}
    <div class="meta-line">
      limits:
      {#if maxSubagents}
        max_subagents=<code>{maxSubagents}</code>
      {/if}
      {#if maxParallelSubagents}
        max_parallel=<code>{maxParallelSubagents}</code>
      {/if}
    </div>
  {/if}
  {#if goal}
    <div class="meta-line">goal: {goal}</div>
  {/if}

  {#if tasks.length > 0}
    <div class="task-list">
      {#each tasks as task}
        {@const taskStatus = taskLifecycleStatus(task)}
        {@const outcomeSummary = taskOutcomeSummary(task)}
        <div class="task-row">
          <div class="task-head">
            <span class="task-label">{task.task_label || "task"}</span>
            <span class="task-status" data-status={taskStatus}>{taskStatus}</span>
          </div>
          {#if task.child_worldline_id}
            <div class="task-meta">
              child worldline:
              <button
                type="button"
                class="worldline-link"
                on:click={() => openWorldline(task.child_worldline_id)}
              >
                <code>{task.child_worldline_id}</code>
              </button>
            </div>
          {/if}
          {#if task.result_worldline_id}
            <div class="task-meta">
              result worldline:
              <button
                type="button"
                class="worldline-link"
                on:click={() => openWorldline(task.result_worldline_id)}
              >
                <code>{task.result_worldline_id}</code>
              </button>
            </div>
          {/if}
          {#if outcomeSummary}
            <div class="task-meta">
              <code>{outcomeSummary}</code>
            </div>
          {/if}
          {#if task.assistant_preview}
            <div class="task-preview">{task.assistant_preview}</div>
          {/if}
          {#if task.error}
            <div class="task-error">{task.error}</div>
          {/if}
        </div>
      {/each}
    </div>
  {:else if plannedTasks.length > 0}
    <div class="task-list">
      {#each plannedTasks as task}
        <div class="task-row">
          <div class="task-head">
            <span class="task-label">{task.task_label}</span>
            <span class="task-status" data-status="queued">queued</span>
          </div>
          {#if task.child_worldline_id}
            <div class="task-meta">
              child worldline:
              <button
                type="button"
                class="worldline-link"
                on:click={() => openWorldline(task.child_worldline_id)}
              >
                <code>{task.child_worldline_id}</code>
              </button>
            </div>
          {/if}
          {#if task.task_message}
            <div class="task-preview">{task.task_message}</div>
          {/if}
        </div>
      {/each}
    </div>
  {:else if resultEvent}
    <pre>{JSON.stringify(resultPayload, null, 2)}</pre>
  {:else}
    <div class="meta-line">Spawning subagent worldlines...</div>
  {/if}

  {#if hasTerminalResult && resultError}
    <div class="task-error">{resultError}</div>
  {/if}
</article>

<style>
  .subagents-cell {
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-lg);
    background: var(--surface-0);
    padding: var(--space-3);
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }
  .summary {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-2);
  }
  .chip {
    border: 1px solid var(--border-soft);
    color: var(--text-secondary);
    border-radius: var(--radius-full);
    padding: 2px 8px;
    font-size: 11px;
    font-family: var(--font-mono);
  }
  .chip.success {
    border-color: var(--accent-green-muted);
    color: var(--accent-green);
  }
  .chip.error {
    border-color: var(--danger-muted);
    color: var(--danger);
  }
  .chip.warn {
    border-color: var(--warning-muted);
    color: var(--warning);
  }
  .chip[data-status="queued"] {
    border-color: var(--warning-muted);
    color: var(--warning);
  }
  .chip[data-status="running"] {
    border-color: var(--border-medium);
    color: var(--text-muted);
  }
  .chip[data-status="retrying"] {
    border-color: var(--warning-muted);
    color: var(--warning);
  }
  .chip[data-status="completed"] {
    border-color: var(--accent-green-muted);
    color: var(--accent-green);
  }
  .chip[data-status="failed"] {
    border-color: var(--danger-muted);
    color: var(--danger);
  }
  .meta-line {
    font-size: 11px;
    color: var(--text-dim);
    font-family: var(--font-mono);
  }
  .task-list {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }
  .task-row {
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    padding: var(--space-2);
    background: var(--bg-0);
  }
  .task-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: var(--space-2);
  }
  .task-label {
    font-size: 12px;
    color: var(--text-secondary);
    font-family: var(--font-mono);
  }
  .task-status {
    font-size: 11px;
    padding: 2px 8px;
    border-radius: var(--radius-full);
    border: 1px solid var(--border-soft);
    color: var(--text-dim);
    text-transform: lowercase;
  }
  .task-status[data-status="queued"] {
    color: var(--warning);
    border-color: var(--warning-muted);
  }
  .task-status[data-status="running"] {
    color: var(--text-secondary);
    border-color: var(--border-medium);
  }
  .task-status[data-status="retrying"] {
    color: var(--warning);
    border-color: var(--warning-muted);
  }
  .task-status[data-status="completed"] {
    color: var(--accent-green);
    border-color: var(--accent-green-muted);
  }
  .task-status[data-status="failed"] {
    color: var(--danger);
    border-color: var(--danger-muted);
  }
  .task-meta {
    margin-top: 4px;
    font-size: 11px;
    color: var(--text-dim);
    font-family: var(--font-mono);
  }
  .worldline-link {
    background: transparent;
    border: none;
    padding: 0;
    margin-left: 4px;
    cursor: pointer;
    color: var(--text-secondary);
    text-decoration: underline;
    text-decoration-color: var(--border-medium);
    text-underline-offset: 2px;
    font: inherit;
  }
  .worldline-link:hover {
    color: var(--accent-orange);
  }
  .task-preview {
    margin-top: 6px;
    font-size: 12px;
    color: var(--text-muted);
    white-space: pre-wrap;
  }
  .task-error {
    margin-top: 6px;
    color: var(--danger);
    font-size: 12px;
    white-space: pre-wrap;
  }
  pre {
    margin: 0;
    font-size: 12px;
    color: var(--text-muted);
    background: var(--bg-0);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    padding: var(--space-2);
    overflow: auto;
  }
</style>
