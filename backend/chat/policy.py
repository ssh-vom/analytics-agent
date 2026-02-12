from __future__ import annotations

import re

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
