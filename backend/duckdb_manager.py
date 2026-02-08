from pathlib import Path
import duckdb
from meta import DB_DIR

WORLDLINES_DIR = DB_DIR / "worldlines"


def worldline_db_path(worldline_id: str) -> Path:
    return WORLDLINES_DIR / worldline_id / "state.duckdb"


def ensure_worldline_db(worldline_id: str) -> Path:
    db_path = worldline_db_path(worldline_id)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(db_path))
    conn.close()
    return db_path


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
