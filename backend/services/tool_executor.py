"""
Tool execution services - SQL and Python execution logic.
"""

from __future__ import annotations

import json
import re
import textwrap
import time
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from fastapi import HTTPException

from debug_log import debug_log as _debug_log
from duckdb_manager import execute_read_query
from meta import (
    EventStoreConflictError,
    append_event_and_advance_head,
    event_row_to_dict,
    get_conn,
    get_worldline_row,
    new_id,
)
from sandbox.docker_runner import DockerSandboxRunner
from sandbox.manager import SandboxManager

if TYPE_CHECKING:
    from api.tools import SqlToolRequest, PythonToolRequest

# Singleton sandbox manager
_sandbox_manager = SandboxManager(DockerSandboxRunner())

READ_ONLY_PREFIXES = ("select", "with", "show", "describe", "explain")
ToolEventCallback = Callable[[dict[str, Any]], Awaitable[None]]


def get_sandbox_manager() -> SandboxManager:
    """Get the singleton sandbox manager instance."""
    return _sandbox_manager


def validate_read_only_sql(sql: str) -> None:
    """Validate that SQL is read-only."""
    stripped = sql.strip().lstrip("(")
    first = stripped.split(None, 1)[0].lower() if stripped else ""
    if first not in READ_ONLY_PREFIXES:
        raise HTTPException(status_code=400, detail="Only read-only SQL is allowed.")
    if ";" in stripped.rstrip(";"):
        raise HTTPException(
            status_code=400, detail="Multiple SQL statements are not allowed."
        )


