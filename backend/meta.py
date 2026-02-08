from __future__ import annotations

import sqlite3
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
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
            _ = conn.execute(statement)  # discard cursor
        conn.commit()


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
    _ = conn.execute(
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
    _ = conn.execute(
        "UPDATE worldlines SET head_event_id = ? WHERE id = ?",
        (event_id, worldline_id),
    )
