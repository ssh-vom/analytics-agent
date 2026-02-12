from __future__ import annotations

import json
import re
from typing import Any

_RERUN_HINT_PATTERN = re.compile(
    r"\b(rerun|re-run|run again|regenerate|recompute|refresh|overwrite|rebuild)\b",
    re.IGNORECASE,
)

_PYTHON_REQUIRED_HINT_PATTERN = re.compile(
    r"\b(python|plot|chart|graph|visuali[sz]e|matplotlib|pandas|data\s*frame|histogram|scatter|heatmap)\b",
    re.IGNORECASE,
)


def user_requested_rerun(message: str) -> bool:
    if not isinstance(message, str):
        return False
    return bool(_RERUN_HINT_PATTERN.search(message))


def required_terminal_tools(
    *,
    message: str,
    requested_output_type: str | None,
) -> set[str]:
    if not isinstance(message, str):
        return set()

    visible_message = re.sub(
        r"<context>[\s\S]*?</context>",
        "",
        message,
        flags=re.IGNORECASE,
    ).strip()
    if not visible_message:
        return set()

    if _PYTHON_REQUIRED_HINT_PATTERN.search(visible_message):
        return {"run_python"}

    return set()


def missing_required_terminal_tools(
    *,
    required_tools: set[str],
    sql_success_count: int,
    python_success_count: int,
) -> set[str]:
    missing: set[str] = set()

    if "run_sql" in required_tools and sql_success_count <= 0:
        missing.add("run_sql")
    if "run_python" in required_tools and python_success_count <= 0:
        missing.add("run_python")

    return missing


def python_code_has_executable_content(code: str) -> bool:
    if not isinstance(code, str):
        return False
    for raw_line in code.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        return True
    return False


def validate_tool_payload(
    *,
    tool_name: str,
    arguments: dict[str, Any],
) -> str | None:
    if tool_name == "run_sql":
        sql = arguments.get("sql")
        if not isinstance(sql, str) or not sql.strip():
            return "run_sql requires a non-empty `sql` string"
        return None

    if tool_name == "run_python":
        code = arguments.get("code")
        if not isinstance(code, str) or not code.strip():
            return "run_python requires a non-empty `code` string"
        if not python_code_has_executable_content(code):
            return (
                "run_python `code` must include executable Python and cannot be "
                "comments/whitespace only"
            )
        return None

    return None


def _checkpoint_preview(data_intent_summary: dict[str, Any] | None) -> str:
    checkpoint = (
        json.dumps(data_intent_summary, ensure_ascii=True, default=str)
        if data_intent_summary
        else "none"
    )
    if len(checkpoint) > 900:
        checkpoint = checkpoint[:900] + "...(truncated)"
    return checkpoint


def build_tool_payload_correction_message(
    *,
    tool_name: str,
    payload_error: str,
    data_intent_summary: dict[str, Any] | None,
) -> str:
    if tool_name == "run_python":
        example_args = json.dumps(
            {
                "code": "import pandas as pd\nprint(LATEST_SQL_DF.head())",
                "timeout": 30,
            },
            ensure_ascii=True,
        )
        return (
            "Correction: your last run_python payload was invalid ("
            + payload_error
            + "). Emit a fresh run_python tool call now with a non-empty executable `code` "
            "string and optional integer `timeout`. "
            "Do not emit empty args, nested JSON-in-code, or comments-only code. "
            "Example args: "
            + example_args
            + ". SQL checkpoint: "
            + _checkpoint_preview(data_intent_summary)
        )

    if tool_name == "run_sql":
        example_args = json.dumps(
            {"sql": "SELECT * FROM your_table LIMIT 50", "limit": 50},
            ensure_ascii=True,
        )
        return (
            "Correction: your last run_sql payload was invalid ("
            + payload_error
            + "). Emit a fresh run_sql tool call with a non-empty `sql` string. "
            "Example args: " + example_args
        )

    return (
        "Correction: your previous tool payload was invalid ("
        + payload_error
        + "). Emit a valid tool call payload next."
    )


def build_required_tool_enforcement_message(
    *,
    missing_required_tools: set[str],
    data_intent_summary: dict[str, Any] | None,
) -> str:
    missing_sorted = sorted(missing_required_tools)
    if missing_sorted == ["run_python"]:
        return (
            "Your previous reply skipped required Python execution. "
            "Emit a run_python tool call now with non-empty executable `code` (not a plan). "
            "Use LATEST_SQL_RESULT/LATEST_SQL_DF and the SQL checkpoint when relevant. "
            "SQL checkpoint: " + _checkpoint_preview(data_intent_summary)
        )

    return (
        "Your previous reply skipped required tool execution ("
        + ", ".join(missing_sorted)
        + "). Emit the required tool call(s) now before finalizing."
    )


def is_retryable_python_preflight_error(tool_result: dict[str, Any]) -> bool:
    if not isinstance(tool_result, dict):
        return False

    if tool_result.get("retryable") is not True:
        return False

    error_code = str(tool_result.get("error_code") or "").strip().lower()
    return error_code in {
        "python_compile_error",
        "python_execution_payload_compile_error",
        "python_tool_invocation_forbidden",
    }


def build_python_preflight_retry_message(
    *,
    tool_result: dict[str, Any],
    data_intent_summary: dict[str, Any] | None,
) -> str:
    error_code = str(tool_result.get("error_code") or "python_preflight_error")
    error_text = str(tool_result.get("error") or "Python preflight failed")
    return (
        "Correction: the previous run_python execution failed preflight "
        f"({error_code}: {error_text}). Emit a fresh run_python tool call with "
        "valid executable Python in `code` (not nested JSON, not comments-only, and "
        "no backend tool function calls). Use LATEST_SQL_RESULT/LATEST_SQL_DF as input. "
        "SQL checkpoint: " + _checkpoint_preview(data_intent_summary)
    )


def is_empty_python_payload_error(tool_result: dict[str, Any]) -> bool:
    error = tool_result.get("error") if isinstance(tool_result, dict) else None
    if not isinstance(error, str):
        return False
    lowered = error.lower()
    return "non-empty 'code'" in lowered or "empty `code`" in lowered
