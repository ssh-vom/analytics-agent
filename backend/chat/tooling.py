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


def tool_definitions(*, include_python: bool = True) -> list[ToolDefinition]:
    tools: list[ToolDefinition] = [
        ToolDefinition(
            name="run_sql",
            description=(
                "Execute a read-only SQL query against the worldline DuckDB. "
                "Use for table reads and aggregations."
            ),
            input_schema=SQL_TOOL_SCHEMA,
        ),
        ToolDefinition(
            name="time_travel",
            description=(
                "Create a new worldline from a prior event and continue execution there."
            ),
            input_schema=TIME_TRAVEL_TOOL_SCHEMA,
        ),
    ]

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
    return None


def looks_like_complete_tool_args(args_delta: str) -> bool:
    if not args_delta or not args_delta.strip().startswith("{"):
        return False
    try:
        parsed = json.loads(args_delta)
        if not isinstance(parsed, dict):
            return False
        return "sql" in parsed or "code" in parsed
    except json.JSONDecodeError:
        return False


def normalize_tool_arguments(
    tool_name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    if "_raw" not in arguments:
        return arguments
    raw = arguments.get("_raw", "")
    if not isinstance(raw, str):
        return arguments

    code_field = "sql" if (tool_name or "").strip() == "run_sql" else "code"
    pattern = rf'"{code_field}"\s*:\s*"((?:[^"\\]|\\.)*)"'
    match = re.search(pattern, raw, re.DOTALL)
    if not match:
        return arguments

    try:
        extracted = json.loads(f'"{match.group(1)}"')
    except (json.JSONDecodeError, ValueError):
        return arguments

    result = {k: v for k, v in arguments.items() if k != "_raw"}
    result[code_field] = extracted
    if code_field == "sql":
        result.setdefault("limit", 100)
    else:
        result.setdefault("timeout", 30)
    return result


def tool_signature(*, worldline_id: str, tool_call: ToolCall) -> str:
    return json.dumps(
        {
            "worldline_id": worldline_id,
            "name": tool_call.name,
            "arguments": tool_call.arguments or {},
        },
        ensure_ascii=True,
        sort_keys=True,
        default=str,
    )
