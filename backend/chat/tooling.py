from __future__ import annotations

import json
import re
from typing import Any

from chat.llm_client import ToolCall, ToolDefinition

SQL_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sql": {"type": "string"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 10_000},
    },
    "required": ["sql"],
    "additionalProperties": False,
}

PYTHON_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "code": {"type": "string"},
        "timeout": {"type": "integer", "minimum": 1, "maximum": 120},
    },
    "required": ["code"],
    "additionalProperties": False,
}

TIME_TRAVEL_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "from_event_id": {"type": "string"},
        "name": {"type": "string"},
    },
    "required": ["from_event_id"],
    "additionalProperties": False,
}

SPAWN_SUBAGENTS_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "goal": {"type": "string"},
        "tasks": {
            "type": "array",
            "minItems": 1,
            "maxItems": 50,
            "items": {
                "type": "object",
                "properties": {
                    "message": {"type": "string"},
                    "label": {"type": "string"},
                    "branch_name": {"type": "string"},
                },
                "required": ["message"],
                "additionalProperties": False,
            },
        },
        "from_event_id": {"type": "string"},
        "timeout_s": {"type": "integer", "minimum": 1, "maximum": 1800},
        "max_iterations": {"type": "integer", "minimum": 1, "maximum": 100},
        "max_subagents": {"type": "integer", "minimum": 1, "maximum": 50},
        "max_parallel_subagents": {
            "type": "integer",
            "minimum": 1,
            "maximum": 10,
        },
    },
    "anyOf": [
        {"required": ["goal"]},
        {"required": ["tasks"]},
    ],
    "additionalProperties": False,
}


def tool_definitions(
    *, include_python: bool = True, include_spawn_subagents: bool = True
) -> list[ToolDefinition]:
    tools: list[ToolDefinition] = [
        ToolDefinition(
            name="run_sql",
            description=(
                "Execute a read-only SQL query against the worldline DuckDB. "
                "Use for table reads and aggregations."
            ),
            input_schema=SQL_TOOL_SCHEMA,
        ),
    ]

    if include_spawn_subagents:
        tools.append(
            ToolDefinition(
                name="spawn_subagents",
                description=(
                    "Fan out parallel child investigations by branching worldlines from a "
                    "prior event. Prefer passing `goal` and let the system split work into "
                    "tasks automatically; optionally pass explicit `tasks`. The parent turn "
                    "blocks until child worldlines finish, then returns aggregated results."
                ),
                input_schema=SPAWN_SUBAGENTS_TOOL_SCHEMA,
            ),
        )

    if include_python:
        tools.insert(
            1,
            ToolDefinition(
                name="run_python",
                description=(
                    "Execute Python in the sandbox workspace for this worldline. "
                    "Use for plotting, data manipulation, and file artifacts. "
                    "For plots: use matplotlib (plt.plot, plt.bar, etc.) and call "
                    "plt.savefig('plot.png') before plt.show() to persist the image."
                ),
                input_schema=PYTHON_TOOL_SCHEMA,
            ),
        )
    return tools


def tool_name_to_delta_type(tool_name: str) -> str | None:
    if tool_name == "run_sql":
        return "tool_call_sql"
    if tool_name == "run_python":
        return "tool_call_python"
    if tool_name == "spawn_subagents":
        return "tool_call_subagents"
    return None


def looks_like_complete_tool_args(args_delta: str) -> bool:
    if not args_delta or not args_delta.strip().startswith("{"):
        return False
    try:
        parsed = json.loads(args_delta)
        if not isinstance(parsed, dict):
            return False
        return (
            "sql" in parsed or "code" in parsed or "tasks" in parsed or "goal" in parsed
        )
    except json.JSONDecodeError:
        return False


def chunk_has_non_empty_code_or_sql(args_delta: str, tool_name: str) -> bool:
    """True if the chunk parses as JSON and has non-empty code/sql content.
    Used to avoid overwriting accumulated args with empty or partial chunks."""
    if not args_delta or not isinstance(args_delta, str):
        return False
    try:
        parsed = json.loads(args_delta)
    except json.JSONDecodeError:
        return False
    if not isinstance(parsed, dict):
        return False
    if tool_name == "run_sql":
        sql = _extract_text_field(
            parsed.get("sql") or parsed.get("query") or parsed.get("statement")
        )
        return sql is not None
    if tool_name == "run_python":
        code = _extract_text_field(
            parsed.get("code")
            or parsed.get("python")
            or parsed.get("script")
            or parsed.get("input")
        )
        return code is not None
    if tool_name == "spawn_subagents":
        tasks = parsed.get("tasks")
        goal = _extract_text_field(parsed.get("goal"))
        return goal is not None or (isinstance(tasks, list) and len(tasks) > 0)
    return "sql" in parsed or "code" in parsed or "tasks" in parsed or "goal" in parsed


def _extract_text_field(value: Any) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _strip_markdown_code_fence(value: str) -> str:
    stripped = value.strip()
    if not stripped.startswith("```"):
        return value

    lines = stripped.splitlines()
    if not lines:
        return value

    if lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _unwrap_embedded_argument_payload(value: str, *, field: str) -> str:
    """
    If a field like `code`/`sql` is itself a JSON object string, unwrap it.

    Example input:
      '{"code":"print(1)","timeout":30}'
    returns:
      'print(1)'
    """
    candidate = _strip_markdown_code_fence(value).strip()
    if not candidate.startswith("{"):
        return candidate

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return candidate

    if not isinstance(parsed, dict):
        return candidate

    direct = _extract_text_field(parsed.get(field))
    if direct is not None:
        return direct

    aliases = {
        "code": ("python", "script", "input", "query"),
        "sql": ("query", "statement"),
    }
    for alias in aliases.get(field, ()):
        value_alias = _extract_text_field(parsed.get(alias))
        if value_alias is not None:
            return value_alias

    return candidate


