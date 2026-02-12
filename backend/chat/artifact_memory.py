from __future__ import annotations

import json
from typing import Any

from chat.llm_client import ChatMessage

ARTIFACT_INVENTORY_HEADER = "Artifact inventory for this worldline"
ARTIFACT_INVENTORY_MAX_ITEMS = 40


def artifact_inventory_from_events(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build artifact inventory from worldline events."""
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
    if len(inventory) > ARTIFACT_INVENTORY_MAX_ITEMS:
        inventory = inventory[-ARTIFACT_INVENTORY_MAX_ITEMS:]
    return inventory


def artifact_inventory_from_tool_result(
    tool_result: dict[str, Any],
    *,
    source_call_id: str | None,
    producer: str,
) -> list[dict[str, Any]]:
    """Extract artifact inventory entries from a single tool result."""
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


def merge_artifact_inventory(
    existing: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge two artifact inventories, deduplicating by name."""
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
    if len(merged) > ARTIFACT_INVENTORY_MAX_ITEMS:
        merged = merged[-ARTIFACT_INVENTORY_MAX_ITEMS:]
    return merged


def artifact_name_set(inventory: list[dict[str, Any]]) -> set[str]:
    """Get set of artifact names from inventory."""
    return {
        str(entry.get("name") or "").strip().lower()
        for entry in inventory
        if str(entry.get("name") or "").strip()
    }


def render_artifact_inventory_message(artifact_inventory: list[dict[str, Any]]) -> str:
    """Render artifact inventory as a system message."""
    payload = {
        "artifact_count": len(artifact_inventory),
        "artifacts": artifact_inventory,
        "instructions": (
            "Check this inventory before creating files. Reuse existing artifacts "
            "instead of regenerating identical outputs."
        ),
    }
    return f"{ARTIFACT_INVENTORY_HEADER} (always-on memory):\n" + json.dumps(
        payload, ensure_ascii=True, default=str
    )


def upsert_artifact_inventory_message(
    messages: list[ChatMessage],
    artifact_inventory: list[dict[str, Any]],
) -> None:
    """Insert or replace artifact inventory message in the message list."""
    content = render_artifact_inventory_message(artifact_inventory)
    memory_message = ChatMessage(role="system", content=content)
    for index, message in enumerate(messages):
        if message.role == "system" and message.content.startswith(
            ARTIFACT_INVENTORY_HEADER
        ):
            messages[index] = memory_message
            return

    insert_index = 1 if messages else 0
    messages.insert(insert_index, memory_message)
