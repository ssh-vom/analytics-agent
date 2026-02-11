from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from meta import (
    EventStoreConflictError,
    append_event_and_advance_head,
    event_row_to_dict,
    get_conn,
    get_worldline_row,
)


def load_event_by_id(event_id: str) -> dict[str, Any]:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT id, parent_event_id, type, payload_json, created_at
            FROM events
            WHERE id = ?
            """,
            (event_id,),
        ).fetchone()

    return event_row_to_dict(row)


def append_worldline_event(
    *,
    worldline_id: str,
    event_type: str,
    payload: dict[str, Any],
    max_attempts: int = 4,
) -> dict[str, Any]:
    attempts = max(1, max_attempts)
    for attempt in range(attempts):
        with get_conn() as conn:
            worldline = get_worldline_row(conn, worldline_id)
            if worldline is None:
                raise HTTPException(status_code=404, detail="worldline not found")

            try:
                event_id = append_event_and_advance_head(
                    conn,
                    worldline_id=worldline_id,
                    expected_head_event_id=worldline["head_event_id"],
                    event_type=event_type,
                    payload=payload,
                )
                conn.commit()
                return load_event_by_id(event_id)
            except EventStoreConflictError:
                conn.rollback()
                if attempt == attempts - 1:
                    raise HTTPException(
                        status_code=409,
                        detail="worldline head moved during event append",
                    )

    raise HTTPException(
        status_code=409,
        detail="worldline head moved during event append",
    )


def max_worldline_rowid(worldline_id: str) -> int:
    with get_conn() as conn:
        worldline = get_worldline_row(conn, worldline_id)
        if worldline is None:
            raise HTTPException(status_code=404, detail="worldline not found")

        row = conn.execute(
            "SELECT COALESCE(MAX(rowid), 0) AS max_rowid FROM events WHERE worldline_id = ?",
            (worldline_id,),
        ).fetchone()
        return int(row["max_rowid"])


def events_since_rowid(*, worldline_id: str, rowid: int) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, parent_event_id, type, payload_json, created_at
            FROM events
            WHERE worldline_id = ? AND rowid > ?
            ORDER BY rowid ASC
            """,
            (worldline_id, rowid),
        ).fetchall()

    output: list[dict[str, Any]] = []
    for row in rows:
        output.append(event_row_to_dict(row))
    return output
