from __future__ import annotations

import sqlite3
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

BASE_DIR = Path(__file__).resolve().parent
DB_DIR = BASE_DIR / "data"
DB_PATH = DB_DIR / "meta.db"


SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS threads (
        id TEXT PRIMARY KEY,
        title TEXT,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS worldlines (
        id TEXT PRIMARY KEY,
        thread_id TEXT NOT NULL,
        parent_worldline_id TEXT NULL,
        forked_from_event_id TEXT NULL,
        head_event_id TEXT NULL,
        name TEXT,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (thread_id) REFERENCES threads(id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
        id TEXT PRIMARY KEY,
        worldline_id TEXT NOT NULL,
        parent_event_id TEXT NULL,
        type TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (worldline_id) REFERENCES worldlines(id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS snapshots (
        id TEXT PRIMARY KEY,
        worldline_id TEXT NOT NULL,
        event_id TEXT NOT NULL,
        duckdb_path TEXT NOT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (worldline_id) REFERENCES worldlines(id),
        FOREIGN KEY (event_id) REFERENCES events(id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS artifacts (
        id TEXT PRIMARY KEY,
        worldline_id TEXT NOT NULL,
        event_id TEXT NOT NULL,
        type TEXT NOT NULL,
        name TEXT NOT NULL,
        path TEXT NOT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (worldline_id) REFERENCES worldlines(id),
        FOREIGN KEY (event_id) REFERENCES events(id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS chat_turn_jobs (
        id TEXT PRIMARY KEY,
        thread_id TEXT NOT NULL,
        worldline_id TEXT NOT NULL,
        request_json TEXT NOT NULL,
        status TEXT NOT NULL,
        error TEXT NULL,
        result_worldline_id TEXT NULL,
        result_summary_json TEXT NULL,
        seen_at DATETIME NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        started_at DATETIME NULL,
        finished_at DATETIME NULL,
        FOREIGN KEY (thread_id) REFERENCES threads(id),
        FOREIGN KEY (worldline_id) REFERENCES worldlines(id)
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_chat_turn_jobs_worldline_status_created
    ON chat_turn_jobs (worldline_id, status, created_at);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_chat_turn_jobs_thread_created
    ON chat_turn_jobs (thread_id, created_at);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_chat_turn_jobs_status_created
    ON chat_turn_jobs (status, created_at);
    """,
)


def new_id(prefix: str) -> str:
    """
    Generate a new id given a prefix
    """
    return f"{prefix}_{uuid4().hex}"


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")

    try:
        yield conn
    finally:
        conn.close()


def init_meta_db() -> None:
    with get_conn() as conn:
        for statement in SCHEMA_STATEMENTS:
            conn.execute(statement)
        conn.commit()


def event_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "parent_event_id": row["parent_event_id"],
        "type": row["type"],
        "payload": json.loads(row["payload_json"]),
        "created_at": row["created_at"],
    }


def paginate_by_cursor(
    items: list[dict[str, Any]],
    *,
    cursor: str | None,
    limit: int,
    id_key: str = "id",
) -> tuple[list[dict[str, Any]], str | None]:
    if cursor:
        idx = next((i for i, item in enumerate(items) if item[id_key] == cursor), None)
        if idx is None:
            raise ValueError("invalid cursor")
        items = items[idx + 1 :]

    page = items[:limit]
    next_cursor = page[-1][id_key] if len(items) > limit else None
    return page, next_cursor


def get_worldline_row(conn: sqlite3.Connection, worldline_id: str):
    return conn.execute(
        "SELECT id, head_event_id FROM worldlines WHERE id = ?",
        (worldline_id,),
    ).fetchone()


def append_event(
    conn: sqlite3.Connection,
    worldline_id: str,
    parent_event_id: str | None,
    event_type: str,
    payload: dict,
) -> str:
    event_id = new_id("event")
    conn.execute(
        """
        INSERT INTO events (id, worldline_id, parent_event_id, type, payload_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (event_id, worldline_id, parent_event_id, event_type, json.dumps(payload)),
    )
    return event_id


def set_worldline_head(
    conn: sqlite3.Connection, worldline_id: str, event_id: str
) -> None:
    conn.execute(
        "UPDATE worldlines SET head_event_id = ? WHERE id = ?",
        (event_id, worldline_id),
    )
