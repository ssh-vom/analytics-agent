from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from fastapi import HTTPException

from debug_log import debug_log as _debug_log
from chat.event_store import (
    append_worldline_event,
    append_worldline_event_with_parent,
    events_since_rowid,
    load_event_by_id,
    max_worldline_rowid,
)
from chat.llm_client import (
    ChatMessage,
    LlmClient,
    LlmResponse,
    ToolCall,
    ToolDefinition,
)
from chat.message_builder import build_llm_messages_from_events
from chat.context_parser import extract_output_type, extract_selected_external_aliases
from chat.artifact_memory import (
    artifact_inventory_from_events,
    artifact_inventory_from_tool_result,
    artifact_name_set,
    merge_artifact_inventory,
    upsert_artifact_inventory_message,
)
from chat.data_intent import (
    build_data_intent_summary,
    data_intent_from_events,
    upsert_data_intent_message,
)
from chat.policy import (
    build_python_execution_retry_message,
    build_python_preflight_retry_message,
    build_required_tool_enforcement_message,
    build_tool_payload_correction_message,
    is_empty_python_payload_error,
    is_retryable_python_preflight_error,
    missing_required_terminal_tools as find_missing_required_terminal_tools,
    required_terminal_tools as determine_required_terminal_tools,
    user_requested_rerun as user_requested_rerun_hint,
    validate_tool_payload,
)
from chat.report_fallback import (
    AUTO_REPORT_CODE,
    artifact_is_pdf,
    assert_auto_report_code_compiles,
    events_contain_pdf_artifact,
)
from chat.state_machine import transition_state
from chat.streaming_bridge import stream_llm_response
from chat.subagents import (
    resolve_fork_event_id_or_head,
    spawn_subagents_blocking,
)
from chat.tooling import (
    normalize_tool_arguments,
    tool_definitions,
    tool_name_to_delta_type,
)
from chat.runtime.tool_dispatcher import ToolDispatcher
from api.tools import (
    PythonToolRequest,
)
from api.worldlines import get_worldline_events
from services.tool_executor import (
    execute_python_tool,
    execute_sql_tool,
)
from worldline_service import WorldlineService

logger = logging.getLogger(__name__)

_MAX_INVALID_TOOL_PAYLOAD_RETRIES: dict[str, int] = {
    "run_sql": 2,
    "run_python": 3,
}
_ARTIFACT_FILENAME_PATTERN = re.compile(
    r"['\"]([^'\"\n\r]+\.(?:csv|png|jpg|jpeg|pdf|svg|json|parquet|xlsx|html))['\"]",
    re.IGNORECASE,
)
_MAX_REQUIRED_TOOL_ENFORCEMENT_RETRIES = 2
_MAX_PYTHON_EXECUTION_ERROR_RETRIES = 2


