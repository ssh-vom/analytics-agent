import type { StreamDeltaPayload, TimelineEvent } from "$lib/types";
import { streamChatTurn } from "$lib/api/client";
import type { RuntimeStateTransition } from "$lib/chat/stateTrace";

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
  optimisticEvent: TimelineEvent;
  appendEvent: (worldlineId: string, event: TimelineEvent) => void;
  rollbackOptimisticMessage: (worldlineId: string, optimisticId: string) => void;
  setWorldlineSending: (worldlineId: string, sending: boolean) => void;
  resetStreamingDrafts: (worldlineId?: string) => void;
  refreshWorldlines: () => Promise<void>;
  loadWorldline: (worldlineId: string) => Promise<void>;
  ensureWorldlineVisible: (worldlineId: string) => void;
  activeWorldlineId: string;
  statusCallbacks: {
    onStatusChange: (status: string) => void;
    onStateTransition: (transition: RuntimeStateTransition) => void;
    onScroll: () => void;
  };
  stateMutators: {
    setStreamingState: (worldlineId: string, state: unknown) => void;
    getStreamingState: (worldlineId: string) => unknown;
    appendRuntimeStateTransition: (
      worldlineId: string,
      transition: RuntimeStateTransition
    ) => void;
  };
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
    appendEvent,
    rollbackOptimisticMessage,
    setWorldlineSending,
    resetStreamingDrafts,
    refreshWorldlines,
    loadWorldline,
    ensureWorldlineVisible,
    activeWorldlineId,
    statusCallbacks,
    stateMutators,
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
        ensureWorldlineVisible(frame.worldline_id);
        appendEvent(frame.worldline_id, frame.event);

        if (frame.worldline_id === activeWorldlineId) {
          const status = statusFromEventType(frame.event.type);
          statusCallbacks.onStatusChange(status);
        }
      },
      onDelta: (frame) => {
        ensureWorldlineVisible(frame.worldline_id);

        const frameStreamingState =
          (stateMutators.getStreamingState(frame.worldline_id) as Record<
            string,
            unknown
          >) ?? {};
        const nextState = applyStreamingDelta(
          frameStreamingState,
          frame.delta
        );
        stateMutators.setStreamingState(frame.worldline_id, nextState);

        // Handle state transitions
        if (frame.delta.type === "state_transition") {
          const transition: RuntimeStateTransition = {
            from_state:
              typeof frame.delta.from_state === "string"
                ? frame.delta.from_state
                : null,
            to_state: String(frame.delta.to_state ?? ""),
            reason: String(frame.delta.reason ?? "unspecified"),
          };

          if (transition.to_state) {
            stateMutators.appendRuntimeStateTransition(
              frame.worldline_id,
              transition
            );

            if (frame.worldline_id === activeWorldlineId) {
              statusCallbacks.onStateTransition(transition);
            }
          }

          if (frame.worldline_id === activeWorldlineId) {
            statusCallbacks.onScroll();
          }
          return;
        }

        if (frame.worldline_id === activeWorldlineId) {
          const status = statusFromDelta(frame.delta);
          if (status) {
            statusCallbacks.onStatusChange(status);
          }
          statusCallbacks.onScroll();
        }
      },
      onDone: async (frame) => {
        resetStreamingDrafts(frame.worldline_id);
        await refreshWorldlines();
        if (frame.worldline_id) {
          await loadWorldline(frame.worldline_id);
        }

        if (frame.worldline_id === activeWorldlineId) {
          statusCallbacks.onStatusChange("Done");
          statusCallbacks.onScroll();
        }
      },
      onError: (error) => {
        resetStreamingDrafts(worldlineId);
        rollbackOptimisticMessage(worldlineId, optimisticId);
        statusCallbacks.onStatusChange(`Error: ${error}`);
      },
    });
  } catch (error) {
    resetStreamingDrafts(worldlineId);
    rollbackOptimisticMessage(worldlineId, optimisticId);
    const message = error instanceof Error ? error.message : "Request failed";
    statusCallbacks.onStatusChange(message);
  } finally {
    setWorldlineSending(worldlineId, false);
  }
}

function statusFromEventType(eventType: TimelineEvent["type"]): string {
  switch (eventType) {
    case "tool_call_sql":
      return "Running SQL...";
    case "tool_call_python":
      return "Running Python...";
    case "assistant_message":
      return "Done";
    default:
      return "Working...";
  }
}

function statusFromDelta(delta: StreamDeltaPayload): string | null {
  if (delta.type === "state_transition") {
    return null;
  }

  if (delta.skipped) {
    if (delta.reason === "invalid_tool_payload") {
      return "Retrying after invalid tool payload...";
    }
    return "Skipped repeated tool call...";
  }

  if (delta.type === "assistant_text" && !delta.done) {
    return "Composing response...";
  }
  if (delta.type === "tool_call_sql" && !delta.done) {
    return "Drafting SQL...";
  }
  if (delta.type === "tool_call_python" && !delta.done) {
    return "Drafting Python...";
  }

  return null;
}

function applyStreamingDelta(
  state: Record<string, unknown>,
  delta: StreamDeltaPayload
): Record<string, unknown> {
  // Simple delta application - in reality this would use the streamState helpers
  return { ...state, delta };
}
