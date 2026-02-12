# Agent State Machine Implementation Plan

Last updated: 2026-02-12

## Progress

- Phase 1 is implemented in code (artifact memory + dedup guards + Python reliability hardening).
- Phase 2 has started with explicit state transitions in the chat engine runtime.

## Decisions (Locked)

1. Output format default is `none` ("No strict format").
2. Use **Option A**: include artifact inventory in every LLM context call.
3. Provider surface remains simplified (OpenAI/OpenRouter only).
4. Keep **free-form Python** as the primary execution mode (no template-only lock-in).
5. Adopt a **hybrid semantic layer**: schema introspection + manual business overrides.
6. State machine must be authoritative (guarding execution/presentation), not just observability.

## Codebase Reality Check (Current)

Hotspots that currently increase regression risk and reduce readability:

- `backend/chat/engine.py` is oversized and mixed-concern (runtime orchestration + report generator payload + policy helpers).
- `frontend/src/routes/chat/+page.svelte` is oversized and carries orchestration, streaming, job, context, and UI duties in one place.
- Some logic is duplicated across engine/message builder layers (artifact/context behavior).
- Minimal lint/format/type quality gates are currently enforced in the repo.

Refactor intent for this plan update:

- Reduce LOC in the largest high-churn files.
- Improve readability and module boundaries.
- Make behavior easier to reason about and extend safely.

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
6. Core orchestration code is too concentrated in monolith files, making correctness changes costly.
7. Readability/style consistency is not protected by repo-level quality gates.

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
- Transition guards are the source of truth for whether a turn may finalize.

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

## Workstream H: Code Reduction + Readability Refactor

Goal: reduce complexity in high-risk files while preserving behavior.

Plan:
1. Split runtime concerns out of `backend/chat/engine.py` into focused modules:
   - `backend/chat/state_machine.py` (states/transitions/guards)
   - `backend/chat/policy.py` (dedup/retry/required-tool policy)
   - `backend/chat/context_parser.py` (context block parsing)
   - `backend/chat/report_fallback.py` (report generator payload + helpers)
2. Remove dead/unused helpers and duplicated logic paths after extraction.
3. Decompose `frontend/src/routes/chat/+page.svelte` into route-shell + feature modules (stream/session/composer/state trace).
4. Keep contracts stable while reducing surface area per file/function.

Acceptance:
- `backend/chat/engine.py` is reduced to orchestration-only responsibilities.
- `frontend/src/routes/chat/+page.svelte` no longer owns most orchestration logic.
- Extracted modules have clear ownership and lower cognitive load.

## Workstream I: Engineering Standards + Quality Gates

Goal: keep readability/extensibility improvements durable.

Plan:
1. Introduce backend lint/format/type checks (ruff-style baseline, import ordering, basic complexity caps).
2. Introduce frontend lint/type checks (ESLint + TypeScript checks) and wire into CI/test workflow.
3. Add lightweight code standards for new modules:
   - avoid mixed concerns in single files
   - prefer typed payload contracts at boundaries
   - centralize reason code taxonomy
   - keep functions focused and short

Acceptance:
- New/updated modules follow consistent style.
- Core runtime files fail fast on style/type regressions.
- Future edits are simpler to review and safer to merge.

## Workstream J: Hybrid Semantic Layer (MVP)

Goal: deterministic answers for covered business asks while preserving agentic fallback.

Plan:
1. Add semantic catalog package:
   - `backend/semantic/catalog.py` for schema introspection
   - `backend/semantic/catalog_overrides.yaml` for manual business mapping/synonyms
2. Add resolver/compiler:
   - `backend/semantic/resolver.py` (user terms -> semantic entities + confidence)
   - `backend/semantic/compiler.py` (QuerySpec -> deterministic SQL)
3. Runtime lane selection:
   - high confidence -> deterministic SQL lane
   - low confidence -> existing tool-calling lane

Acceptance:
- Ontology-covered intents compile to deterministic SQL.
- Uncovered intents still work through agentic fallback without breaking flow.
- Semantic layer remains lightweight and maintainable.

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
- `backend/chat/state_machine.py` (new, authoritative transitions/guards)
- `backend/chat/policy.py` (new, retry/dedup/required-tool policy)
- `backend/chat/context_parser.py` (new, context parsing)
- `backend/chat/report_fallback.py` (new, report fallback payload/helpers)
- `backend/semantic/catalog.py` (new, schema + override merge)
- `backend/semantic/resolver.py` (new, semantic resolution)
- `backend/semantic/compiler.py` (new, QuerySpec compiler)
- `backend/semantic/types.py` (new, semantic/queryspec contracts)
- `backend/semantic/catalog_overrides.yaml` (new, manual override source)
- `backend/tools.py` (optional artifact dedup persistence hooks)
- `backend/tests/test_chat_api.py`
- `backend/tests/test_chat_context_parser.py`
- `backend/tests/test_chat_tooling.py`
- `backend/tests/test_streaming_bridge.py`
- `backend/tests/test_python_tool.py`
- `backend/tests/test_state_machine.py` (new)

Frontend:
- `frontend/src/lib/components/PythonCell.svelte` (clearer failure context)
- `frontend/src/routes/chat/+page.svelte` (decomposition into route shell)
- `frontend/src/lib/chat/*` (expanded feature modules for stream/session/composer state)
- optional: status badges for skip/dedup reasons in tool cells

## Implementation Phases

Phase 0 (Stability unblock, 0.5-1 day):
- Fix deterministic runtime issues first (including report fallback payload integrity).
- Add Python compile preflight and typed retry reasons before sandbox execution.

Phase 1 (Architecture extraction, 1-2 days):
- Workstreams C + H (state machine extraction + engine decomposition).

Phase 2 (Semantic MVP, 1-2 days):
- Workstream J (hybrid semantic layer + deterministic lane).

Phase 3 (Reliability + durability, 1-2 days):
- Workstreams A + B (phase1/phase2) + E + F.

Phase 4 (Readability hardening, 1 day):
- Workstream I + frontend chat route decomposition completion.

## Acceptance Criteria (End-to-End)

1. Repeated analysis prompts do not regenerate equivalent CSV/PDF artifacts.
2. "Continue" follows prior state/artifacts instead of restarting from scratch.
3. Python empty-code and stream mismatch errors are rare and recoverable.
4. State transitions are explicit and observable.
5. Full backend/frontend tests pass.
6. Free-form Python remains default, but malformed code is caught pre-execution with clear recovery.
7. Hybrid semantic coverage yields deterministic SQL for covered intents.
8. Core hotspot files are reduced and easier to read/extend.

## Out of Scope (This Plan)

- Full enterprise ontology platform (beyond hybrid MVP)
- Non-OpenAI/OpenRouter provider expansion
- Cross-org/global memory beyond worldline/thread scope
