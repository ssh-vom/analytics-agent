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
from chat.policy import (
    missing_required_terminal_tools as find_missing_required_terminal_tools,
    required_terminal_tools as determine_required_terminal_tools,
    user_requested_rerun as user_requested_rerun_hint,
)
from chat.report_fallback import (
    AUTO_REPORT_CODE,
    artifact_is_pdf,
    assert_auto_report_code_compiles,
    events_contain_pdf_artifact,
)
from chat.state_machine import transition_state
from chat.streaming_bridge import stream_llm_response
from chat.tooling import (
    normalize_tool_arguments,
    tool_definitions,
    tool_name_to_delta_type,
    tool_signature,
)
from tools import (
    PythonToolRequest,
    SqlToolRequest,
    execute_python_tool,
    execute_sql_tool,
)
from worldlines import get_worldline_events
from worldline_service import BranchOptions, WorldlineService

logger = logging.getLogger(__name__)

_ARTIFACT_INVENTORY_HEADER = "Artifact inventory for this worldline"
_DATA_INTENT_HEADER = "SQL-to-Python data checkpoint"
_ARTIFACT_INVENTORY_MAX_ITEMS = 40
_RECENT_SIGNATURE_WINDOW = 24
_MAX_INVALID_TOOL_PAYLOAD_RETRIES: dict[str, int] = {
    "run_sql": 2,
    "run_python": 3,
}
_ARTIFACT_FILENAME_PATTERN = re.compile(
    r"['\"]([^'\"\n\r]+\.(?:csv|png|jpg|jpeg|pdf|svg|json|parquet|xlsx|html))['\"]",
    re.IGNORECASE,
)
_MAX_REQUIRED_TOOL_ENFORCEMENT_RETRIES = 2


