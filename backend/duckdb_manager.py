import duckdb
import shutil
from pathlib import Path
from typing import Any

if (__package__ or "").startswith("backend"):
    from backend import meta
else:
    import meta


def worldline_db_path(worldline_id: str) -> Path:
    return meta.DB_DIR / "worldlines" / worldline_id / "state.duckdb"


def ensure_worldline_db(worldline_id: str) -> Path:
    db_path = worldline_db_path(worldline_id)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(db_path))
    conn.close()
    return db_path


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _metadata_table_exists(conn: duckdb.DuckDBPyConnection, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'main' AND table_name = ?
        LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def _load_external_sources(conn: duckdb.DuckDBPyConnection) -> list[dict[str, str]]:
    if not _metadata_table_exists(conn, "_external_sources"):
        return []

    rows = conn.execute(
        """
        SELECT alias, db_path
        FROM _external_sources
        WHERE db_type = 'duckdb'
        ORDER BY attached_at DESC, alias ASC
        """
    ).fetchall()
    return [
        {
            "alias": str(row[0]),
            "db_path": str(row[1]),
        }
        for row in rows
    ]


def attach_read_only_database(
    conn: duckdb.DuckDBPyConnection, *, alias: str, db_path: str
) -> None:
    conn.execute(
        f"ATTACH {_quote_literal(db_path)} AS {_quote_identifier(alias)} (READ_ONLY)"
    )


def _normalize_allowed_aliases(
    allowed_external_aliases: list[str] | None,
) -> set[str] | None:
    if allowed_external_aliases is None:
        return None

    aliases: set[str] = set()
    for alias in allowed_external_aliases:
        normalized = alias.strip()
        if normalized:
            aliases.add(normalized)
    return aliases


def reattach_external_sources(
    conn: duckdb.DuckDBPyConnection,
    *,
    allowed_external_aliases: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Re-attach external DuckDB sources recorded in _external_sources.

    Attach definitions are session-scoped in DuckDB, so we reapply them
    whenever a new connection is opened to a worldline DB.
    """
    allowed_aliases = _normalize_allowed_aliases(allowed_external_aliases)
    attach_results: list[dict[str, Any]] = []
    for source in _load_external_sources(conn):
        alias = source["alias"]
        if allowed_aliases is not None and alias not in allowed_aliases:
            continue
        external_db_path = source["db_path"]
        if not Path(external_db_path).exists():
            attach_results.append(
                {
                    "alias": alias,
                    "db_path": external_db_path,
                    "attached": False,
                    "error": "database file missing",
                }
            )
            continue

        try:
            attach_read_only_database(conn, alias=alias, db_path=external_db_path)
            attach_results.append(
                {
                    "alias": alias,
                    "db_path": external_db_path,
                    "attached": True,
                    "error": None,
                }
            )
        except Exception as exc:
            attach_results.append(
                {
                    "alias": alias,
                    "db_path": external_db_path,
                    "attached": False,
                    "error": str(exc),
                }
            )

    return attach_results


def open_worldline_connection(
    worldline_id: str,
    *,
    include_external_sources: bool = True,
    allowed_external_aliases: list[str] | None = None,
) -> duckdb.DuckDBPyConnection:
    db_path = ensure_worldline_db(worldline_id)
    conn = duckdb.connect(str(db_path))
    if include_external_sources:
        reattach_external_sources(
            conn,
            allowed_external_aliases=allowed_external_aliases,
        )
    return conn


def clone_worldline_db(source_worldline_id: str, target_worldline_id: str) -> Path:
    source_path = worldline_db_path(source_worldline_id)
    return clone_worldline_db_from_file(source_path, target_worldline_id)


def clone_worldline_db_from_file(source_path: Path, target_worldline_id: str) -> Path:
    target_path = worldline_db_path(target_worldline_id)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    if source_path.exists():
        shutil.copy2(source_path, target_path)
        return target_path

    return ensure_worldline_db(target_worldline_id)


def snapshot_db_path(worldline_id: str, event_id: str) -> Path:
    return meta.DB_DIR / "snapshots" / worldline_id / f"{event_id}.duckdb"


def capture_worldline_snapshot(worldline_id: str, event_id: str) -> Path:
    source_path = ensure_worldline_db(worldline_id)
    target_snapshot_path = snapshot_db_path(worldline_id, event_id)
    target_snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, target_snapshot_path)
    return target_snapshot_path


def execute_read_query(
    worldline_id: str,
    sql: str,
    limit: int,
    *,
    allowed_external_aliases: list[str] | None = None,
) -> dict:
    conn = open_worldline_connection(
        worldline_id,
        allowed_external_aliases=allowed_external_aliases,
    )

    try:
        cur = conn.execute(sql)
        rows_all = cur.fetchall()
        columns = [{"name": d[0], "type": str(d[1])} for d in cur.description]
        rows_preview = [list(row) for row in rows_all[:limit]]
        return {
            "columns": columns,
            "rows": rows_preview,
            "row_count": len(rows_all),
            "preview_count": len(rows_preview),
        }

    finally:
        conn.close()
