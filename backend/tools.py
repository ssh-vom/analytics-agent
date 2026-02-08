import time
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


class SqlToolRequest(BaseModel):
    worldline_id: str
    sql: str
    limit: int = Field(default=100, ge=1, le=10_000)


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


class PythonToolRequest(BaseModel):
    worldline_id: str
    code: str
    timeout: int = Field(default=30, ge=1, le=120)


@router.post("/python")
async def run_python(body: PythonToolRequest):
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
            "tool_call_python",
            {"code": body.code, "timeout": body.timeout},
        )
        try:
            raw_result = await _sandbox_manager.execute(
                worldline_id=body.worldline_id,
                code=body.code,
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
            raise HTTPException(status_code=400, detail=str(exc))
