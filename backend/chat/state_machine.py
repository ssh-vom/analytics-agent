from __future__ import annotations

import logging
from typing import Any, Callable

TURN_STATE_TRANSITIONS: dict[str, set[str]] = {
    "planning": {
        "semantic_shortcut",
        "data_fetching",
        "analyzing",
        "presenting",
        "completed",
        "error",
    },
    "semantic_shortcut": {"presenting", "completed", "error"},
    "data_fetching": {"analyzing", "presenting", "error", "completed"},
    "analyzing": {"data_fetching", "presenting", "error", "completed"},
    "presenting": {"analyzing", "error", "completed"},
    "error": {"planning", "completed"},
    "completed": set(),
}


def transition_state(
    *,
    current_state: str,
    to_state: str,
    reason: str,
    transitions: list[dict[str, Any]],
    worldline_id: str,
    logger: logging.Logger,
    debug_log: Callable[..., None] | None = None,
) -> str:
    if current_state == to_state:
        return current_state

    allowed = TURN_STATE_TRANSITIONS.get(current_state, set())
    if to_state not in allowed:
        logger.warning(
            "Invalid state transition: %s -> %s (%s)",
            current_state,
            to_state,
            reason,
        )
        transitions.append(
            {
                "from": current_state,
                "to": "error",
                "reason": f"invalid_transition:{current_state}->{to_state}:{reason}",
            }
        )
        if debug_log is not None:
            debug_log(
                run_id="initial",
                hypothesis_id="STATE_MACHINE_PHASE2",
                location="backend/chat/state_machine.py:transition_state:invalid",
                message="Invalid state transition attempted",
                data={
                    "worldline_id": worldline_id,
                    "from": current_state,
                    "to": to_state,
                    "reason": reason,
                },
            )
        return "error"

    transitions.append({"from": current_state, "to": to_state, "reason": reason})
    if debug_log is not None:
        debug_log(
            run_id="initial",
            hypothesis_id="STATE_MACHINE_PHASE2",
            location="backend/chat/state_machine.py:transition_state",
            message="State transition",
            data={
                "worldline_id": worldline_id,
                "from": current_state,
                "to": to_state,
                "reason": reason,
            },
        )
    return to_state
