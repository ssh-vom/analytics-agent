from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from chat.event_store import append_worldline_event, append_worldline_event_with_parent


async def append_event_with_callback(
    *,
    worldline_id: str,
    event_type: str,
    payload: dict[str, Any],
    on_event: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    event = append_worldline_event(
        worldline_id=worldline_id,
        event_type=event_type,
        payload=payload,
    )
    if on_event is not None:
        await on_event(worldline_id, event)
    return event


async def append_event_with_parent_callback(
    *,
    worldline_id: str,
    parent_event_id: str | None,
    event_type: str,
    payload: dict[str, Any],
    on_event: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    event = append_worldline_event_with_parent(
        worldline_id=worldline_id,
        parent_event_id=parent_event_id,
        event_type=event_type,
        payload=payload,
    )
    if on_event is not None:
        await on_event(worldline_id, event)
    return event
