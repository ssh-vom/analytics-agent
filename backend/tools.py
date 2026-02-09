import time
import json
import textwrap
from typing import Any, Awaitable, Callable
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

try:
    from backend.duckdb_manager import execute_read_query
    from backend.meta import (
        append_event,
        get_conn,
        get_worldline_row,
        set_worldline_head,
        new_id,
    )
    from backend.sandbox.docker_runner import DockerSandboxRunner
    from backend.sandbox.manager import SandboxManager
except ModuleNotFoundError:
    from duckdb_manager import execute_read_query
    from meta import (
        append_event,
        get_conn,
        get_worldline_row,
        set_worldline_head,
        new_id,
    )
    from sandbox.docker_runner import DockerSandboxRunner
    from sandbox.manager import SandboxManager

router = APIRouter(prefix="/api/tools", tags=["tools"])

READ_ONLY_PREFIXES = ("select", "with", "show", "describe", "explain")
_sandbox_manager = SandboxManager(DockerSandboxRunner())
ToolEventCallback = Callable[[dict[str, Any]], Awaitable[None]]


class SqlToolRequest(BaseModel):
    worldline_id: str
    sql: str
    limit: int = Field(default=1000, ge=1, le=100_000)
    call_id: str | None = None


def validate_read_only_sql(sql: str) -> None:
    stripped = sql.strip().lstrip("(")
    first = stripped.split(None, 1)[0].lower() if stripped else ""
    if first not in READ_ONLY_PREFIXES:
        raise HTTPException(status_code=400, detail="Only read-only SQL is allowed.")
    if ";" in stripped.rstrip(";"):
        raise HTTPException(
            status_code=400, detail="Multiple SQL statements are not allowed."
        )


def get_sandbox_manager() -> SandboxManager:
    return _sandbox_manager