def _normalize_timeout_or_limit(
    *,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    result = dict(arguments)
    if tool_name == "run_sql":
        raw_limit = result.get("limit", 100)
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            limit = 100
        result["limit"] = max(1, min(limit, 10_000))
    elif tool_name == "run_python":
        raw_timeout = result.get("timeout", 30)
        try:
            timeout = int(raw_timeout)
        except (TypeError, ValueError):
            timeout = 30
        result["timeout"] = max(1, min(timeout, 120))
    elif tool_name == "spawn_subagents":
        raw_timeout = result.get("timeout_s", 300)
        try:
            timeout_s = int(raw_timeout)
        except (TypeError, ValueError):
            timeout_s = 300
        result["timeout_s"] = max(1, min(timeout_s, 1800))

        raw_iterations = result.get("max_iterations", 8)
        try:
            max_iterations = int(raw_iterations)
        except (TypeError, ValueError):
            max_iterations = 8
        result["max_iterations"] = max(1, min(max_iterations, 100))

        raw_max_subagents = result.get("max_subagents", 8)
        try:
            max_subagents = int(raw_max_subagents)
        except (TypeError, ValueError):
            max_subagents = 8
        result["max_subagents"] = max(1, min(max_subagents, 50))

        raw_max_parallel = result.get("max_parallel_subagents", 3)
        try:
            max_parallel = int(raw_max_parallel)
        except (TypeError, ValueError):
            max_parallel = 3
        result["max_parallel_subagents"] = max(1, min(max_parallel, 10))
    return result


def _maybe_extract_nested_arguments(raw: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None

    if not isinstance(parsed, dict):
        return None

    nested = parsed.get("arguments")
    if isinstance(nested, dict):
        return nested
    if isinstance(nested, str):
        try:
            nested_parsed = json.loads(nested)
        except json.JSONDecodeError:
            return None
        if isinstance(nested_parsed, dict):
            return nested_parsed
    return parsed


def normalize_tool_arguments(
    tool_name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    resolved_tool = (tool_name or "").strip()
    result = dict(arguments or {})

    if resolved_tool == "run_sql":
        sql = _extract_text_field(result.get("sql"))
        if sql is None:
            sql = _extract_text_field(result.get("query"))
        if sql is None:
            sql = _extract_text_field(result.get("statement"))
        if sql is not None:
            result["sql"] = _unwrap_embedded_argument_payload(sql, field="sql")

    if resolved_tool == "run_python":
        code = _extract_text_field(result.get("code"))
        if code is None:
            code = _extract_text_field(result.get("python"))
        if code is None:
            code = _extract_text_field(result.get("script"))
        if code is None:
            code = _extract_text_field(result.get("input"))
        if code is None:
            code = _extract_text_field(result.get("query"))
        if code is not None:
            result["code"] = _unwrap_embedded_argument_payload(code, field="code")

    raw = result.get("_raw")
    if isinstance(raw, str) and raw.strip():
        nested = _maybe_extract_nested_arguments(raw)
        if isinstance(nested, dict):
            merged = {k: v for k, v in nested.items() if k != "_raw"}
            merged.update({k: v for k, v in result.items() if k != "_raw"})
            result = merged

        code_field = "sql" if resolved_tool == "run_sql" else "code"
        raw_looks_complete = raw.strip().endswith("}")
        # Try regex extraction whenever we lack code/sql - including incomplete _raw
        if code_field not in result and (
            resolved_tool not in {"run_sql", "run_python"} or raw_looks_complete
        ):
            pattern = rf'"{code_field}"\s*:\s*"((?:[^"\\]|\\.)*)"'
            match = re.search(pattern, raw, re.DOTALL)
            if match:
                try:
                    result[code_field] = json.loads(f'"{match.group(1)}"')
                except (json.JSONDecodeError, ValueError):
                    pass
        # Also try regex on incomplete _raw for run_sql/run_python when still missing
        if code_field not in result and resolved_tool in {"run_sql", "run_python"}:
            pattern = rf'"{code_field}"\s*:\s*"((?:[^"\\]|\\.)*)"'
            match = re.search(pattern, raw, re.DOTALL)
            if match:
                try:
                    decoded = json.loads(f'"{match.group(1)}"')
                    if isinstance(decoded, str) and decoded.strip():
                        result[code_field] = decoded
                except (json.JSONDecodeError, ValueError):
                    pass

        if code_field not in result:
            raw_stripped = raw.strip()
            if raw_stripped and resolved_tool not in {"run_sql", "run_python"}:
                result[code_field] = raw_stripped

    if resolved_tool == "run_sql" and not isinstance(result.get("sql"), str):
        result.pop("sql", None)
    if resolved_tool == "run_python" and not isinstance(result.get("code"), str):
        result.pop("code", None)

    if resolved_tool == "run_sql":
        sql = _extract_text_field(result.get("sql"))
        if sql is not None:
            result["sql"] = _unwrap_embedded_argument_payload(sql, field="sql")
    if resolved_tool == "run_python":
        code = _extract_text_field(result.get("code"))
        if code is not None:
            result["code"] = _unwrap_embedded_argument_payload(code, field="code")

    result.pop("_raw", None)
    return _normalize_timeout_or_limit(tool_name=resolved_tool, arguments=result)