@dataclass
class ChatEngine:
    llm_client: LlmClient
    max_iterations: int = 20
    max_output_tokens: int | None = 1500
    worldline_service: WorldlineService = field(default_factory=WorldlineService)

    async def run_turn(
        self,
        worldline_id: str,
        message: str,
        on_event: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
        on_delta: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
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
        artifact_inventory = self._artifact_inventory_from_events(history_events)
        data_intent_summary = self._data_intent_from_events(history_events)
        recent_successful_tool_signatures = self._recent_successful_tool_signatures(
            worldline_id=active_worldline_id,
            events=history_events,
            limit=_RECENT_SIGNATURE_WINDOW,
        )
        recent_artifact_names = self._artifact_name_set(artifact_inventory)
        user_requested_rerun_flag = user_requested_rerun_hint(message)
        required_tools = determine_required_terminal_tools(
            message=message,
            requested_output_type=requested_output_type,
        )
        # #region agent log
        _debug_log(
            run_id="initial",
            hypothesis_id="H6_H7",
            location="backend/chat/engine.py:run_turn:built_messages",
            message="Built LLM messages for turn",
            data={
                "worldline_id": active_worldline_id,
                "message_count": len(messages),
                "tail": [
                    {
                        "role": msg.role,
                        "content_preview": (msg.content or "")[:140],
                        "has_tool_calls": bool(msg.tool_calls),
                        "tool_call_count": len(msg.tool_calls or []),
                    }
                    for msg in messages[-6:]
                ],
            },
        )
        # #endregion
        final_text: str | None = None
        successful_tool_signatures: set[str] = set()
        successful_tool_results: dict[str, dict[str, Any]] = {}
        turn_artifact_names: set[str] = set()
        sql_success_count = 0
        python_success_count = 0
        empty_response_retries = 0
        required_tool_enforcement_failures = 0
        invalid_tool_payload_failures: dict[str, int] = {
            "run_sql": 0,
            "run_python": 0,
        }
        turn_state = "planning"
        state_transitions: list[dict[str, Any]] = [
            {"from": None, "to": "planning", "reason": "turn_started"}
        ]
        await self._emit_state_transition_delta(
            worldline_id=active_worldline_id,
            transition=state_transitions[0],
            on_delta=on_delta,
        )

        for _ in range(self.max_iterations):
            self._upsert_artifact_inventory_message(messages, artifact_inventory)
            self._upsert_data_intent_message(messages, data_intent_summary)
            # ----- LLM call: stream when on_delta is available, else batch -----
            if on_delta is not None:
                response = await self._stream_llm_response(
                    worldline_id=active_worldline_id,
                    messages=messages,
                    tools=self._tool_definitions(include_python=True),
                    on_delta=on_delta,
                )
            else:
                response = await self.llm_client.generate(
                    messages=messages,
                    tools=self._tool_definitions(include_python=True),
                    max_output_tokens=self.max_output_tokens,
                )

            # ----- Emit assistant_plan if text accompanies tool calls ----------
            if response.text and response.tool_calls:
                plan_event = self._append_worldline_event(
                    worldline_id=active_worldline_id,
                    event_type="assistant_plan",
                    payload={"text": response.text},
                )
                if on_event is not None:
                    await on_event(active_worldline_id, plan_event)

            if response.text:
                messages.append(ChatMessage(role="assistant", content=response.text))

            if response.tool_calls:
                repeated_call_detected = False
                invalid_payload_retry_requested = False
                for raw_tool_call in response.tool_calls:
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
                    _debug_log(
                        run_id="initial",
                        hypothesis_id="H3_H4",
                        location="backend/chat/engine.py:run_turn:tool_call",
                        message="Model emitted tool call",
                        data={
                            "worldline_id": active_worldline_id,
                            "tool_name": tool_name,
                            "tool_call_id": tool_call.id,
                            "tool_args_preview": json.dumps(
                                tool_call.arguments or {},
                                ensure_ascii=True,
                                default=str,
                            )[:220],
                        },
                    )
                    # #endregion

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

                    payload_error = self._validate_tool_payload(
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
                                    content=self._build_tool_payload_correction_message(
                                        tool_name=tool_name,
                                        payload_error=payload_error,
                                        data_intent_summary=data_intent_summary,
                                    ),
                                )
                            )
                            invalid_payload_retry_requested = True
                            break

                        if tool_name == "run_python":
                            final_text = (
                                "I stopped after repeated invalid Python tool payloads "
                                "(empty/invalid `code`). I can continue once the model emits "
                                "a valid run_python call."
                            )
                        else:
                            final_text = (
                                "I stopped after repeated invalid SQL tool payloads "
                                "(empty/invalid `sql`). I can continue once the model emits "
                                "a valid run_sql call."
                            )
                        repeated_call_detected = True
                        break

                    signature = self._tool_signature(
                        worldline_id=active_worldline_id,
                        tool_call=tool_call,
                    )
                    if signature in successful_tool_signatures:
                        if on_delta is not None and delta_type is not None:
                            await on_delta(
                                active_worldline_id,
                                {
                                    "type": delta_type,
                                    "call_id": tool_call.id or None,
                                    "skipped": True,
                                    "reason": "repeated_identical_tool_call",
                                },
                            )
                        final_text = (
                            "I stopped because the model repeated the same tool call "
                            "with identical arguments in this turn."
                        )
                        repeated_call_detected = True
                        break

                    if (
                        not user_requested_rerun_flag
                        and signature in recent_successful_tool_signatures
                    ):
                        if on_delta is not None and delta_type is not None:
                            await on_delta(
                                active_worldline_id,
                                {
                                    "type": delta_type,
                                    "call_id": tool_call.id or None,
                                    "skipped": True,
                                    "reason": "recent_identical_successful_tool_call",
                                },
                            )
                        final_text = (
                            "I skipped this tool call because an identical successful "
                            "call already ran recently in this worldline. "
                            "If you want a fresh rerun, ask me to rerun or overwrite it."
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
                    await asyncio.sleep(0.4)

                    tool_result, switched_worldline_id = await self._execute_tool_call(
                        worldline_id=active_worldline_id,
                        tool_call=tool_call,
                        carried_user_message=message,
                        allowed_external_aliases=allowed_external_aliases,
                        on_event=on_event,
                    )
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
                        artifact_inventory = self._artifact_inventory_from_events(
                            history_events
                        )
                        data_intent_summary = self._data_intent_from_events(
                            history_events
                        )
                        recent_successful_tool_signatures = (
                            self._recent_successful_tool_signatures(
                                worldline_id=active_worldline_id,
                                events=history_events,
                                limit=_RECENT_SIGNATURE_WINDOW,
                            )
                        )
                        recent_artifact_names = self._artifact_name_set(
                            artifact_inventory
                        )
                        turn_artifact_names.clear()
                        # Reset per-turn state for the new worldline context
                        sql_success_count = 0
                        python_success_count = 0
                        successful_tool_signatures.clear()
                        turn_state = await self._transition_state_and_emit(
                            current_state=turn_state,
                            to_state="planning",
                            reason="worldline_switched",
                            transitions=state_transitions,
                            worldline_id=active_worldline_id,
                            on_delta=on_delta,
                        )

                    serialized = json.dumps(
                        tool_result,
                        ensure_ascii=True,
                        default=str,
                    )
                    if len(serialized) > 12_000:
                        serialized = serialized[:12_000] + "...(truncated)"
                    messages.append(
                        ChatMessage(
                            role="tool",
                            content=serialized,
                            tool_call_id=tool_call.id or None,
                        )
                    )

                    if (
                        tool_name == "run_python"
                        and self._is_empty_python_payload_error(tool_result)
                    ):
                        invalid_tool_payload_failures["run_python"] += 1
                        if (
                            invalid_tool_payload_failures["run_python"]
                            <= _MAX_INVALID_TOOL_PAYLOAD_RETRIES["run_python"]
                        ):
                            messages.append(
                                ChatMessage(
                                    role="system",
                                    content=self._build_tool_payload_correction_message(
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
                            final_text = (
                                "I stopped after repeated invalid Python tool payloads "
                                "(empty/invalid `code`). I'll continue once a valid Python "
                                "tool call is provided."
                            )
                            repeated_call_detected = True
                            break

                    if not tool_result.get("error"):
                        successful_tool_signatures.add(signature)
                        recent_successful_tool_signatures.add(signature)
                        successful_tool_results[signature] = tool_result
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
                            data_intent_summary = self._build_data_intent_summary(
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
                            new_inventory_entries = (
                                self._artifact_inventory_from_tool_result(
                                    tool_result,
                                    source_call_id=tool_call.id,
                                    producer="run_python",
                                )
                            )
                            if new_inventory_entries:
                                artifact_inventory = self._merge_artifact_inventory(
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

                        if self._is_retryable_python_preflight_error(tool_result):
                            invalid_tool_payload_failures["run_python"] += 1
                            if (
                                invalid_tool_payload_failures["run_python"]
                                <= _MAX_INVALID_TOOL_PAYLOAD_RETRIES["run_python"]
                            ):
                                messages.append(
                                    ChatMessage(
                                        role="system",
                                        content=self._build_python_preflight_retry_message(
                                            tool_result=tool_result,
                                            data_intent_summary=data_intent_summary,
                                        ),
                                    )
                                )
                                invalid_payload_retry_requested = True
                                break

                            final_text = (
                                "I stopped after repeated Python preflight failures "
                                "(syntax/tool-usage issues). I can continue once a valid "
                                "run_python call is emitted."
                            )
                            repeated_call_detected = True
                            break
                if repeated_call_detected:
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
                if invalid_payload_retry_requested:
                    continue
                continue

            if response.text:
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
                                content=self._build_required_tool_enforcement_message(
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
                        "execution (" + ", ".join(sorted(missing_required_tools)) + ")."
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

        if requested_output_type == "report":
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
        _debug_log(
            run_id="initial",
            hypothesis_id="STATE_MACHINE_PHASE2",
            location="backend/chat/engine.py:run_turn:state_transitions",
            message="Completed turn with explicit state transitions",
            data={
                "worldline_id": active_worldline_id,
                "transitions": state_transitions,
                "final_state": turn_state,
                "sql_success_count": sql_success_count,
                "python_success_count": python_success_count,
                "required_tools": sorted(required_tools),
                "required_tool_enforcement_failures": required_tool_enforcement_failures,
                "invalid_tool_payload_failures": invalid_tool_payload_failures,
            },
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

    def _tool_definitions(self, *, include_python: bool = True) -> list[ToolDefinition]:
        return tool_definitions(include_python=include_python)

    # ---- tool execution -----------------------------------------------------

    async def _execute_tool_call(
        self,
        worldline_id: str,
        tool_call: ToolCall,
        carried_user_message: str,
        allowed_external_aliases: list[str] | None,
        on_event: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    ) -> tuple[dict[str, Any], str | None]:
        name = (tool_call.name or "").strip()
        args = tool_call.arguments or {}

        if name == "run_sql":
            sql = args.get("sql")
            if not isinstance(sql, str) or not sql.strip():
                return {"error": "run_sql requires a non-empty 'sql' string"}, None

            raw_limit = args.get("limit", 100)
            try:
                limit = int(raw_limit)
            except (TypeError, ValueError):
                limit = 100
            limit = max(1, min(limit, 10_000))

            try:
                result = await execute_sql_tool(
                    SqlToolRequest(
                        worldline_id=worldline_id,
                        sql=sql,
                        limit=limit,
                        allowed_external_aliases=allowed_external_aliases,
                        call_id=tool_call.id or None,
                    ),
                    on_event=(
                        None
                        if on_event is None
                        else lambda event: on_event(worldline_id, event)
                    ),
                )
                return result, None
            except HTTPException as exc:
                return {"error": str(exc.detail), "status_code": exc.status_code}, None
            except Exception as exc:  # pragma: no cover
                return {"error": str(exc)}, None

        if name == "run_python":
            code = args.get("code")
            if not isinstance(code, str) or not code.strip():
                # #region agent log
                _debug_log(
                    run_id="initial",
                    hypothesis_id="H8",
                    location="backend/chat/engine.py:_execute_tool_call:run_python_invalid_args",
                    message="run_python call missing/invalid code argument",
                    data={
                        "worldline_id": worldline_id,
                        "call_id": tool_call.id,
                        "args_keys": sorted(list(args.keys())),
                        "args_preview": json.dumps(
                            args, ensure_ascii=True, default=str
                        )[:220],
                    },
                )
                # #endregion
                return {"error": "run_python requires a non-empty 'code' string"}, None

            raw_timeout = args.get("timeout", 30)
            try:
                timeout = int(raw_timeout)
            except (TypeError, ValueError):
                timeout = 30
            timeout = max(1, min(timeout, 120))

            try:
                result = await execute_python_tool(
                    PythonToolRequest(
                        worldline_id=worldline_id,
                        code=code,
                        timeout=timeout,
                        call_id=tool_call.id or None,
                    ),
                    on_event=(
                        None
                        if on_event is None
                        else lambda event: on_event(worldline_id, event)
                    ),
                )
                return result, None
            except HTTPException as exc:
                if isinstance(exc.detail, dict):
                    result_payload: dict[str, Any] = dict(exc.detail)
                    if "status_code" not in result_payload:
                        result_payload["status_code"] = exc.status_code
                    return result_payload, None
                return {"error": str(exc.detail), "status_code": exc.status_code}, None
            except Exception as exc:  # pragma: no cover
                return {"error": str(exc)}, None

        if name == "time_travel":
            from_event_id = args.get("from_event_id")
            if not isinstance(from_event_id, str) or not from_event_id.strip():
                return {"error": "time_travel requires 'from_event_id'"}, None

            name_arg = args.get("name")
            branch_name = name_arg if isinstance(name_arg, str) and name_arg else None

            try:
                branch_result = self.worldline_service.branch_from_event(
                    BranchOptions(
                        source_worldline_id=worldline_id,
                        from_event_id=from_event_id,
                        name=branch_name,
                        append_events=True,
                        carried_user_message=carried_user_message,
                    )
                )
                if on_event is not None:
                    for event_id in branch_result.created_event_ids:
                        event = self._load_event_by_id(event_id)
                        await on_event(branch_result.new_worldline_id, event)
                return branch_result.to_tool_result(), branch_result.new_worldline_id
            except HTTPException as exc:
                return {"error": str(exc.detail), "status_code": exc.status_code}, None
            except Exception as exc:  # pragma: no cover
                return {"error": str(exc)}, None

        return {"error": f"unknown tool '{name}'"}, None

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

    def _artifact_inventory_from_events(
        self, events: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        by_id = {event.get("id"): event for event in events}
        deduped_by_name: dict[str, dict[str, Any]] = {}

        for event in events:
            if event.get("type") != "tool_result_python":
                continue

            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue

            artifacts = payload.get("artifacts")
            if not isinstance(artifacts, list):
                continue

            parent = by_id.get(event.get("parent_event_id")) or {}
            parent_payload = parent.get("payload") if isinstance(parent, dict) else {}
            source_call_id = None
            if isinstance(parent_payload, dict):
                raw_call_id = parent_payload.get("call_id")
                if isinstance(raw_call_id, str) and raw_call_id.strip():
                    source_call_id = raw_call_id.strip()

            created_at = str(event.get("created_at") or "")
            source_event_id = str(event.get("id") or "")

            for artifact in artifacts:
                if not isinstance(artifact, dict):
                    continue
                name = str(artifact.get("name") or "").strip()
                if not name:
                    continue

                key = name.lower()
                entry = {
                    "artifact_id": str(artifact.get("artifact_id") or ""),
                    "name": name,
                    "type": str(artifact.get("type") or "file"),
                    "created_at": created_at,
                    "source_call_id": source_call_id,
                    "source_event_id": source_event_id,
                    "producer": "run_python",
                }
                if key in deduped_by_name:
                    del deduped_by_name[key]
                deduped_by_name[key] = entry

        inventory = list(deduped_by_name.values())
        if len(inventory) > _ARTIFACT_INVENTORY_MAX_ITEMS:
            inventory = inventory[-_ARTIFACT_INVENTORY_MAX_ITEMS:]
        return inventory

    def _artifact_inventory_from_tool_result(
        self,
        tool_result: dict[str, Any],
        *,
        source_call_id: str | None,
        producer: str,
    ) -> list[dict[str, Any]]:
        artifacts = tool_result.get("artifacts")
        if not isinstance(artifacts, list):
            return []

        inventory: list[dict[str, Any]] = []
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            name = str(artifact.get("name") or "").strip()
            if not name:
                continue
            inventory.append(
                {
                    "artifact_id": str(artifact.get("artifact_id") or ""),
                    "name": name,
                    "type": str(artifact.get("type") or "file"),
                    "created_at": "",
                    "source_call_id": source_call_id,
                    "source_event_id": "",
                    "producer": producer,
                }
            )
        return inventory

    def _merge_artifact_inventory(
        self,
        existing: list[dict[str, Any]],
        incoming: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        deduped_by_name: dict[str, dict[str, Any]] = {}

        for entry in [*existing, *incoming]:
            name = str(entry.get("name") or "").strip()
            if not name:
                continue
            key = name.lower()
            normalized_entry = dict(entry)
            normalized_entry["name"] = name
            if key in deduped_by_name:
                del deduped_by_name[key]
            deduped_by_name[key] = normalized_entry

        merged = list(deduped_by_name.values())
        if len(merged) > _ARTIFACT_INVENTORY_MAX_ITEMS:
            merged = merged[-_ARTIFACT_INVENTORY_MAX_ITEMS:]
        return merged

    def _artifact_name_set(self, inventory: list[dict[str, Any]]) -> set[str]:
        return {
            str(entry.get("name") or "").strip().lower()
            for entry in inventory
            if str(entry.get("name") or "").strip()
        }

    def _render_artifact_inventory_message(
        self, artifact_inventory: list[dict[str, Any]]
    ) -> str:
        payload = {
            "artifact_count": len(artifact_inventory),
            "artifacts": artifact_inventory,
            "instructions": (
                "Check this inventory before creating files. Reuse existing artifacts "
                "instead of regenerating identical outputs."
            ),
        }
        return f"{_ARTIFACT_INVENTORY_HEADER} (always-on memory):\n" + json.dumps(
            payload, ensure_ascii=True, default=str
        )

    def _upsert_artifact_inventory_message(
        self,
        messages: list[ChatMessage],
        artifact_inventory: list[dict[str, Any]],
    ) -> None:
        content = self._render_artifact_inventory_message(artifact_inventory)
        memory_message = ChatMessage(role="system", content=content)
        for index, message in enumerate(messages):
            if message.role == "system" and message.content.startswith(
                _ARTIFACT_INVENTORY_HEADER
            ):
                messages[index] = memory_message
                return

        insert_index = 1 if messages else 0
        messages.insert(insert_index, memory_message)

    def _data_intent_from_events(
        self,
        events: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        by_id = {event.get("id"): event for event in events}

        for event in reversed(events):
            if event.get("type") != "tool_result_sql":
                continue

            payload = event.get("payload")
            if not isinstance(payload, dict) or payload.get("error"):
                continue

            parent = by_id.get(event.get("parent_event_id"))
            parent_payload = parent.get("payload") if isinstance(parent, dict) else None
            sql = None
            if isinstance(parent_payload, dict):
                raw_sql = parent_payload.get("sql")
                if isinstance(raw_sql, str) and raw_sql.strip():
                    sql = raw_sql.strip()

            return self._build_data_intent_summary(sql=sql, sql_result=payload)

        return None

    def _build_data_intent_summary(
        self,
        *,
        sql: Any,
        sql_result: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not isinstance(sql_result, dict) or sql_result.get("error"):
            return None

        columns_meta = sql_result.get("columns")
        if not isinstance(columns_meta, list):
            columns_meta = []

        columns: list[str] = []
        dimensions: list[str] = []
        measures: list[str] = []

        for column in columns_meta:
            if not isinstance(column, dict):
                continue

            name = str(column.get("name") or "").strip()
            if not name:
                continue
            columns.append(name)

            col_type = str(column.get("type") or "")
            if self._is_numeric_sql_type(col_type):
                measures.append(name)
            else:
                dimensions.append(name)

        rows = sql_result.get("rows")
        row_count = sql_result.get("row_count")
        if not isinstance(row_count, int) or row_count < 0:
            row_count = len(rows) if isinstance(rows, list) else 0

        preview_count = sql_result.get("preview_count")
        if not isinstance(preview_count, int) or preview_count < 0:
            preview_count = len(rows) if isinstance(rows, list) else 0

        time_columns = [
            name
            for name in columns
            if re.search(
                r"(date|time|month|year|day|week|quarter)", name, re.IGNORECASE
            )
        ]

        sql_preview = ""
        if isinstance(sql, str) and sql.strip():
            sql_preview = " ".join(sql.strip().split())
            if len(sql_preview) > 220:
                sql_preview = sql_preview[:220] + "..."

        return {
            "source": "latest_successful_sql",
            "row_count": row_count,
            "preview_count": preview_count,
            "columns": columns[:24],
            "dimensions": dimensions[:16],
            "measures": measures[:16],
            "time_columns": time_columns[:8],
            "sql_preview": sql_preview,
        }

    def _is_numeric_sql_type(self, type_name: str) -> bool:
        if not isinstance(type_name, str):
            return False
        lowered = type_name.strip().lower()
        return any(
            token in lowered
            for token in (
                "int",
                "decimal",
                "double",
                "float",
                "real",
                "numeric",
                "hugeint",
            )
        )

    def _render_data_intent_message(
        self,
        data_intent_summary: dict[str, Any],
    ) -> str:
        payload = {
            "data_intent": data_intent_summary,
            "instructions": (
                "Use this checkpoint when planning follow-up SQL/Python steps. "
                "If Python is needed, reference LATEST_SQL_RESULT/LATEST_SQL_DF "
                "instead of refetching identical data."
            ),
        }
        return f"{_DATA_INTENT_HEADER} (always-on memory):\n" + json.dumps(
            payload,
            ensure_ascii=True,
            default=str,
        )

    def _upsert_data_intent_message(
        self,
        messages: list[ChatMessage],
        data_intent_summary: dict[str, Any] | None,
    ) -> None:
        existing_index = None
        for index, message in enumerate(messages):
            if message.role == "system" and message.content.startswith(
                _DATA_INTENT_HEADER
            ):
                existing_index = index
                break

        if data_intent_summary is None:
            if existing_index is not None:
                del messages[existing_index]
            return

        memory_message = ChatMessage(
            role="system",
            content=self._render_data_intent_message(data_intent_summary),
        )
        if existing_index is not None:
            messages[existing_index] = memory_message
            return

        insert_index = 2 if len(messages) >= 2 else len(messages)
        messages.insert(insert_index, memory_message)

    def _python_code_has_executable_content(self, code: str) -> bool:
        if not isinstance(code, str):
            return False
        for raw_line in code.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("#"):
                continue
            return True
        return False

    def _validate_tool_payload(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> str | None:
        if tool_name == "run_sql":
            sql = arguments.get("sql")
            if not isinstance(sql, str) or not sql.strip():
                return "run_sql requires a non-empty `sql` string"
            return None

        if tool_name == "run_python":
            code = arguments.get("code")
            if not isinstance(code, str) or not code.strip():
                return "run_python requires a non-empty `code` string"
            if not self._python_code_has_executable_content(code):
                return (
                    "run_python `code` must include executable Python and cannot be "
                    "comments/whitespace only"
                )
            return None

        return None

    def _build_tool_payload_correction_message(
        self,
        *,
        tool_name: str,
        payload_error: str,
        data_intent_summary: dict[str, Any] | None,
    ) -> str:
        if tool_name == "run_python":
            example_args = json.dumps(
                {
                    "code": "import pandas as pd\nprint(LATEST_SQL_DF.head())",
                    "timeout": 30,
                },
                ensure_ascii=True,
            )
            checkpoint = (
                json.dumps(data_intent_summary, ensure_ascii=True, default=str)
                if data_intent_summary
                else "none"
            )
            if len(checkpoint) > 900:
                checkpoint = checkpoint[:900] + "...(truncated)"
            return (
                "Correction: your last run_python payload was invalid ("
                + payload_error
                + "). Emit a fresh run_python tool call now with a non-empty executable `code` "
                "string and optional integer `timeout`. "
                "Do not emit empty args, nested JSON-in-code, or comments-only code. "
                "Example args: " + example_args + ". SQL checkpoint: " + checkpoint
            )

        if tool_name == "run_sql":
            example_args = json.dumps(
                {"sql": "SELECT * FROM your_table LIMIT 50", "limit": 50},
                ensure_ascii=True,
            )
            return (
                "Correction: your last run_sql payload was invalid ("
                + payload_error
                + "). Emit a fresh run_sql tool call with a non-empty `sql` string. "
                "Example args: " + example_args
            )

        return (
            "Correction: your previous tool payload was invalid ("
            + payload_error
            + "). Emit a valid tool call payload next."
        )

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

    def _recent_successful_tool_signatures(
        self,
        *,
        worldline_id: str,
        events: list[dict[str, Any]],
        limit: int,
    ) -> set[str]:
        if limit <= 0:
            return set()

        by_id = {event.get("id"): event for event in events}
        signatures: set[str] = set()

        for event in reversed(events):
            event_type = event.get("type")
            if event_type not in {"tool_result_sql", "tool_result_python"}:
                continue

            payload = event.get("payload")
            if not isinstance(payload, dict) or payload.get("error"):
                continue

            parent = by_id.get(event.get("parent_event_id"))
            if not isinstance(parent, dict):
                continue

            parent_type = parent.get("type")
            if parent_type == "tool_call_sql":
                tool_name = "run_sql"
            elif parent_type == "tool_call_python":
                tool_name = "run_python"
            else:
                continue

            parent_payload = parent.get("payload")
            if not isinstance(parent_payload, dict):
                continue

            args = dict(parent_payload)
            args.pop("call_id", None)
            normalized_args = normalize_tool_arguments(tool_name, args)
            signature = self._tool_signature(
                worldline_id=worldline_id,
                tool_call=ToolCall(
                    id=str(parent.get("id") or ""),
                    name=tool_name,
                    arguments=normalized_args,
                ),
            )
            signatures.add(signature)
            if len(signatures) >= limit:
                break

        return signatures

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

    def _build_required_tool_enforcement_message(
        self,
        *,
        missing_required_tools: set[str],
        data_intent_summary: dict[str, Any] | None,
    ) -> str:
        missing_sorted = sorted(missing_required_tools)
        if missing_sorted == ["run_python"]:
            checkpoint = (
                json.dumps(data_intent_summary, ensure_ascii=True, default=str)
                if data_intent_summary
                else "none"
            )
            if len(checkpoint) > 900:
                checkpoint = checkpoint[:900] + "...(truncated)"
            return (
                "Your previous reply skipped required Python execution. "
                "Emit a run_python tool call now with non-empty executable `code` (not a plan). "
                "Use LATEST_SQL_RESULT/LATEST_SQL_DF and the SQL checkpoint when relevant. "
                "SQL checkpoint: " + checkpoint
            )

        return (
            "Your previous reply skipped required tool execution ("
            + ", ".join(missing_sorted)
            + "). Emit the required tool call(s) now before finalizing."
        )

    def _is_retryable_python_preflight_error(
        self,
        tool_result: dict[str, Any],
    ) -> bool:
        if not isinstance(tool_result, dict):
            return False

        if tool_result.get("retryable") is not True:
            return False

        error_code = str(tool_result.get("error_code") or "").strip().lower()
        return error_code in {
            "python_compile_error",
            "python_execution_payload_compile_error",
            "python_tool_invocation_forbidden",
        }

    def _build_python_preflight_retry_message(
        self,
        *,
        tool_result: dict[str, Any],
        data_intent_summary: dict[str, Any] | None,
    ) -> str:
        error_code = str(tool_result.get("error_code") or "python_preflight_error")
        error_text = str(tool_result.get("error") or "Python preflight failed")
        checkpoint = (
            json.dumps(data_intent_summary, ensure_ascii=True, default=str)
            if data_intent_summary
            else "none"
        )
        if len(checkpoint) > 900:
            checkpoint = checkpoint[:900] + "...(truncated)"

        return (
            "Correction: the previous run_python execution failed preflight "
            f"({error_code}: {error_text}). Emit a fresh run_python tool call with "
            "valid executable Python in `code` (not nested JSON, not comments-only, and "
            "no backend tool function calls). Use LATEST_SQL_RESULT/LATEST_SQL_DF as input. "
            "SQL checkpoint: " + checkpoint
        )

    def _is_empty_python_payload_error(self, tool_result: dict[str, Any]) -> bool:
        error = tool_result.get("error") if isinstance(tool_result, dict) else None
        if not isinstance(error, str):
            return False
        lowered = error.lower()
        return "non-empty 'code'" in lowered or "empty `code`" in lowered

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

    def _tool_signature(self, *, worldline_id: str, tool_call: ToolCall) -> str:
        return tool_signature(
            worldline_id=worldline_id,
            tool_call=tool_call,
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

    def _max_worldline_rowid(self, worldline_id: str) -> int:
        return max_worldline_rowid(worldline_id)

    def _events_since_rowid(
        self, *, worldline_id: str, rowid: int
    ) -> list[dict[str, Any]]:
        return events_since_rowid(worldline_id=worldline_id, rowid=rowid)
