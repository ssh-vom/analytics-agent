import time
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from duckdb_manager import execute_read_query
from meta import get_conn, get_worldline_row, append_event, set_worldline_head

router = APIRouter(prefix="/api/tools", tags=["tools"])

READ_ONLY_PREFIXES = ("select", "with", "show", "describe", "explain")


class SqlToolRequest(BaseModel):
    worldline_id: str
    sql: str
    limit: int = 100


def validate_read_only_sql(sql: str) -> None:
    stripped = sql.strip().lstrip("(")
    first = stripped.split(None, 1)[0].lower() if stripped else ""
    if first not in READ_ONLY_PREFIXES:
        raise HTTPException(status_code=400, detail="Only read-only SQL is allowed.")
    if ";" in stripped.rstrip(";"):
        raise HTTPException(
            status_code=400, detail="Multiple SQL statements are not allowed."
        )


@router.post("/sql")
async def run_sql(body: SqlToolRequest):
    validate_read_only_sql(body.sql)
    started = time.perf_counter()

    with get_conn() as conn:
        worldline = get_worldline_row(conn, body.worldline_id)
        if worldline is None:
            raise HTTPException(status_code=404, detail="worldline not found")

        parent_event_id = worldline["head_event_id"]
        call_event_id = append_event(
            conn,
            body.worldline_id,
            parent_event_id,
            "tool_call_sql",
            {"sql": body.sql, "limit": body.limit},
        )

        try:
            result = execute_read_query(body.worldline_id, body.sql, body.limit)
            result["execution_ms"] = int((time.perf_counter() - started) * 1000)
            result_event_id = append_event(
                conn,
                body.worldline_id,
                call_event_id,
                "tool_result_sql",
                result,
            )
            set_worldline_head(conn, body.worldline_id, result_event_id)
            conn.commit()
            return result
        except Exception as exc:
            result_event_id = append_event(
                conn,
                body.worldline_id,
                call_event_id,
                "tool_result_sql",
                {"error": str(exc)},
            )
            set_worldline_head(conn, body.worldline_id, result_event_id)
            conn.commit()
            raise HTTPException(status_code=400, detail=str(exc))
