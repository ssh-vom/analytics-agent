import type {
  PythonResultPayload,
  SqlResultPayload,
  TimelineEvent,
} from "$lib/types";

export type RenderCell =
  | MessageRenderCell
  | SqlRenderCell
  | PythonRenderCell
  | MetaRenderCell;

export interface MessageRenderCell {
  kind: "message";
  id: string;
  role: "user" | "assistant" | "plan";
  text: string;
  event: TimelineEvent;
}

export interface SqlRenderCell {
  kind: "sql";
  id: string;
  call: TimelineEvent | null;
  result: TimelineEvent | null;
}

export interface PythonRenderCell {
  kind: "python";
  id: string;
  call: TimelineEvent | null;
  result: TimelineEvent | null;
}

export interface MetaRenderCell {
  kind: "meta";
  id: string;
  label: string;
  event: TimelineEvent;
}

function payloadText(payload: Record<string, unknown>): string {
  const text = payload.text;
  if (typeof text === "string") {
    return text;
  }
  return JSON.stringify(payload);
}

export function readSqlResult(event: TimelineEvent | null): SqlResultPayload | null {
  if (!event) {
    return null;
  }
  return event.payload as unknown as SqlResultPayload;
}

export function readPythonResult(
  event: TimelineEvent | null,
): PythonResultPayload | null {
  if (!event) {
    return null;
  }
  return event.payload as unknown as PythonResultPayload;
}

export function groupEventsIntoCells(events: TimelineEvent[]): RenderCell[] {
  const cells: RenderCell[] = [];
  const sqlByCallId = new Map<string, SqlRenderCell>();
  const pythonByCallId = new Map<string, PythonRenderCell>();
  const pendingSqlResults = new Map<string, TimelineEvent>();
  const pendingPythonResults = new Map<string, TimelineEvent>();

  for (const event of events) {
    switch (event.type) {
      case "user_message":
        cells.push({
          kind: "message",
          id: `msg-${event.id}`,
          role: "user",
          text: payloadText(event.payload),
          event,
        });
        break;
      case "assistant_plan":
        cells.push({
          kind: "message",
          id: `plan-${event.id}`,
          role: "plan",
          text: payloadText(event.payload),
          event,
        });
        break;
      case "assistant_message":
        cells.push({
          kind: "message",
          id: `assistant-${event.id}`,
          role: "assistant",
          text: payloadText(event.payload),
          event,
        });
        break;
      case "tool_call_sql": {
        const cell: SqlRenderCell = {
          kind: "sql",
          id: `sql-${event.id}`,
          call: event,
          result: pendingSqlResults.get(event.id) ?? null,
        };
        pendingSqlResults.delete(event.id);
        cells.push(cell);
        sqlByCallId.set(event.id, cell);
        break;
      }
      case "tool_result_sql": {
        const parentId = event.parent_event_id ?? "";
        const existing = sqlByCallId.get(parentId);
        if (existing) {
          existing.result = event;
        } else {
          pendingSqlResults.set(parentId, event);
        }
        break;
      }
      case "tool_call_python": {
        const cell: PythonRenderCell = {
          kind: "python",
          id: `py-${event.id}`,
          call: event,
          result: pendingPythonResults.get(event.id) ?? null,
        };
        pendingPythonResults.delete(event.id);
        cells.push(cell);
        pythonByCallId.set(event.id, cell);
        break;
      }
      case "tool_result_python": {
        const parentId = event.parent_event_id ?? "";
        const existing = pythonByCallId.get(parentId);
        if (existing) {
          existing.result = event;
        } else {
          pendingPythonResults.set(parentId, event);
        }
        break;
      }
      case "time_travel":
        cells.push({
          kind: "meta",
          id: `meta-${event.id}`,
          label: "Time travel",
          event,
        });
        break;
      case "worldline_created":
        cells.push({
          kind: "meta",
          id: `meta-${event.id}`,
          label: "Worldline created",
          event,
        });
        break;
      default:
        cells.push({
          kind: "meta",
          id: `meta-${event.id}`,
          label: event.type,
          event,
        });
    }
  }

  for (const [parentId, resultEvent] of pendingSqlResults.entries()) {
    cells.push({
      kind: "sql",
      id: `sql-orphan-${parentId}-${resultEvent.id}`,
      call: null,
      result: resultEvent,
    });
  }
  for (const [parentId, resultEvent] of pendingPythonResults.entries()) {
    cells.push({
      kind: "python",
      id: `py-orphan-${parentId}-${resultEvent.id}`,
      call: null,
      result: resultEvent,
    });
  }

  return cells;
}
