import { describe, it, expect } from "vitest";
import {
  createOptimisticUserMessage,
  insertOptimisticEvent,
  replaceOptimisticWithReal,
  removeOptimisticEvent,
} from "./optimisticState";
import type { TimelineEvent } from "$lib/types";

describe("optimisticState", () => {
  describe("createOptimisticUserMessage", () => {
    it("creates an optimistic user message event", () => {
      const { id, event } = createOptimisticUserMessage("Hello world");

      expect(id).toMatch(/^optimistic-user-\d+$/);
      expect(event.id).toBe(id);
      expect(event.type).toBe("user_message");
      expect(event.payload.text).toBe("Hello world");
      expect(event.parent_event_id).toBeNull();
      expect(event.created_at).toBeDefined();
    });

    it("generates IDs with timestamp prefix", () => {
      const result = createOptimisticUserMessage("Test");

      expect(result.id).toMatch(/^optimistic-user-\d+$/);
      expect(result.event.id).toBe(result.id);
    });
  });

  describe("insertOptimisticEvent", () => {
    it("appends optimistic event to empty list", () => {
      const optimistic = createOptimisticUserMessage("Test");
      const events = insertOptimisticEvent([], optimistic.event);

      expect(events).toHaveLength(1);
      expect(events[0].id).toBe(optimistic.id);
    });

    it("appends optimistic event to existing events", () => {
      const existing: TimelineEvent = {
        id: "event_1",
        parent_event_id: null,
        type: "assistant_message",
        payload: { text: "Hi" },
        created_at: "2024-01-01T00:00:00Z",
      };
      const optimistic = createOptimisticUserMessage("Test");

      const events = insertOptimisticEvent([existing], optimistic.event);

      expect(events).toHaveLength(2);
      expect(events[0].id).toBe("event_1");
      expect(events[1].id).toBe(optimistic.id);
    });

    it("preserves immutability of input array", () => {
      const existing: TimelineEvent[] = [
        {
          id: "event_1",
          parent_event_id: null,
          type: "assistant_message",
          payload: { text: "Hi" },
          created_at: "2024-01-01T00:00:00Z",
        },
      ];
      const optimistic = createOptimisticUserMessage("Test");

      const events = insertOptimisticEvent(existing, optimistic.event);

      expect(events).not.toBe(existing);
      expect(existing).toHaveLength(1);
    });
  });

  describe("replaceOptimisticWithReal", () => {
    it("replaces optimistic event when real user_message arrives", () => {
      const optimistic = createOptimisticUserMessage("Hello");
      const events = insertOptimisticEvent([], optimistic.event);

      const realEvent: TimelineEvent = {
        id: "real_event_1",
        parent_event_id: null,
        type: "user_message",
        payload: { text: "Hello" },
        created_at: "2024-01-01T00:00:01Z",
      };

      const result = replaceOptimisticWithReal(events, optimistic.id, realEvent);

      expect(result.replaced).toBe(true);
      expect(result.events).toHaveLength(1);
      expect(result.events[0].id).toBe("real_event_1");
      expect(result.events[0].type).toBe("user_message");
    });

    it("appends real event when no optimistic ID provided", () => {
      const existing: TimelineEvent = {
        id: "event_1",
        parent_event_id: null,
        type: "assistant_message",
        payload: { text: "Hi" },
        created_at: "2024-01-01T00:00:00Z",
      };

      const realEvent: TimelineEvent = {
        id: "real_event_1",
        parent_event_id: null,
        type: "user_message",
        payload: { text: "Hello" },
        created_at: "2024-01-01T00:00:01Z",
      };

      const result = replaceOptimisticWithReal([existing], null, realEvent);

      expect(result.replaced).toBe(false);
      expect(result.events).toHaveLength(2);
      expect(result.events[1].id).toBe("real_event_1");
    });

    it("handles missing optimistic event gracefully", () => {
      const existing: TimelineEvent = {
        id: "event_1",
        parent_event_id: null,
        type: "assistant_message",
        payload: { text: "Hi" },
        created_at: "2024-01-01T00:00:00Z",
      };

      const realEvent: TimelineEvent = {
        id: "real_event_1",
        parent_event_id: null,
        type: "user_message",
        payload: { text: "Hello" },
        created_at: "2024-01-01T00:00:01Z",
      };

      const result = replaceOptimisticWithReal([existing], "non_existent_id", realEvent);

      expect(result.replaced).toBe(false);
      expect(result.events).toHaveLength(2);
    });

    it("preserves other events when replacing", () => {
      const optimistic = createOptimisticUserMessage("Hello");
      const otherEvent: TimelineEvent = {
        id: "other_1",
        parent_event_id: null,
        type: "assistant_message",
        payload: { text: "Welcome" },
        created_at: "2024-01-01T00:00:00Z",
      };

      const events = [otherEvent, optimistic.event];

      const realEvent: TimelineEvent = {
        id: "real_event_1",
        parent_event_id: null,
        type: "user_message",
        payload: { text: "Hello" },
        created_at: "2024-01-01T00:00:01Z",
      };

      const result = replaceOptimisticWithReal(events, optimistic.id, realEvent);

      expect(result.events).toHaveLength(2);
      expect(result.events[0].id).toBe("other_1");
      expect(result.events[1].id).toBe("real_event_1");
    });
  });

  describe("removeOptimisticEvent", () => {
    it("removes optimistic event by ID", () => {
      const optimistic = createOptimisticUserMessage("Hello");
      const events = insertOptimisticEvent([], optimistic.event);

      const result = removeOptimisticEvent(events, optimistic.id);

      expect(result).toHaveLength(0);
    });

    it("preserves other events when removing", () => {
      const optimistic = createOptimisticUserMessage("Hello");
      const otherEvent: TimelineEvent = {
        id: "other_1",
        parent_event_id: null,
        type: "assistant_message",
        payload: { text: "Welcome" },
        created_at: "2024-01-01T00:00:00Z",
      };

      const events = [otherEvent, optimistic.event];
      const result = removeOptimisticEvent(events, optimistic.id);

      expect(result).toHaveLength(1);
      expect(result[0].id).toBe("other_1");
    });

    it("returns unchanged array when no optimistic ID", () => {
      const events: TimelineEvent[] = [
        {
          id: "event_1",
          parent_event_id: null,
          type: "assistant_message",
          payload: { text: "Hi" },
          created_at: "2024-01-01T00:00:00Z",
        },
      ];

      const result = removeOptimisticEvent(events, null);

      expect(result).toBe(events);
      expect(result).toHaveLength(1);
    });

    it("returns unchanged array when optimistic ID not found", () => {
      const events: TimelineEvent[] = [
        {
          id: "event_1",
          parent_event_id: null,
          type: "assistant_message",
          payload: { text: "Hi" },
          created_at: "2024-01-01T00:00:00Z",
        },
      ];

      const result = removeOptimisticEvent(events, "non_existent");

      expect(result).toHaveLength(1);
      expect(result[0].id).toBe("event_1");
    });
  });

  describe("characterization: current behavior on stream error", () => {
    it("characterizes current behavior - optimistic event remains on error (no rollback)", () => {
      const optimistic = createOptimisticUserMessage("Hello");
      const events = insertOptimisticEvent([], optimistic.event);

      expect(events).toHaveLength(1);
      expect(events[0].id).toBe(optimistic.id);

      const result = removeOptimisticEvent(events, optimistic.id);

      expect(result).toHaveLength(0);
    });

    it("characterizes current behavior - optimistic can be manually removed", () => {
      const optimistic = createOptimisticUserMessage("Hello");
      const events = insertOptimisticEvent([], optimistic.event);

      const cleaned = removeOptimisticEvent(events, optimistic.id);
      expect(cleaned).toHaveLength(0);
    });
  });
});
