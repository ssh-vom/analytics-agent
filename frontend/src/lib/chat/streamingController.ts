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
  loadWorldline: (worldlineId: string) => Promise<void>;
  ensureWorldlineVisible: (worldlineId: string) => void;
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
    loadWorldline,
    ensureWorldlineVisible,
    onStatusChange,
    onScroll,
    onTurnCompleted,
    setStreamingState,
    getStreamingState,
    appendRuntimeStateTransition,
  } = context;

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

        if (frameWorldlineId === getActiveWorldlineId()) {
          onScroll();
          onStatusChange(statusFromStreamEvent(frame.event.type));
        }
      },
      onDelta: (frame) => {
        const frameWorldlineId = frame.worldline_id;
        ensureWorldlineVisible(frameWorldlineId);

        const frameStreamingState =
          getStreamingState(frameWorldlineId) ?? createStreamingState();
        setStreamingState(frameWorldlineId, applyDelta(frameStreamingState, frame.delta));

        const stateTransition = stateTransitionFromDelta(frame.delta);
        if (stateTransition) {
          appendRuntimeStateTransition(frameWorldlineId, stateTransition);
          if (frameWorldlineId === getActiveWorldlineId()) {
            onStatusChange(`State: ${stateTransition.to_state.replace(/_/g, " ")}`);
            onScroll();
          }
          return;
        }

        if (frameWorldlineId === getActiveWorldlineId()) {
          const status = statusFromDelta(frame.delta);
          if (status) {
            onStatusChange(status);
          }
          onScroll();
        }
      },
      onDone: async (frame) => {
        resetStreamingDrafts(frame.worldline_id);
        await refreshWorldlines();
        if (frame.worldline_id) {
          await loadWorldline(frame.worldline_id);
        }

        if (frame.worldline_id === getActiveWorldlineId()) {
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
