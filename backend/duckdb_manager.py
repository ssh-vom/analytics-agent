import duckdb
import shutil
from pathlib import Path

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


def execute_read_query(worldline_id: str, sql: str, limit: int) -> dict:
    db_path = ensure_worldline_db(worldline_id)
    conn = duckdb.connect(str(db_path))

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
