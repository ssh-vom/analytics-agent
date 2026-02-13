import type { StreamDeltaPayload, TimelineEvent } from "$lib/types";
import { streamChatTurn } from "$lib/api/client";
import {
  stateTransitionFromDelta,
  type RuntimeStateTransition,
} from "$lib/chat/stateTrace";
import { statusFromDelta, statusFromStreamEvent } from "$lib/chat/streamStatus";
import {
  applyDelta,
  clearFromEvent,
  createStreamingState,
  type StreamingState,
} from "$lib/streaming";
import { replaceOptimisticWithReal } from "$lib/chat/optimisticState";
import type { VisibleWorldlineHint } from "$lib/chat/worldlineState";

export type StreamCallbacks = {
  onEvent: (frame: { worldline_id: string; event: TimelineEvent }) => void;
  onDelta: (frame: { worldline_id: string; delta: StreamDeltaPayload }) => void;
  onDone: (frame: { worldline_id: string }) => void;
  onError: (error: string) => void;
};

export type SendPromptOptions = {
  worldlineId: string;
  message: string;
  provider: string;
  model?: string;
  maxIterations?: number;
};

export type SendPromptContext = {
  optimisticId: string;
  getActiveWorldlineId: () => string;
  getEvents: (worldlineId: string) => TimelineEvent[];
  setWorldlineEvents: (worldlineId: string, events: TimelineEvent[]) => void;
  appendEvent: (worldlineId: string, event: TimelineEvent) => void;
  rollbackOptimisticMessage: (worldlineId: string, optimisticId: string) => void;
  setWorldlineSending: (worldlineId: string, sending: boolean) => void;
  resetStreamingDrafts: (worldlineId?: string) => void;
  refreshWorldlines: () => Promise<void>;
  ensureWorldlineVisible: (
    worldlineId: string,
    hint?: VisibleWorldlineHint,
  ) => void;
  onStatusChange: (status: string) => void;
  onScroll: () => void;
  onTurnCompleted: () => void;
  setStreamingState: (worldlineId: string, state: StreamingState) => void;
  getStreamingState: (worldlineId: string) => StreamingState | undefined;
  appendRuntimeStateTransition: (
    worldlineId: string,
    transition: RuntimeStateTransition
  ) => void;
};