def _load_event_by_id(conn, event_id: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT id, parent_event_id, type, payload_json, created_at
        FROM events
        WHERE id = ?
        """,
        (event_id,),
    ).fetchone()
    return event_row_to_dict(row)


async def _append_worldline_event(
    conn,
    *,
    worldline_id: str,
    parent_event_id: str | None,
    event_type: str,
    payload: dict[str, Any],
    on_event: ToolEventCallback | None = None,
    max_attempts: int = 1,
    allow_rebase_on_conflict: bool = False,
) -> str:
    expected_parent = parent_event_id
    attempts = max(1, max_attempts)

    for attempt in range(attempts):
        try:
            event_id = append_event_and_advance_head(
                conn,
                worldline_id=worldline_id,
                expected_head_event_id=expected_parent,
                event_type=event_type,
                payload=payload,
            )
            conn.commit()
            if on_event is not None:
                await on_event(_load_event_by_id(conn, event_id))
            return event_id
        except EventStoreConflictError:
            conn.rollback()
            if allow_rebase_on_conflict and attempt < attempts - 1:
                worldline = get_worldline_row(conn, worldline_id)
                if worldline is None:
                    raise HTTPException(status_code=404, detail="worldline not found")
                expected_parent = worldline["head_event_id"]
                continue
            raise HTTPException(
                status_code=409,
                detail=f"worldline head moved during {event_type} append",
            )

    raise HTTPException(
        status_code=409,
        detail=f"worldline head moved during {event_type} append",
    )


async def execute_sql_tool(
    worldline_id: "SqlToolRequest | str",
    sql: str | None = None,
    limit: int = 1000,
    allowed_external_aliases: list[str] | None = None,
    call_id: str | None = None,
    on_event: ToolEventCallback | None = None,
):
    """Execute a SQL query against a worldline's DuckDB.

    Accepts either:
    - A SqlToolRequest object as first argument, or
    - Unpacked keyword arguments (worldline_id, sql, limit, etc.)
    """
    # Handle request object form (duck typing for SqlToolRequest)
    if hasattr(worldline_id, "worldline_id"):
        body = worldline_id
        worldline_id = body.worldline_id
        sql = body.sql
        limit = body.limit
        allowed_external_aliases = body.allowed_external_aliases
        call_id = body.call_id
    else:
        if sql is None:
            raise ValueError("sql argument is required")

    validate_read_only_sql(sql)
    started = time.perf_counter()

    with get_conn() as conn:
        worldline = get_worldline_row(conn, worldline_id)
        if worldline is None:
            raise HTTPException(status_code=404, detail="worldline not found")

        parent_event_id = worldline["head_event_id"]
        call_payload: dict[str, Any] = {"sql": sql, "limit": limit}
        if allowed_external_aliases is not None:
            normalized_aliases = [
                alias.strip()
                for alias in allowed_external_aliases
                if isinstance(alias, str) and alias.strip()
            ]
            call_payload["allowed_external_aliases"] = normalized_aliases
        else:
            normalized_aliases = None
        if call_id:
            call_payload["call_id"] = call_id
        call_event_id = await _append_worldline_event(
            conn,
            worldline_id=worldline_id,
            parent_event_id=parent_event_id,
            event_type="tool_call_sql",
            payload=call_payload,
            on_event=on_event,
            max_attempts=4,
            allow_rebase_on_conflict=True,
        )

        query_error: Exception | None = None
        try:
            result = execute_read_query(
                worldline_id,
                sql,
                limit,
                allowed_external_aliases=normalized_aliases,
            )
            result["execution_ms"] = int((time.perf_counter() - started) * 1000)
        except Exception as exc:
            query_error = exc
            result = {"error": str(exc)}

        await _append_worldline_event(
            conn,
            worldline_id=worldline_id,
            parent_event_id=call_event_id,
            event_type="tool_result_sql",
            payload=result,
            on_event=on_event,
        )
        if query_error is not None:
            raise HTTPException(status_code=400, detail=str(query_error))
        return result


# Python tool helpers


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
        events.append(event_row_to_dict(row))
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


def _extract_latest_successful_sql_result(events: list[dict]) -> dict[str, Any] | None:
    for event in reversed(events):
        if event["type"] != "tool_result_sql":
            continue
        payload = event.get("payload", {})
        if payload.get("error"):
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _build_sql_context_code(latest_sql_result: dict[str, Any] | None) -> str:
    if not latest_sql_result:
        return ""
    serialized = json.dumps(latest_sql_result, ensure_ascii=True, default=str)
    escaped = serialized.replace("\\", "\\\\").replace("'", "\\'")
    return "\n".join(
        [
            "import json",
            f"LATEST_SQL_RESULT = json.loads('{escaped}')",
            "LATEST_SQL_COLUMNS = [c.get('name', '') for c in (LATEST_SQL_RESULT.get('columns') or []) if isinstance(c, dict)]",
            "LATEST_SQL_ROWS = LATEST_SQL_RESULT.get('rows') or []",
            "try:",
            "    import pandas as pd",
            "    LATEST_SQL_DF = pd.DataFrame(LATEST_SQL_ROWS, columns=LATEST_SQL_COLUMNS)",
            "except Exception:",
            "    LATEST_SQL_DF = None",
        ]
    )


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


def _detect_tool_invocations_in_python(code: str) -> list[str]:
    found: list[str] = []
    for tool_name in ("run_sql", "run_python", "time_travel"):
        if re.search(rf"\b{tool_name}\s*\(", code):
            found.append(tool_name)
    return found


def _format_syntax_error(exc: SyntaxError) -> dict[str, Any]:
    line = exc.lineno if isinstance(exc.lineno, int) and exc.lineno > 0 else None
    column = exc.offset if isinstance(exc.offset, int) and exc.offset > 0 else None

    location_parts: list[str] = []
    if line is not None:
        location_parts.append(f"line {line}")
    if column is not None:
        location_parts.append(f"column {column}")
    location_text = ""
    if location_parts:
        location_text = " at " + ", ".join(location_parts)

    message = str(exc.msg or "invalid syntax")
    text = f"Python code failed syntax preflight{location_text}: {message}."
    if isinstance(exc.text, str) and exc.text.strip():
        text += f" Offending line: {exc.text.strip()}"

    payload: dict[str, Any] = {
        "error": text,
        "error_code": "python_compile_error",
        "retryable": True,
    }
    if line is not None:
        payload["line"] = line
    if column is not None:
        payload["column"] = column
    return payload


def _python_preflight_error_payload(
    *,
    code: str,
    execution_code: str,
) -> dict[str, Any] | None:
    invalid_tool_calls = _detect_tool_invocations_in_python(code)
    if invalid_tool_calls:
        return {
            "error": (
                "Python code attempted to call backend tools directly "
                f"({', '.join(invalid_tool_calls)}). Use tool calls at the model level "
                "(run_sql/run_python) and keep Python as plain executable analysis code."
            ),
            "error_code": "python_tool_invocation_forbidden",
            "retryable": True,
            "invalid_tool_calls": invalid_tool_calls,
        }

    try:
        compile(code, "<run_python_code>", "exec")
    except SyntaxError as exc:
        return _format_syntax_error(exc)

    try:
        compile(execution_code, "<run_python_execution_payload>", "exec")
    except SyntaxError as exc:
        payload = _format_syntax_error(exc)
        payload["error_code"] = "python_execution_payload_compile_error"
        payload["error"] = (
            "Generated execution payload failed syntax preflight before sandbox run: "
            + str(payload.get("error") or "python compile error")
        )
        return payload

    return None


class PythonPreflightError(Exception):
    """Raised when Python code fails preflight checks."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        super().__init__(str(payload.get("error") or "python preflight failed"))


async def execute_python_tool(
    worldline_id: "PythonToolRequest | str",
    code: str | None = None,
    timeout: int = 60,
    call_id: str | None = None,
    on_event: ToolEventCallback | None = None,
):
    """Execute Python code in a sandboxed environment.

    Accepts either:
    - A PythonToolRequest object as first argument, or
    - Unpacked keyword arguments (worldline_id, code, timeout, etc.)
    """
    # Handle request object form (duck typing for PythonToolRequest)
    if hasattr(worldline_id, "worldline_id"):
        body = worldline_id
        worldline_id = body.worldline_id
        code = body.code
        timeout = body.timeout
        call_id = body.call_id
    else:
        if code is None:
            raise ValueError("code argument is required")

    started = time.perf_counter()
    with get_conn() as conn:
        worldline = get_worldline_row(conn, worldline_id)
        if worldline is None:
            raise HTTPException(status_code=404, detail="worldline not found")
        parent_event_id = worldline["head_event_id"]
        prior_events = _load_event_chain(conn, parent_event_id)
        prior_python_codes = _extract_successful_python_codes(prior_events)
        latest_sql_result = _extract_latest_successful_sql_result(prior_events)
        sql_context_code = _build_sql_context_code(latest_sql_result)
        active_worldlines_fn = getattr(_sandbox_manager, "active_worldlines", None)
        active_worldlines = (
            active_worldlines_fn() if callable(active_worldlines_fn) else []
        )
        sandbox_warm = worldline_id in set(active_worldlines)
        replay_python_codes = [] if sandbox_warm else prior_python_codes
        _debug_log(
            run_id="initial",
            hypothesis_id="H1_H2",
            location="services/tool_executor.py:execute_python_tool:prior_history",
            message="Loaded prior python execution history",
            data={
                "worldline_id": worldline_id,
                "parent_event_id": parent_event_id,
                "prior_events_count": len(prior_events),
                "prior_python_code_count": len(prior_python_codes),
                "sandbox_warm": sandbox_warm,
                "replay_python_code_count_applied": len(replay_python_codes),
                "has_sql_context": latest_sql_result is not None,
                "current_code_preview": code[:180],
            },
        )
        execution_code = _build_replay_code(replay_python_codes, code)
        if sql_context_code:
            execution_code = f"{sql_context_code}\n\n{execution_code}"
        _debug_log(
            run_id="initial",
            hypothesis_id="H1",
            location="services/tool_executor.py:execute_python_tool:execution_code",
            message="Built python execution payload for sandbox",
            data={
                "worldline_id": worldline_id,
                "prior_replay_steps": len(replay_python_codes),
                "has_sql_context": bool(sql_context_code),
                "execution_code_len": len(execution_code),
                "execution_code_preview": execution_code[:220],
            },
        )

        call_payload: dict[str, Any] = {"code": code, "timeout": timeout}
        if call_id:
            call_payload["call_id"] = call_id
        call_event_id = await _append_worldline_event(
            conn,
            worldline_id=worldline_id,
            parent_event_id=parent_event_id,
            event_type="tool_call_python",
            payload=call_payload,
            on_event=on_event,
            max_attempts=4,
            allow_rebase_on_conflict=True,
        )

        try:
            preflight_error_payload = _python_preflight_error_payload(
                code=code,
                execution_code=execution_code,
            )
            if preflight_error_payload is not None:
                _debug_log(
                    run_id="initial",
                    hypothesis_id="H9",
                    location="services/tool_executor.py:execute_python_tool:preflight_error",
                    message="Python preflight blocked sandbox execution",
                    data={
                        "worldline_id": worldline_id,
                        "call_id": call_id,
                        "error_code": preflight_error_payload.get("error_code"),
                        "error": preflight_error_payload.get("error"),
                    },
                )
                raise PythonPreflightError(preflight_error_payload)

            _debug_log(
                run_id="initial",
                hypothesis_id="H1_H5",
                location="services/tool_executor.py:execute_python_tool:before_execute",
                message="Dispatching code to sandbox manager",
                data={
                    "worldline_id": worldline_id,
                    "timeout": timeout,
                    "call_id": call_id,
                },
            )
            raw_result = await _sandbox_manager.execute(
                worldline_id=worldline_id,
                code=execution_code,
                timeout_s=timeout,
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

            result_event_id = append_event_and_advance_head(
                conn,
                worldline_id=worldline_id,
                expected_head_event_id=call_event_id,
                event_type="tool_result_python",
                payload=api_result,
            )
            for artifact in db_artifacts:
                if not artifact["path"]:
                    continue

                conn.execute(
                    """
                        INSERT INTO artifacts (id, worldline_id, event_id, type, name, path)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                    (
                        artifact["id"],
                        worldline_id,
                        result_event_id,
                        artifact["type"],
                        artifact["name"],
                        artifact["path"],
                    ),
                )

            conn.commit()
            if on_event is not None:
                await on_event(_load_event_by_id(conn, result_event_id))
            _debug_log(
                run_id="initial",
                hypothesis_id="H1_H5",
                location="services/tool_executor.py:execute_python_tool:result",
                message="Python tool execution completed",
                data={
                    "worldline_id": worldline_id,
                    "result_error": api_result.get("error"),
                    "stdout_len": len(str(api_result.get("stdout", ""))),
                    "stderr_len": len(str(api_result.get("stderr", ""))),
                    "artifact_count": len(api_result.get("artifacts", [])),
                },
            )
            return api_result
        except PythonPreflightError as exc:
            await _append_worldline_event(
                conn,
                worldline_id=worldline_id,
                parent_event_id=call_event_id,
                event_type="tool_result_python",
                payload=exc.payload,
                on_event=on_event,
            )
            raise HTTPException(
                status_code=400,
                detail=exc.payload,
            )
        except EventStoreConflictError:
            conn.rollback()
            raise HTTPException(
                status_code=409,
                detail="worldline head moved before python tool result append",
            )
        except HTTPException:
            raise
        except Exception as exc:
            _debug_log(
                run_id="initial",
                hypothesis_id="H5",
                location="services/tool_executor.py:execute_python_tool:error",
                message="Python tool execution raised exception",
                data={
                    "worldline_id": worldline_id,
                    "call_id": call_id,
                    "error": str(exc),
                },
            )
            await _append_worldline_event(
                conn,
                worldline_id=worldline_id,
                parent_event_id=call_event_id,
                event_type="tool_result_python",
                payload={
                    "error": str(exc),
                    "error_code": "python_runtime_error",
                    "retryable": False,
                },
                on_event=on_event,
            )
            raise HTTPException(status_code=400, detail=str(exc))
