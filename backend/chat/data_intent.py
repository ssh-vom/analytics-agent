from __future__ import annotations

import json
import re
from typing import Any

from chat.llm_client import ChatMessage

DATA_INTENT_HEADER = "SQL-to-Python data checkpoint"


def data_intent_from_events(
    events: list[dict[str, Any]],
) -> dict[str, Any] | None:
    by_id = {event.get("id"): event for event in events}

    for event in reversed(events):
        if event.get("type") != "tool_result_sql":
            continue

        payload = event.get("payload")
        if not isinstance(payload, dict) or payload.get("error"):
            continue

        parent = by_id.get(event.get("parent_event_id"))
        parent_payload = parent.get("payload") if isinstance(parent, dict) else None
        sql = None
        if isinstance(parent_payload, dict):
            raw_sql = parent_payload.get("sql")
            if isinstance(raw_sql, str) and raw_sql.strip():
                sql = raw_sql.strip()

        return build_data_intent_summary(sql=sql, sql_result=payload)

    return None


def build_data_intent_summary(
    *,
    sql: Any,
    sql_result: dict[str, Any],
) -> dict[str, Any] | None:
    if not isinstance(sql_result, dict) or sql_result.get("error"):
        return None

    columns_meta = sql_result.get("columns")
    if not isinstance(columns_meta, list):
        columns_meta = []

    columns: list[str] = []
    dimensions: list[str] = []
    measures: list[str] = []

    for column in columns_meta:
        if not isinstance(column, dict):
            continue

        name = str(column.get("name") or "").strip()
        if not name:
            continue
        columns.append(name)

        col_type = str(column.get("type") or "")
        if _is_numeric_sql_type(col_type):
            measures.append(name)
        else:
            dimensions.append(name)

    rows = sql_result.get("rows")
    row_count = sql_result.get("row_count")
    if not isinstance(row_count, int) or row_count < 0:
        row_count = len(rows) if isinstance(rows, list) else 0

    preview_count = sql_result.get("preview_count")
    if not isinstance(preview_count, int) or preview_count < 0:
        preview_count = len(rows) if isinstance(rows, list) else 0

    time_columns = [
        name
        for name in columns
        if re.search(r"(date|time|month|year|day|week|quarter)", name, re.IGNORECASE)
    ]

    sql_preview = ""
    if isinstance(sql, str) and sql.strip():
        sql_preview = " ".join(sql.strip().split())
        if len(sql_preview) > 220:
            sql_preview = sql_preview[:220] + "..."

    return {
        "source": "latest_successful_sql",
        "row_count": row_count,
        "preview_count": preview_count,
        "columns": columns[:24],
        "dimensions": dimensions[:16],
        "measures": measures[:16],
        "time_columns": time_columns[:8],
        "sql_preview": sql_preview,
    }


def _is_numeric_sql_type(type_name: str) -> bool:
    if not isinstance(type_name, str):
        return False
    lowered = type_name.strip().lower()
    return any(
        token in lowered
        for token in (
            "int",
            "decimal",
            "double",
            "float",
            "real",
            "numeric",
            "hugeint",
        )
    )


def _render_data_intent_message(data_intent_summary: dict[str, Any]) -> str:
    payload = {
        "data_intent": data_intent_summary,
        "instructions": (
            "Use this checkpoint when planning follow-up SQL/Python steps. "
            "If Python is needed, reference LATEST_SQL_RESULT/LATEST_SQL_DF "
            "instead of refetching identical data."
        ),
    }
    return f"{DATA_INTENT_HEADER} (always-on memory):\n" + json.dumps(
        payload,
        ensure_ascii=True,
        default=str,
    )


def upsert_data_intent_message(
    messages: list[ChatMessage],
    data_intent_summary: dict[str, Any] | None,
) -> None:
    existing_index = None
    for index, message in enumerate(messages):
        if message.role == "system" and message.content.startswith(DATA_INTENT_HEADER):
            existing_index = index
            break

    if data_intent_summary is None:
        if existing_index is not None:
            del messages[existing_index]
        return

    memory_message = ChatMessage(
        role="system",
        content=_render_data_intent_message(data_intent_summary),
    )
    if existing_index is not None:
        messages[existing_index] = memory_message
        return

    insert_index = 2 if len(messages) >= 2 else len(messages)
    messages.insert(insert_index, memory_message)