export async function sendPromptWithStreaming(
  options: SendPromptOptions,
  context: SendPromptContext
): Promise<void> {
  const {
    worldlineId,
    message,
    provider,
    model,
    maxIterations = 10,
  } = options;

  const {
    optimisticId,
    getActiveWorldlineId,
    getEvents,
    setWorldlineEvents,
    appendEvent,
    rollbackOptimisticMessage,
    setWorldlineSending,
    resetStreamingDrafts,
    refreshWorldlines,
    ensureWorldlineVisible,
    onStatusChange,
    onScroll,
    onTurnCompleted,
    setStreamingState,
    getStreamingState,
    appendRuntimeStateTransition,
  } = context;
  const fanoutOrderBaseByGroup = new Map<string, number>();

  const isActiveWorldline = (candidateWorldlineId: string): boolean =>
    candidateWorldlineId === getActiveWorldlineId();

  const ensureDeltaWorldlinesVisible = (
    ownerWorldlineId: string,
    delta: StreamDeltaPayload,
  ): void => {
    if (delta.type !== "subagent_progress") {
      return;
    }
    const taskIndex =
      typeof delta.task_index === "number" && Number.isFinite(delta.task_index)
        ? Math.max(0, Math.floor(delta.task_index))
        : null;
    const suggestedName =
      typeof delta.task_label === "string" && delta.task_label.trim()
        ? delta.task_label.trim()
        : taskIndex !== null
          ? `subagent-${taskIndex + 1}`
          : null;
    const groupKey =
      typeof delta.fanout_group_id === "string" && delta.fanout_group_id
        ? delta.fanout_group_id
        : `${ownerWorldlineId}:${delta.parent_tool_call_id ?? "subagents"}`;
    let groupBase = fanoutOrderBaseByGroup.get(groupKey);
    if (groupBase === undefined) {
      groupBase = Date.now();
      fanoutOrderBaseByGroup.set(groupKey, groupBase);
    }
    const createdAtHint =
      taskIndex !== null ? new Date(groupBase + taskIndex).toISOString() : null;
    const parentWorldlineId =
      typeof delta.source_worldline_id === "string" && delta.source_worldline_id
        ? delta.source_worldline_id
        : ownerWorldlineId;
    const visibilityHint: VisibleWorldlineHint = {
      parentWorldlineId,
      suggestedName,
      createdAt: createdAtHint,
    };
    if (typeof delta.child_worldline_id === "string" && delta.child_worldline_id) {
      ensureWorldlineVisible(delta.child_worldline_id, visibilityHint);
    }
    if (typeof delta.result_worldline_id === "string" && delta.result_worldline_id) {
      ensureWorldlineVisible(delta.result_worldline_id, visibilityHint);
    }
  };

  setWorldlineSending(worldlineId, true);

  try {
    await streamChatTurn({
      worldlineId,
      message,
      provider,
      model,
      maxIterations,
      onEvent: (frame) => {
        const frameWorldlineId = frame.worldline_id;
        ensureWorldlineVisible(frameWorldlineId);
        const frameStreamingState =
          getStreamingState(frameWorldlineId) ?? createStreamingState();
        setStreamingState(
          frameWorldlineId,
          clearFromEvent(frameStreamingState, frame.event)
        );

        if (frame.event.type === "user_message") {
          const existing = getEvents(frameWorldlineId);
          const { events: updated } = replaceOptimisticWithReal(
            existing,
            optimisticId,
            frame.event
          );
          setWorldlineEvents(frameWorldlineId, updated);
        } else {
          appendEvent(frameWorldlineId, frame.event);
        }

        if (isActiveWorldline(frameWorldlineId)) {
          onScroll();
          onStatusChange(statusFromStreamEvent(frame.event.type));
        }
      },
      onDelta: (frame) => {
        const frameWorldlineId = frame.worldline_id;
        ensureWorldlineVisible(frameWorldlineId);
        ensureDeltaWorldlinesVisible(frameWorldlineId, frame.delta);

        const frameStreamingState =
          getStreamingState(frameWorldlineId) ?? createStreamingState();
        setStreamingState(frameWorldlineId, applyDelta(frameStreamingState, frame.delta));

        const stateTransition = stateTransitionFromDelta(frame.delta);
        if (stateTransition) {
          appendRuntimeStateTransition(frameWorldlineId, stateTransition);
          if (isActiveWorldline(frameWorldlineId)) {
            onStatusChange(`State: ${stateTransition.to_state.replace(/_/g, " ")}`);
            onScroll();
          }
          return;
        }

        if (isActiveWorldline(frameWorldlineId)) {
          const status = statusFromDelta(frame.delta);
          if (status) {
            onStatusChange(status);
          }
          onScroll();
        }
      },
      onDone: async (frame) => {
        const completedWorldlineId = frame.worldline_id;
        resetStreamingDrafts(completedWorldlineId);
        // Keep done handling lightweight to avoid clobbering newer UI state.
        void refreshWorldlines().catch(() => undefined);

        if (isActiveWorldline(completedWorldlineId)) {
          onStatusChange("Done");
          onScroll();
        }
        onTurnCompleted();
      },
      onError: (error) => {
        resetStreamingDrafts(worldlineId);
        rollbackOptimisticMessage(worldlineId, optimisticId);
        onStatusChange(`Error: ${error}`);
      },
    });
  } catch (error) {
    resetStreamingDrafts(worldlineId);
    rollbackOptimisticMessage(worldlineId, optimisticId);
    const message = error instanceof Error ? error.message : "Request failed";
    onStatusChange(message);
  } finally {
    setWorldlineSending(worldlineId, false);
  }
}
