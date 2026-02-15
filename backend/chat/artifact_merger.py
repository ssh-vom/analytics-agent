"""Copy artifacts from child worldlines to parent worldline after subagent fan-in."""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import Any

import meta

logger = logging.getLogger(__name__)


def _workspace_path(worldline_id: str) -> Path:
    return meta.DB_DIR / "worldlines" / worldline_id / "workspace"


def _normalize_label(label: str) -> str:
    """Normalize task label for use in filename prefix."""
    clean = re.sub(r"[^a-zA-Z0-9_-]", "-", label.strip().lower())
    clean = re.sub(r"-+", "-", clean).strip("-")
    return clean[:30] if clean else ""


def copy_artifacts_to_parent(
    *,
    source_worldline_id: str,
    target_worldline_id: str,
    artifacts: list[dict[str, Any]],
    task_label: str,
    task_index: int,
    target_event_id: str,
) -> list[dict[str, Any]]:
    """
    Copy artifact files from child workspace to parent workspace.

    Returns list of new artifact records with prefixed names.
    """
    if not artifacts:
        return []

    source_workspace = _workspace_path(source_worldline_id)
    target_workspace = _workspace_path(target_worldline_id)
    target_workspace.mkdir(parents=True, exist_ok=True)

    # Build prefix from task label or index
    label_prefix = _normalize_label(task_label)
    if not label_prefix:
        label_prefix = f"task-{task_index}"

    merged: list[dict[str, Any]] = []

    for artifact in artifacts:
        original_name = str(artifact.get("name") or "").strip()
        original_path = str(artifact.get("path") or "").strip()
        artifact_type = str(artifact.get("type") or "file").strip()

        if not original_name or not original_path:
            continue

        # Resolve source file path
        source_path = Path(original_path)
        if not source_path.is_absolute():
            source_path = source_workspace / original_path

        if not source_path.exists() or not source_path.is_file():
            logger.warning("Skipping artifact copy - source not found: %s", source_path)
            continue

        # Create prefixed name
        prefixed_name = f"{label_prefix}_{original_name}"

        # Copy to target workspace
        target_path = target_workspace / prefixed_name
        try:
            shutil.copy2(source_path, target_path)
        except (OSError, IOError) as exc:
            logger.warning("Failed to copy artifact %s: %s", original_name, exc)
            continue

        # Create new artifact record
        new_artifact_id = meta.new_id("artifact")

        # Insert into artifacts table
        with meta.get_conn() as conn:
            conn.execute(
                """
                INSERT INTO artifacts (id, worldline_id, event_id, type, name, path)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    new_artifact_id,
                    target_worldline_id,
                    target_event_id,
                    artifact_type,
                    prefixed_name,
                    str(target_path),
                ),
            )
            conn.commit()

        merged.append(
            {
                "artifact_id": new_artifact_id,
                "name": prefixed_name,
                "type": artifact_type,
                "source_worldline_id": source_worldline_id,
                "source_name": original_name,
                "task_label": task_label,
                "task_index": task_index,
            }
        )

        logger.info(
            "Copied artifact %s -> %s (worldline %s -> %s)",
            original_name,
            prefixed_name,
            source_worldline_id[:8],
            target_worldline_id[:8],
        )

    return merged