def _load_event_by_id(conn, event_id: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT id, parent_event_id, type, payload_json, created_at
        FROM events
        WHERE id = ?
        """,
        (event_id,),
    ).fetchone()
    return {
        "id": row["id"],
        "parent_event_id": row["parent_event_id"],
        "type": row["type"],
        "payload": json.loads(row["payload_json"]),
        "created_at": row["created_at"],
    }


async def execute_sql_tool(
    body: SqlToolRequest,
    on_event: ToolEventCallback | None = None,
):
    validate_read_only_sql(body.sql)
    started = time.perf_counter()

    with get_conn() as conn:
        worldline = get_worldline_row(conn, body.worldline_id)
        if worldline is None:
            raise HTTPException(status_code=404, detail="worldline not found")

        parent_event_id = worldline["head_event_id"]
        call_payload: dict[str, Any] = {"sql": body.sql, "limit": body.limit}
        if body.call_id:
            call_payload["call_id"] = body.call_id
        call_event_id = append_event(
            conn,
            body.worldline_id,
            parent_event_id,
            "tool_call_sql",
            call_payload,
        )
        set_worldline_head(conn, body.worldline_id, call_event_id)
        conn.commit()
        if on_event is not None:
            await on_event(_load_event_by_id(conn, call_event_id))

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
            if on_event is not None:
                await on_event(_load_event_by_id(conn, result_event_id))
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
            if on_event is not None:
                await on_event(_load_event_by_id(conn, result_event_id))
            raise HTTPException(status_code=400, detail=str(exc))


@router.post("/sql")
async def run_sql(body: SqlToolRequest):
    return await execute_sql_tool(body)


class PythonToolRequest(BaseModel):
    worldline_id: str
    code: str
    timeout: int = Field(default=60, ge=1, le=600)
    call_id: str | None = None


def _load_event_chain(conn, head_event_id: str | None) -> list[dict]:
    if head_event_id is None:
        return []

    rows = conn.execute(
        """
        WITH RECURSIVE chain AS (
            SELECT id, parent_event_id, type, payload_json, created_at, 0 AS depth
            FROM events
            WHERE id = ?
            UNION ALL
            SELECT e.id, e.parent_event_id, e.type, e.payload_json, e.created_at, chain.depth + 1
            FROM events e
            JOIN chain ON chain.parent_event_id = e.id
        )
        SELECT id, parent_event_id, type, payload_json, created_at, depth
        FROM chain
        ORDER BY depth DESC
        """,
        (head_event_id,),
    ).fetchall()

    events: list[dict] = []
    for row in rows:
        events.append(
            {
                "id": row["id"],
                "parent_event_id": row["parent_event_id"],
                "type": row["type"],
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
            }
        )
    return events


def _extract_successful_python_codes(events: list[dict]) -> list[str]:
    by_id = {event["id"]: event for event in events}
    codes: list[str] = []

    for event in events:
        if event["type"] != "tool_result_python":
            continue

        payload = event.get("payload", {})
        if payload.get("error"):
            continue

        call_event = by_id.get(event["parent_event_id"])
        if not call_event or call_event["type"] != "tool_call_python":
            continue

        code = call_event.get("payload", {}).get("code")
        if code:
            codes.append(code)

    return codes


def _build_replay_code(prior_codes: list[str], current_code: str) -> str:
    if not prior_codes:
        return current_code

    chunks: list[str] = [
        "import contextlib",
        "import io",
    ]
    for idx, code in enumerate(prior_codes, start=1):
        indented = textwrap.indent(code, "    ")
        chunks.append(
            "\n".join(
                [
                    f"# replay_step_{idx}",
                    "_replay_stdout = io.StringIO()",
                    "_replay_stderr = io.StringIO()",
                    "with contextlib.redirect_stdout(_replay_stdout), contextlib.redirect_stderr(_replay_stderr):",
                    indented,
                ]
            )
        )
    chunks.append(f"# current_step\n{current_code}")
    return "\n\n".join(chunks)


async def execute_python_tool(
    body: PythonToolRequest,
    on_event: ToolEventCallback | None = None,
):
    started = time.perf_counter()
    with get_conn() as conn:
        worldline = get_worldline_row(conn, body.worldline_id)
        if worldline is None:
            raise HTTPException(status_code=404, detail="worldline not found")
        parent_event_id = worldline["head_event_id"]
        prior_events = _load_event_chain(conn, parent_event_id)
        prior_python_codes = _extract_successful_python_codes(prior_events)
        execution_code = _build_replay_code(prior_python_codes, body.code)

        call_payload: dict[str, Any] = {"code": body.code, "timeout": body.timeout}
        if body.call_id:
            call_payload["call_id"] = body.call_id
        call_event_id = append_event(
            conn,
            body.worldline_id,
            parent_event_id,
            "tool_call_python",
            call_payload,
        )
        set_worldline_head(conn, body.worldline_id, call_event_id)
        conn.commit()
        if on_event is not None:
            await on_event(_load_event_by_id(conn, call_event_id))

        try:
            raw_result = await _sandbox_manager.execute(
                worldline_id=body.worldline_id,
                code=execution_code,
                timeout_s=body.timeout,
            )
            api_artifacts = []
            db_artifacts = []
            for artifact in raw_result.get("artifacts", []):
                artifact_id = new_id("artifact")
                api_artifacts.append(
                    {
                        "type": artifact.get("type", "file"),
                        "name": artifact.get("name", "artifact"),
                        "artifact_id": artifact_id,
                    }
                )

                db_artifacts.append(
                    {
                        "id": artifact_id,
                        "type": artifact.get("type", "file"),
                        "name": artifact.get("name", "artifact"),
                        "path": artifact.get("path", ""),
                    }
                )

            api_result = {
                "stdout": raw_result.get("stdout", ""),
                "stderr": raw_result.get("stderr", ""),
                "error": raw_result.get("error"),
                "artifacts": api_artifacts,
                "previews": raw_result.get("previews", {"dataframes": []}),
                "execution_ms": int((time.perf_counter() - started) * 1000),
            }

            result_event_id = append_event(
                conn,
                body.worldline_id,
                call_event_id,
                "tool_result_python",
                api_result,
            )
            for artifact in db_artifacts:
                if not artifact["path"]:
                    continue

                _ = conn.execute(
                    """
                        INSERT INTO artifacts (id, worldline_id, event_id, type, name, path)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                    (
                        artifact["id"],
                        body.worldline_id,
                        result_event_id,
                        artifact["type"],
                        artifact["name"],
                        artifact["path"],
                    ),
                )

            set_worldline_head(conn, body.worldline_id, result_event_id)
            conn.commit()
            if on_event is not None:
                await on_event(_load_event_by_id(conn, result_event_id))
            return api_result
        except Exception as exc:
            result_event_id = append_event(
                conn,
                body.worldline_id,
                call_event_id,
                "tool_result_python",
                {"error": str(exc)},
            )
            set_worldline_head(conn, body.worldline_id, result_event_id)
            conn.commit()
            if on_event is not None:
                await on_event(_load_event_by_id(conn, result_event_id))
            raise HTTPException(status_code=400, detail=str(exc))


@router.post("/python")
async def run_python(body: PythonToolRequest):
    return await execute_python_tool(body)
