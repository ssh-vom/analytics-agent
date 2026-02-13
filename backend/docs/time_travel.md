# Time Travel and Branching Notes

This document captures the current worldline behavior after the phase 2-8 refactors.

## Core model

- Events are immutable.
- Each worldline has a `head_event_id` pointer.
- Branches are created from a specific event (`from_event_id`), not from a source worldline's latest head by default.
- Parent linkage is preserved with `parent_worldline_id` and `forked_from_event_id`.

## Write correctness

- Event append + head advance are performed through a shared atomic primitive.
- Writers fail on stale heads instead of silently creating divergent chains.
- Conflict/retry behavior is handled in callers that need it.

## Branch state correctness

- On branch creation, the SQL state is restored at the fork boundary event.
- Snapshot replay has a deterministic fallback path when a direct snapshot is unavailable.
- This prevents accidental inclusion of post-fork source changes.

## Subagent fan-out (blocking)

- Tool: `spawn_subagents`
- The parent chat turn can fan out many child tasks by branching worldlines from one fork event.
- In the primary UX path, each task runs directly as a child turn on a distinct child worldline (not background jobs).
- The parent turn blocks until children complete (or timeout), then receives one aggregated tool result with:
  - per-task status (`completed` / `failed` / `timeout`)
  - child/result worldline IDs
  - assistant preview and latest assistant text (when available)
  - aggregate counters (`completed_count`, `failed_count`, `timed_out_count`)

### Tool arguments

- `tasks` (required): array of task objects, each with:
- `goal` (recommended): high-level objective; backend splits into parallel tasks automatically.
- `tasks` (optional override): array of task objects, each with:
  - `message` (required string)
  - `label` (optional string)
  - `branch_name` (optional string)
- `from_event_id` (optional): explicit fork boundary; defaults to current worldline head
- `timeout_s` (optional): global blocking wait timeout for fan-in
- `max_iterations` (optional): max turn iterations per child run

### Event traceability

Fan-out is persisted directly in the parent worldline event chain:
- `tool_call_subagents`
- `tool_result_subagents`

These events make fan-out/fan-in visible in timeline rendering and allow deterministic replay/audit.

## Worldline summaries for UI

- Endpoint: `GET /api/threads/{thread_id}/worldline-summaries`
- Returns one row per worldline with:
  - message count
  - last event timestamp
  - job counts by status (`queued`, `running`, `completed`, `failed`, `cancelled`)
  - latest job status
  - computed `last_activity`

The worldlines page uses this endpoint to avoid N+1 per-worldline event fetches.

## Mental model for timeline rendering

- Start from a target worldline.
- Walk parent links through `parent_worldline_id`.
- For each segment, include events up to the relevant fork boundary.
- Merge into a deterministic timeline for the selected branch context.