@dataclass
class ChatEngine:
    llm_client: LlmClient
    max_iterations: int = 75
    max_output_tokens: int | None = None
    worldline_service: WorldlineService = field(default_factory=WorldlineService)
    _tool_dispatcher: ToolDispatcher | None = field(default=None, repr=False)

    def _get_tool_dispatcher(self) -> ToolDispatcher:
        if self._tool_dispatcher is None:
            self._tool_dispatcher = ToolDispatcher(
                llm_client=self.llm_client,
                worldline_service=self.worldline_service,
                load_event_by_id=self._load_event_by_id,
                run_child_turn=self._run_child_turn,
                execute_sql_tool=execute_sql_tool,
                execute_python_tool=execute_python_tool,
                resolve_fork_event_id_or_head=resolve_fork_event_id_or_head,
                spawn_subagents_blocking=spawn_subagents_blocking,
                get_turn_coordinator=self._get_turn_coordinator,
            )
        return self._tool_dispatcher

    @staticmethod
    def _get_turn_coordinator():
        from services.chat_runtime import get_turn_coordinator

        return get_turn_coordinator()

    async def run_turn(
        self,
        worldline_id: str,
        message: str,
        on_event: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
        on_delta: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
        allow_subagents: bool = True,
        subagent_depth: int = 0,
        allow_tools: bool = True,
    ) -> tuple[str, list[dict[str, Any]]]:
        MAX_SUBAGENT_DEPTH = 2

        effective_allow_subagents = (
            allow_subagents and subagent_depth < MAX_SUBAGENT_DEPTH
        )

        if not message or not message.strip():
            raise HTTPException(status_code=400, detail="message must not be empty")

        allowed_external_aliases = extract_selected_external_aliases(message)
        requested_output_type = extract_output_type(message)

        # #region agent log
        _debug_log(
            run_id="initial",
            hypothesis_id="H3_H4",
            location="backend/chat/engine.py:run_turn:start",
            message="Starting chat turn",
            data={
                "worldline_id": worldline_id,
                "message_preview": message[:200],
                "allow_tools": allow_tools,
                "allow_subagents": effective_allow_subagents,
                "subagent_depth": subagent_depth,
            },
        )
        # #endregion

        active_worldline_id = worldline_id
        starting_rowid_by_worldline = {
            active_worldline_id: self._max_worldline_rowid(active_worldline_id)
        }
        user_event = self._append_worldline_event(
            worldline_id=active_worldline_id,
            event_type="user_message",
            payload={"text": message},
        )
        if on_event is not None:
            await on_event(active_worldline_id, user_event)

        history_events = await self._load_worldline_events(active_worldline_id)
        messages = build_llm_messages_from_events(list(history_events))
        artifact_inventory = artifact_inventory_from_events(history_events)
        data_intent_summary = data_intent_from_events(history_events)
        recent_artifact_names = artifact_name_set(artifact_inventory)
        user_requested_rerun_flag = user_requested_rerun_hint(message)
        required_tools: set[str] = (
            determine_required_terminal_tools(
                message=message,
                requested_output_type=requested_output_type,
            )
            if allow_tools
            else set()
        )
        final_text: str | None = None
        stopped_due_to_invalid_payload = False
        stopped_due_to_guard_stop = False
        turn_artifact_names: set[str] = set()
        sql_success_count = 0
        python_success_count = 0
        empty_response_retries = 0
        required_tool_enforcement_failures = 0
        invalid_tool_payload_failures: dict[str, int] = {
            "run_sql": 0,
            "run_python": 0,
        }
        python_execution_error_count = 0
        turn_state = "planning"
        state_transitions: list[dict[str, Any]] = [
            {"from": None, "to": "planning", "reason": "turn_started"}
        ]
        await self._emit_state_transition_delta(
            worldline_id=active_worldline_id,
            transition=state_transitions[0],
            on_delta=on_delta,
        )

        _include_spawn = effective_allow_subagents

        for _ in range(self.max_iterations):
            upsert_artifact_inventory_message(messages, artifact_inventory)
            upsert_data_intent_message(messages, data_intent_summary)

            turn_tools: list[ToolDefinition] = []
            if allow_tools:
                turn_tools = self._tool_definitions(
                    include_python=True,
                    include_spawn_subagents=_include_spawn,
                )
            if on_delta is not None:
                response = await self._stream_llm_response(
                    worldline_id=active_worldline_id,
                    messages=messages,
                    tools=turn_tools,
                    on_delta=on_delta,
                )
            else:
                response = await self.llm_client.generate(
                    messages=messages,
                    tools=turn_tools,
                    max_output_tokens=self.max_output_tokens,
                )
            response_tool_calls = response.tool_calls if allow_tools else []

            # ----- Emit assistant_plan if text accompanies tool calls ----------
            if response.text and response_tool_calls:
                plan_event = self._append_worldline_event(
                    worldline_id=active_worldline_id,
                    event_type="assistant_plan",
                    payload={"text": response.text},
                )
                if on_event is not None:
                    await on_event(active_worldline_id, plan_event)

            if response.text:
                messages.append(ChatMessage(role="assistant", content=response.text))

            if response_tool_calls:
                repeated_call_detected = False
                invalid_payload_retry_requested = False
                python_execution_retry_requested = False
                for raw_tool_call in response_tool_calls:
                    tool_name = (raw_tool_call.name or "").strip()
                    normalized_arguments = normalize_tool_arguments(
                        tool_name,
                        dict(raw_tool_call.arguments or {}),
                    )
                    tool_call = ToolCall(
                        id=raw_tool_call.id,
                        name=raw_tool_call.name,
                        arguments=normalized_arguments,
                    )
                    delta_type = self._tool_name_to_delta_type(tool_name)
                    # #region agent log
                    if tool_name == "run_sql":
                        turn_state = await self._transition_state_and_emit(
                            current_state=turn_state,
                            to_state="data_fetching",
                            reason="tool_call:run_sql",
                            transitions=state_transitions,
                            worldline_id=active_worldline_id,
                            on_delta=on_delta,
                        )
                    elif tool_name == "run_python":
                        turn_state = await self._transition_state_and_emit(
                            current_state=turn_state,
                            to_state="analyzing",
                            reason="tool_call:run_python",
                            transitions=state_transitions,
                            worldline_id=active_worldline_id,
                            on_delta=on_delta,
                        )

                    payload_error = validate_tool_payload(
                        tool_name=tool_name,
                        arguments=tool_call.arguments or {},
                    )
                    if payload_error is not None:
                        if on_delta is not None and delta_type is not None:
                            await on_delta(
                                active_worldline_id,
                                {
                                    "type": delta_type,
                                    "call_id": tool_call.id or None,
                                    "skipped": True,
                                    "reason": "invalid_tool_payload",
                                    "error": payload_error,
                                },
                            )

                        turn_state = await self._transition_state_and_emit(
                            current_state=turn_state,
                            to_state="error",
                            reason=f"invalid_tool_payload:{tool_name}",
                            transitions=state_transitions,
                            worldline_id=active_worldline_id,
                            on_delta=on_delta,
                        )
                        turn_state = await self._transition_state_and_emit(
                            current_state=turn_state,
                            to_state="planning",
                            reason="recover_after_invalid_tool_payload",
                            transitions=state_transitions,
                            worldline_id=active_worldline_id,
                            on_delta=on_delta,
                        )

                        invalid_tool_payload_failures[tool_name] = (
                            invalid_tool_payload_failures.get(tool_name, 0) + 1
                        )
                        max_retries = _MAX_INVALID_TOOL_PAYLOAD_RETRIES.get(
                            tool_name, 2
                        )

                        if invalid_tool_payload_failures[tool_name] <= max_retries:
                            messages.append(
                                ChatMessage(
                                    role="system",
                                    content=build_tool_payload_correction_message(
                                        tool_name=tool_name,
                                        payload_error=payload_error,
                                        data_intent_summary=data_intent_summary,
                                    ),
                                )
                            )
                            invalid_payload_retry_requested = True
                            break

                        stopped_due_to_invalid_payload = True
                        if tool_name == "run_python":
                            final_text = (
                                "I couldn't execute the Python code. Please try asking again "
                                "or rephrasing (e.g. 'run Python to plot X' or 'visualize the data')."
                            )
                        else:
                            final_text = (
                                "I stopped after repeated invalid SQL tool payloads "
                                "(empty/invalid `sql`). I can continue once the model emits "
                                "a valid run_sql call."
                            )
                        repeated_call_detected = True
                        break

                    if tool_name == "run_python" and not user_requested_rerun_flag:
                        code = (tool_call.arguments or {}).get("code")
                        if isinstance(code, str) and code.strip():
                            candidate_names = (
                                self._extract_artifact_names_from_python_code(code)
                            )
                            if candidate_names:
                                existing_names = sorted(
                                    {
                                        name
                                        for name in candidate_names
                                        if name in recent_artifact_names
                                        or name in turn_artifact_names
                                    }
                                )
                                if existing_names and len(existing_names) == len(
                                    candidate_names
                                ):
                                    if on_delta is not None and delta_type is not None:
                                        await on_delta(
                                            active_worldline_id,
                                            {
                                                "type": delta_type,
                                                "call_id": tool_call.id or None,
                                                "skipped": True,
                                                "reason": "duplicate_artifact_prevented",
                                                "artifact_names": existing_names,
                                            },
                                        )
                                    final_text = (
                                        "I skipped Python execution because it would recreate "
                                        "existing artifacts: "
                                        + ", ".join(existing_names)
                                        + ". Ask me to rerun or overwrite if you want new files."
                                    )
                                    repeated_call_detected = True
                                    break

                    # Note: tool call deltas were already streamed in _stream_llm_response.
                    # For the non-streaming path (on_delta is None), no deltas are emitted.

                    # Small delay between tool calls to avoid burning through API requests too fast
                    await asyncio.sleep(0.1)

                    tool_result, switched_worldline_id = await self._execute_tool_call(
                        worldline_id=active_worldline_id,
                        tool_call=tool_call,
                        carried_user_message=message,
                        allowed_external_aliases=allowed_external_aliases,
                        on_event=on_event,
                        on_delta=on_delta,
                        subagent_depth=subagent_depth,
                    )

                    if tool_name == "spawn_subagents" and _include_spawn:
                        _include_spawn = False

                    if (
                        switched_worldline_id
                        and switched_worldline_id != active_worldline_id
                    ):
                        active_worldline_id = switched_worldline_id
                        starting_rowid_by_worldline.setdefault(active_worldline_id, 0)
                        history_events = await self._load_worldline_events(
                            active_worldline_id
                        )
                        messages = build_llm_messages_from_events(list(history_events))
                        artifact_inventory = artifact_inventory_from_events(
                            history_events
                        )
                        data_intent_summary = data_intent_from_events(history_events)
                        recent_artifact_names = artifact_name_set(artifact_inventory)
                        turn_artifact_names.clear()
                        # Reset per-turn state for the new worldline context
                        sql_success_count = 0
                        python_success_count = 0
                        python_execution_error_count = 0
                        turn_state = await self._transition_state_and_emit(
                            current_state=turn_state,
                            to_state="planning",
                            reason="worldline_switched",
                            transitions=state_transitions,
                            worldline_id=active_worldline_id,
                            on_delta=on_delta,
                        )

                    serialized = self._serialize_tool_result_for_context(
                        tool_name=tool_name,
                        tool_result=tool_result,
                    )
                    messages.append(
                        ChatMessage(
                            role="tool",
                            content=serialized,
                            tool_call_id=tool_call.id or None,
                        )
                    )
                    if tool_name == "spawn_subagents":
                        partial_failure_nudge = (
                            self._build_subagent_partial_failure_nudge(tool_result)
                        )
                        if partial_failure_nudge:
                            messages.append(
                                ChatMessage(
                                    role="system",
                                    content=partial_failure_nudge,
                                )
                            )

                    if tool_name == "run_python" and is_empty_python_payload_error(
                        tool_result
                    ):
                        invalid_tool_payload_failures["run_python"] += 1
                        if (
                            invalid_tool_payload_failures["run_python"]
                            <= _MAX_INVALID_TOOL_PAYLOAD_RETRIES["run_python"]
                        ):
                            messages.append(
                                ChatMessage(
                                    role="system",
                                    content=build_tool_payload_correction_message(
                                        tool_name="run_python",
                                        payload_error=(
                                            "run_python requires a non-empty `code` "
                                            "string containing executable Python"
                                        ),
                                        data_intent_summary=data_intent_summary,
                                    ),
                                )
                            )
                            invalid_payload_retry_requested = True
                            break
                        else:
                            stopped_due_to_invalid_payload = True
                            final_text = (
                                "I couldn't execute the Python code. Please try asking again "
                                "or rephrasing (e.g. 'run Python to plot X' or 'visualize the data')."
                            )
                            repeated_call_detected = True
                            break

                    if not tool_result.get("error"):
                        if tool_name == "run_sql":
                            sql_success_count += 1
                            turn_state = await self._transition_state_and_emit(
                                current_state=turn_state,
                                to_state="analyzing",
                                reason="tool_result:run_sql_success",
                                transitions=state_transitions,
                                worldline_id=active_worldline_id,
                                on_delta=on_delta,
                            )
                            data_intent_summary = build_data_intent_summary(
                                sql=(tool_call.arguments or {}).get("sql"),
                                sql_result=tool_result,
                            )
                        if tool_name == "run_python":
                            python_success_count += 1
                            turn_state = await self._transition_state_and_emit(
                                current_state=turn_state,
                                to_state="analyzing",
                                reason="tool_result:run_python_success",
                                transitions=state_transitions,
                                worldline_id=active_worldline_id,
                                on_delta=on_delta,
                            )
                            new_inventory_entries = artifact_inventory_from_tool_result(
                                tool_result,
                                source_call_id=tool_call.id,
                                producer="run_python",
                            )
                            if new_inventory_entries:
                                artifact_inventory = merge_artifact_inventory(
                                    artifact_inventory,
                                    new_inventory_entries,
                                )
                                for entry in new_inventory_entries:
                                    name = str(entry.get("name") or "").strip().lower()
                                    if not name:
                                        continue
                                    recent_artifact_names.add(name)
                                    turn_artifact_names.add(name)
                    else:
                        error_code = ""
                        if isinstance(tool_result, dict):
                            raw_error_code = tool_result.get("error_code")
                            if (
                                isinstance(raw_error_code, str)
                                and raw_error_code.strip()
                            ):
                                error_code = raw_error_code.strip()

                        transition_reason = f"tool_result:{tool_name}_error"
                        if error_code:
                            transition_reason = f"{transition_reason}:{error_code}"

                        turn_state = await self._transition_state_and_emit(
                            current_state=turn_state,
                            to_state="error",
                            reason=transition_reason,
                            transitions=state_transitions,
                            worldline_id=active_worldline_id,
                            on_delta=on_delta,
                        )
                        turn_state = await self._transition_state_and_emit(
                            current_state=turn_state,
                            to_state="planning",
                            reason="recover_after_tool_error",
                            transitions=state_transitions,
                            worldline_id=active_worldline_id,
                            on_delta=on_delta,
                        )

                        if is_retryable_python_preflight_error(tool_result):
                            invalid_tool_payload_failures["run_python"] += 1
                            if (
                                invalid_tool_payload_failures["run_python"]
                                <= _MAX_INVALID_TOOL_PAYLOAD_RETRIES["run_python"]
                            ):
                                messages.append(
                                    ChatMessage(
                                        role="system",
                                        content=build_python_preflight_retry_message(
                                            tool_result=tool_result,
                                            data_intent_summary=data_intent_summary,
                                        ),
                                    )
                                )
                                invalid_payload_retry_requested = True
                                break

                            stopped_due_to_invalid_payload = True
                            final_text = (
                                "I stopped after repeated Python preflight failures "
                                "(syntax/tool-usage issues). I can continue once a valid "
                                "run_python call is emitted."
                            )
                            repeated_call_detected = True
                            break

                        # Execution error: Python ran but crashed - nudge to fix and retry
                        if (
                            tool_name == "run_python"
                            and tool_result.get("error")
                            and not is_retryable_python_preflight_error(tool_result)
                            and not is_empty_python_payload_error(tool_result)
                        ):
                            python_execution_error_count += 1
                            if (
                                python_execution_error_count
                                <= _MAX_PYTHON_EXECUTION_ERROR_RETRIES
                            ):
                                messages.append(
                                    ChatMessage(
                                        role="system",
                                        content=build_python_execution_retry_message(
                                            tool_result=tool_result,
                                            data_intent_summary=data_intent_summary,
                                        ),
                                    ),
                                )
                                python_execution_retry_requested = True
                                break
                            final_text = (
                                "Python execution failed repeatedly. Please try "
                                "rephrasing your request or simplifying the analysis."
                            )
                            repeated_call_detected = True
                            break
                if repeated_call_detected:
                    stopped_due_to_guard_stop = True
                    if final_text:
                        turn_state = await self._transition_state_and_emit(
                            current_state=turn_state,
                            to_state="presenting",
                            reason="guard_stop",
                            transitions=state_transitions,
                            worldline_id=active_worldline_id,
                            on_delta=on_delta,
                        )
                    break
                if invalid_payload_retry_requested or python_execution_retry_requested:
                    continue
                continue

            if response.text:
                if allow_tools:
                    missing_required_tools = find_missing_required_terminal_tools(
                        required_tools=required_tools,
                        sql_success_count=sql_success_count,
                        python_success_count=python_success_count,
                    )
                    if missing_required_tools:
                        required_tool_enforcement_failures += 1
                        turn_state = await self._transition_state_and_emit(
                            current_state=turn_state,
                            to_state="error",
                            reason=(
                                "required_tool_missing:"
                                + ",".join(sorted(missing_required_tools))
                            ),
                            transitions=state_transitions,
                            worldline_id=active_worldline_id,
                            on_delta=on_delta,
                        )

                        if (
                            required_tool_enforcement_failures
                            <= _MAX_REQUIRED_TOOL_ENFORCEMENT_RETRIES
                        ):
                            messages.append(
                                ChatMessage(
                                    role="system",
                                    content=build_required_tool_enforcement_message(
                                        missing_required_tools=missing_required_tools,
                                        data_intent_summary=data_intent_summary,
                                    ),
                                )
                            )
                            turn_state = await self._transition_state_and_emit(
                                current_state=turn_state,
                                to_state="planning",
                                reason="retry_after_missing_required_tool",
                                transitions=state_transitions,
                                worldline_id=active_worldline_id,
                                on_delta=on_delta,
                            )
                            continue

                        final_text = (
                            "I stopped because the model kept replying without required tool "
                            "execution ("
                            + ", ".join(sorted(missing_required_tools))
                            + ")."
                        )
                        turn_state = await self._transition_state_and_emit(
                            current_state=turn_state,
                            to_state="presenting",
                            reason="required_tool_missing_stop",
                            transitions=state_transitions,
                            worldline_id=active_worldline_id,
                            on_delta=on_delta,
                        )
                        break

                turn_state = await self._transition_state_and_emit(
                    current_state=turn_state,
                    to_state="presenting",
                    reason="assistant_text_ready",
                    transitions=state_transitions,
                    worldline_id=active_worldline_id,
                    on_delta=on_delta,
                )
                final_text = response.text
                break

            # The LLM returned no text and no tool calls.  This typically
            # happens when the conversation history confuses the model.
            # Retry once by continuing the loop (which makes a fresh LLM
            # call); give up on the second empty response.
            empty_response_retries += 1
            if empty_response_retries <= 1:
                logger.warning(
                    "Empty LLM response (no text, no tool_calls). "
                    "messages=%d, last_role=%s. Retrying (attempt %d).",
                    len(messages),
                    messages[-1].role if messages else "N/A",
                    empty_response_retries,
                )
                continue

            logger.warning(
                "Empty LLM response persisted after retry. "
                "messages=%d, last_role=%s. Giving up.",
                len(messages),
                messages[-1].role if messages else "N/A",
            )
            final_text = (
                "I wasn't able to generate a response for that request. "
                "Could you try rephrasing your question?"
            )
            turn_state = await self._transition_state_and_emit(
                current_state=turn_state,
                to_state="presenting",
                reason="empty_llm_response_fallback",
                transitions=state_transitions,
                worldline_id=active_worldline_id,
                on_delta=on_delta,
            )
            break

        if final_text is None:
            final_text = (
                "I reached the tool-loop limit before producing a final answer."
            )
            turn_state = await self._transition_state_and_emit(
                current_state=turn_state,
                to_state="presenting",
                reason="max_iterations_reached",
                transitions=state_transitions,
                worldline_id=active_worldline_id,
                on_delta=on_delta,
            )

        # Skip report fallback when we had invalid Python payloads - don't paper over failures
        had_invalid_python_payloads = (
            invalid_tool_payload_failures.get("run_python", 0) > 0
        )
        if (
            requested_output_type == "report"
            and not stopped_due_to_invalid_payload
            and not stopped_due_to_guard_stop
            and not had_invalid_python_payloads
        ):
            report_generated = await self._ensure_report_pdf_artifact(
                worldline_id=active_worldline_id,
                starting_rowid=starting_rowid_by_worldline[active_worldline_id],
                on_event=on_event,
            )
            if report_generated:
                final_text = f"{final_text}\n\nGenerated downloadable report artifact: report.pdf."

        turn_state = await self._transition_state_and_emit(
            current_state=turn_state,
            to_state="completed",
            reason="assistant_message_persisted",
            transitions=state_transitions,
            worldline_id=active_worldline_id,
            on_delta=on_delta,
        )

        assistant_payload = {
            "text": final_text,
            "state_trace": state_transitions,
            "state_final": turn_state,
            "turn_stats": {
                "required_tools": sorted(required_tools),
                "sql_success_count": sql_success_count,
                "python_success_count": python_success_count,
                "invalid_tool_payload_failures": invalid_tool_payload_failures,
                "required_tool_enforcement_failures": required_tool_enforcement_failures,
            },
        }
        assistant_event = self._append_worldline_event(
            worldline_id=active_worldline_id,
            event_type="assistant_message",
            payload=assistant_payload,
        )
        if on_event is not None:
            await on_event(active_worldline_id, assistant_event)

        events = self._events_since_rowid(
            worldline_id=active_worldline_id,
            rowid=starting_rowid_by_worldline[active_worldline_id],
        )

        return (
            active_worldline_id,
            events,
        )

    # ---- real-time streaming bridge -----------------------------------------

    async def _stream_llm_response(
        self,
        *,
        worldline_id: str,
        messages: list[ChatMessage],
        tools: list[ToolDefinition],
        on_delta: Callable[[str, dict[str, Any]], Awaitable[None]],
    ) -> LlmResponse:
        return await stream_llm_response(
            llm_client=self.llm_client,
            worldline_id=worldline_id,
            messages=messages,
            tools=tools,
            max_output_tokens=self.max_output_tokens,
            on_delta=on_delta,
        )

    def _tool_name_to_delta_type(self, tool_name: str) -> str | None:
        return tool_name_to_delta_type(tool_name)

    def _tool_definitions(
        self,
        *,
        include_python: bool = True,
        include_spawn_subagents: bool = True,
    ) -> list[ToolDefinition]:
        return tool_definitions(
            include_python=include_python,
            include_spawn_subagents=include_spawn_subagents,
        )

    async def _run_child_turn(
        self,
        child_worldline_id: str,
        child_message: str,
        child_max_iterations: int,
        subagent_depth: int,
        allow_tools: bool = True,
    ) -> tuple[str, list[dict[str, Any]]]:
        child_engine = ChatEngine(
            llm_client=self.llm_client,
            max_iterations=child_max_iterations,
            max_output_tokens=self.max_output_tokens,
            worldline_service=self.worldline_service,
        )
        return await child_engine.run_turn(
            worldline_id=child_worldline_id,
            message=child_message,
            allow_subagents=False,
            subagent_depth=subagent_depth,
            allow_tools=allow_tools,
        )

    # ---- tool execution -----------------------------------------------------

    async def _execute_tool_call(
        self,
        worldline_id: str,
        tool_call: ToolCall,
        carried_user_message: str,
        allowed_external_aliases: list[str] | None,
        on_event: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
        on_delta: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
        subagent_depth: int = 0,
    ) -> tuple[dict[str, Any], str | None]:
        return await self._get_tool_dispatcher().execute_tool_call(
            worldline_id=worldline_id,
            tool_call=tool_call,
            carried_user_message=carried_user_message,
            allowed_external_aliases=allowed_external_aliases,
            on_event=on_event,
            on_delta=on_delta,
            subagent_depth=subagent_depth,
        )

    async def _ensure_report_pdf_artifact(
        self,
        *,
        worldline_id: str,
        starting_rowid: int,
        on_event: Callable[[str, dict[str, Any]], Awaitable[None]] | None,
    ) -> bool:
        turn_events = self._events_since_rowid(
            worldline_id=worldline_id, rowid=starting_rowid
        )
        if events_contain_pdf_artifact(turn_events):
            return False

        if not assert_auto_report_code_compiles(logger):
            return False

        try:
            result = await execute_python_tool(
                PythonToolRequest(
                    worldline_id=worldline_id,
                    code=AUTO_REPORT_CODE,
                    timeout=90,
                    call_id="auto_report_pdf",
                ),
                on_event=(
                    None
                    if on_event is None
                    else lambda event: on_event(worldline_id, event)
                ),
            )
        except HTTPException as exc:
            logger.warning(
                "Auto report PDF generation failed with HTTPException: %s", exc.detail
            )
            return False
        except Exception as exc:  # pragma: no cover
            logger.warning("Auto report PDF generation failed: %s", exc)
            return False

        artifacts = result.get("artifacts") if isinstance(result, dict) else None
        if not isinstance(artifacts, list):
            return False
        return any(artifact_is_pdf(artifact) for artifact in artifacts)

    async def _load_worldline_events(self, worldline_id: str) -> list[dict[str, Any]]:
        timeline = await get_worldline_events(
            worldline_id=worldline_id,
            limit=250,
            cursor=None,
        )
        events = timeline.get("events", [])
        return list(events)

    async def _emit_state_transition_delta(
        self,
        *,
        worldline_id: str,
        transition: dict[str, Any],
        on_delta: Callable[[str, dict[str, Any]], Awaitable[None]] | None,
    ) -> None:
        if on_delta is None:
            return

        await on_delta(
            worldline_id,
            {
                "type": "state_transition",
                "from_state": transition.get("from"),
                "to_state": transition.get("to"),
                "reason": transition.get("reason"),
            },
        )

    async def _transition_state_and_emit(
        self,
        *,
        current_state: str,
        to_state: str,
        reason: str,
        transitions: list[dict[str, Any]],
        worldline_id: str,
        on_delta: Callable[[str, dict[str, Any]], Awaitable[None]] | None,
    ) -> str:
        before_len = len(transitions)
        next_state = self._transition_state(
            current_state=current_state,
            to_state=to_state,
            reason=reason,
            transitions=transitions,
            worldline_id=worldline_id,
        )

        if len(transitions) > before_len:
            await self._emit_state_transition_delta(
                worldline_id=worldline_id,
                transition=transitions[-1],
                on_delta=on_delta,
            )

        return next_state

    def _extract_artifact_names_from_python_code(self, code: str) -> set[str]:
        if not isinstance(code, str) or not code.strip():
            return set()

        names: set[str] = set()
        for match in _ARTIFACT_FILENAME_PATTERN.finditer(code):
            candidate = match.group(1).strip()
            if not candidate:
                continue
            if len(candidate) > 180:
                continue
            names.add(candidate.lower())
        return names

    def _transition_state(
        self,
        *,
        current_state: str,
        to_state: str,
        reason: str,
        transitions: list[dict[str, Any]],
        worldline_id: str,
    ) -> str:
        return transition_state(
            current_state=current_state,
            to_state=to_state,
            reason=reason,
            transitions=transitions,
            worldline_id=worldline_id,
            logger=logger,
            debug_log=_debug_log,
        )

    # ---- helpers ------------------------------------------------------------

    def _serialize_tool_result_for_context(
        self,
        *,
        tool_name: str,
        tool_result: dict[str, Any],
    ) -> str:
        payload = tool_result
        if tool_name == "spawn_subagents":
            payload = self._compact_subagent_result_payload(tool_result)

        serialized = json.dumps(
            payload,
            ensure_ascii=True,
            default=str,
        )
        if len(serialized) > 12_000:
            serialized = serialized[:12_000] + "...(truncated)"
        return serialized

    def _compact_subagent_result_payload(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return payload

        compact = dict(payload)
        raw_tasks = payload.get("tasks")
        if not isinstance(raw_tasks, list):
            return compact

        compact_tasks: list[dict[str, Any]] = []
        for task in raw_tasks[:50]:
            if not isinstance(task, dict):
                continue
            compact_tasks.append(
                {
                    "task_index": task.get("task_index"),
                    "task_label": task.get("task_label"),
                    "status": task.get("status"),
                    "child_worldline_id": task.get("child_worldline_id"),
                    "result_worldline_id": task.get("result_worldline_id"),
                    "assistant_preview": str(task.get("assistant_preview") or "")[:220],
                    "error": str(task.get("error") or "")[:500],
                }
            )

        compact["tasks"] = compact_tasks
        return compact

    def _build_subagent_partial_failure_nudge(
        self,
        payload: dict[str, Any],
    ) -> str | None:
        if not isinstance(payload, dict):
            return None
        if not bool(payload.get("partial_failure")):
            return None

        def _as_int(value: Any) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return 0

        completed_count = _as_int(payload.get("completed_count"))
        failed_count = _as_int(payload.get("failed_count"))
        timed_out_count = _as_int(payload.get("timed_out_count"))
        task_count = _as_int(payload.get("task_count"))

        raw_tasks = payload.get("tasks")
        task_rows = raw_tasks if isinstance(raw_tasks, list) else []

        completed_slices: list[str] = []
        failed_or_missing_slices: list[str] = []
        for index, task in enumerate(task_rows):
            if not isinstance(task, dict):
                continue
            label = str(task.get("task_label") or f"task-{index + 1}").strip()
            if not label:
                label = f"task-{index + 1}"

            status = str(task.get("status") or "").strip().lower()
            preview = str(task.get("assistant_preview") or "").strip()
            failure_code = str(task.get("failure_code") or "").strip()
            error = str(task.get("error") or "").strip()

            if status == "completed":
                snippet = preview[:160] if preview else "completed without preview"
                completed_slices.append(f"{label}: {snippet}")
                continue

            failure_parts: list[str] = []
            if status:
                failure_parts.append(f"status={status}")
            if failure_code:
                failure_parts.append(f"failure_code={failure_code}")
            if error:
                failure_parts.append(f"error={error[:220]}")
            if not failure_parts:
                failure_parts.append("no explicit failure details")
            failed_or_missing_slices.append(f"{label}: {'; '.join(failure_parts)}")

        missing_slice_count = max(0, task_count - len(task_rows))
        if missing_slice_count > 0:
            failed_or_missing_slices.append(
                f"{missing_slice_count} task result slice(s) missing from payload"
            )

        if not failed_or_missing_slices and (failed_count > 0 or timed_out_count > 0):
            failed_or_missing_slices.append(
                (
                    "Some slices failed or timed out but did not include per-task details: "
                    f"failed={failed_count}, timed_out={timed_out_count}"
                )
            )

        successful_summary = (
            " | ".join(completed_slices[:8]) if completed_slices else "none"
        )
        failed_summary = (
            " | ".join(failed_or_missing_slices[:8])
            if failed_or_missing_slices
            else "none"
        )

        return (
            "Subagent fan-out completed with partial failures.\n"
            "For your next response:\n"
            "1) Synthesize conclusions from successful task outputs only.\n"
            "2) Explicitly list missing or failed slices and what remains unknown.\n"
            "3) Do not imply failed/missing slices completed successfully.\n"
            f"Aggregate counts: completed={completed_count}, failed={failed_count}, "
            f"timed_out={timed_out_count}, task_count={task_count}.\n"
            f"Successful slices: {successful_summary}\n"
            f"Missing/failed slices: {failed_summary}"
        )

    def _append_worldline_event(
        self,
        *,
        worldline_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return append_worldline_event(
            worldline_id=worldline_id,
            event_type=event_type,
            payload=payload,
        )

    def _load_event_by_id(self, event_id: str) -> dict[str, Any]:
        return load_event_by_id(event_id)

    def _append_worldline_event_with_parent(
        self,
        *,
        worldline_id: str,
        parent_event_id: str | None,
        event_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return append_worldline_event_with_parent(
            worldline_id=worldline_id,
            parent_event_id=parent_event_id,
            event_type=event_type,
            payload=payload,
        )

    def _max_worldline_rowid(self, worldline_id: str) -> int:
        return max_worldline_rowid(worldline_id)

    def _events_since_rowid(
        self, *, worldline_id: str, rowid: int
    ) -> list[dict[str, Any]]:
        return events_since_rowid(worldline_id=worldline_id, rowid=rowid)
