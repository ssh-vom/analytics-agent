# Agent State Machine Implementation Plan

Last updated: 2026-02-12

## Progress

- Phase 1 is implemented in code (artifact memory + dedup guards + Python reliability hardening).
- Phase 2 has started with explicit state transitions in the chat engine runtime.

## Decisions (Locked)

1. Output format default is `none` ("No strict format").
2. Use **Option A**: include artifact inventory in every LLM context call.
3. Provider surface remains simplified (OpenAI/OpenRouter only).

## Why This Plan

Current issue: repeated artifact creation (same CSVs/PDFs) and unreliable Python tool behavior.

Research-aligned direction (based on TextQL public architecture/docs):
- Runtime tool-access state machine
- Context management across queries
- SQL -> dataset intent -> Python loop
- Explicit invalid-state handling

## Current Gaps in This Repo

1. Tool dedup is mostly turn-local; cross-turn repeated work still occurs.
2. Tool-result truncation can hide artifact metadata from later reasoning.
3. No explicit artifact inventory memory fed to the model each iteration.
4. Python call reliability suffers from argument-shape drift and streaming ID mismatch edge cases.
5. No explicit state machine transitions governing planning/fetching/analyzing/presenting.

## Target State Machine

## States

- `planning`
- `data_fetching`
- `analyzing`
- `presenting`
- `completed`
- `error`

## Allowed transitions

- `planning -> data_fetching | analyzing | presenting | completed`
- `data_fetching -> analyzing | presenting | error`
- `analyzing -> presenting | data_fetching | error`
- `presenting -> completed | analyzing | error`
- `error -> planning | completed`

## Transition guards (core)

- Block repeated identical tool calls unless user explicitly asks to rerun.
- Block duplicate artifact creation when equivalent artifact already exists.
- Require non-empty normalized `sql`/`code` payload before tool execution.
- Enforce one active tool execution at a time per worldline turn.

## Workstream A: Artifact Memory (Option A, always-on)

Goal: model always sees what already exists.

Plan:
1. Build artifact inventory from worldline events/artifact records.
2. Inject inventory into LLM context every iteration (not just first turn).
3. Include compact metadata:
   - `artifact_id`, `name`, `type`, `created_at`, `source_call_id`, `producer` (`sql`/`python`), optional `logical_key`.
4. Add prompt rule: "Reuse existing artifacts when possible; do not regenerate equivalent outputs."

Acceptance:
- Model references existing artifacts instead of recreating same CSV/PDF.
- Repeated "continue" prompts do not spam identical files.

## Workstream B: Deterministic Dedup + Idempotency

Goal: prevent duplicate outputs without brittle heuristics.

Phase 1 (fast/demo, low LOC):
- Dedup by `(tool_name + normalized_args)` and recent artifact names per turn + recent history window.

Phase 2 (durable):
- Add artifact fingerprint/logical key support (e.g., content hash + intent key).
- Skip/relabel duplicates with explicit reason.

Acceptance:
- Same intent in same context does not produce multiple equivalent artifacts.
- Skip reason is visible in event payload/logs (`duplicate_artifact_prevented`).

## Workstream C: Explicit Runtime State Machine

Goal: make behavior predictable and debuggable.

Plan:
1. Add explicit state tracking object in chat engine loop.
2. Transition state on:
   - assistant plan emission
   - tool call start/end
   - final assistant message
   - tool/runtime error
3. Emit state transition events (or structured debug logs).

Acceptance:
- Every turn has traceable state transitions.
- Invalid transitions are rejected with a clear internal error code/message.

## Workstream D: Python Reliability Hardening

Goal: reduce noisy Python failures and empty-code executions.

Plan:
1. Normalize tool arguments aggressively before execution (`code` aliases, nested args, timeout coercion).
2. Harden streaming tool-call ID reconciliation across start/delta/done.
3. Add soft-recovery path for empty Python payload:
   - one guided retry with explicit correction hint to LLM.
4. Preserve clear UI reason when call/result ordering differs.

Acceptance:
- Significant reduction in `run_python requires a non-empty 'code' string`.
- Fewer orphan/mismatched Python call-result cells.

## Workstream E: SQL->Python Checkpointing

Goal: mirror TextQL-style staged flow.

Plan:
1. Capture a short "data intent summary" after SQL success:
   - what dataset was fetched
   - key dimensions/measures
   - row count and time scope
2. Feed this back into subsequent Python/planning steps.

Acceptance:
- Fewer redundant SQL/Python loops.
- Better continuity when user asks "continue" / "refine this".

## Workstream F: Observability + Error Taxonomy

Goal: make failures diagnosable.

Plan:
1. Introduce structured reasons:
   - `invalid_state`
   - `duplicate_tool_call_skipped`
   - `duplicate_artifact_prevented`
   - `empty_python_payload_retried`
2. Emit counters for:
   - duplicate skips
   - artifact dedups
   - python normalization rescues
   - retry success rate

Acceptance:
- We can quantify reliability improvements from logs/tests.

## Workstream G: Tests

Add/extend tests for:

1. Artifact inventory always included in context (Option A).
2. Duplicate artifact prevention across:
   - same turn
   - follow-up "continue" turn.
3. State transition validity.
4. Empty Python code recovery path.
5. Streaming call-id mismatch still reconstructs valid tool call.
6. Report mode + artifact dedup coexist without regressions.

## File-Level Change Map

Backend:
- `backend/chat/engine.py` (state machine + guards + retries)
- `backend/chat/message_builder.py` (artifact inventory injection)
- `backend/chat/tooling.py` (argument normalization/idempotency helpers)
- `backend/chat/streaming_bridge.py` (stream call-id robustness)
- `backend/tools.py` (optional artifact dedup persistence hooks)
- `backend/tests/test_chat_api.py`
- `backend/tests/test_chat_context_parser.py`
- `backend/tests/test_chat_tooling.py`
- `backend/tests/test_streaming_bridge.py`
- `backend/tests/test_python_tool.py`
- `backend/tests/test_state_machine.py` (new)

Frontend:
- `frontend/src/lib/components/PythonCell.svelte` (clearer failure context)
- optional: status badges for skip/dedup reasons in tool cells

## Implementation Phases

Phase 1 (Demo stability, 1-2 days):
- Workstreams A + B (phase1) + D + key tests.

Phase 2 (Predictable orchestration, 1-2 days):
- Workstreams C + E + tests.

Phase 3 (Durability, 1-2 days):
- Workstream B (phase2 fingerprinting) + F + regression suite.

## Acceptance Criteria (End-to-End)

1. Repeated analysis prompts do not regenerate equivalent CSV/PDF artifacts.
2. "Continue" follows prior state/artifacts instead of restarting from scratch.
3. Python empty-code and stream mismatch errors are rare and recoverable.
4. State transitions are explicit and observable.
5. Full backend/frontend tests pass.

## Out of Scope (This Plan)

- Full ontology compiler implementation
- Non-OpenAI/OpenRouter provider expansion
- Cross-org/global memory beyond worldline/thread scope
