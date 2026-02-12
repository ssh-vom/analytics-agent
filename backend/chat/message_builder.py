from __future__ import annotations

import json
from typing import Any

from chat.llm_client import ChatMessage

SYSTEM_PROMPT = """You are an AI assistant with access to tools for data analysis. You have two main tools available:

1. **run_sql**: Execute SQL queries against a DuckDB database containing user data. Use this when:
   - The user asks questions about data, tables, or specific values
   - You need to retrieve, filter, aggregate, or explore data
   - The request involves counting, summing, or analyzing structured data

2. **run_python**: Execute Python code in a sandboxed environment. Use this when:
   - You need to create visualizations, charts, or plots (matplotlib is available)
   - Complex data manipulation or statistical analysis is required
   - Working with files, dataframes, or generating data insights

   For plots: use matplotlib (e.g. plt.plot, plt.bar) and call plt.savefig('filename.png') 
   to persist the image. The working directory is /workspace; saved files become viewable artifacts.

**CRITICAL - You MUST execute tools:**
- NEVER respond with only a plan or description of what you would do. You MUST actually call run_sql and/or run_python.
- When the user asks for analysis, data exploration, or visualizations: call the tools first, then summarize the results.
- If you need to explore the schema: call run_sql with a query like "SELECT * FROM table LIMIT 5" or "PRAGMA table_info(table)".
- Do not say "Let me..." or "I'll..." without immediately following with a tool call in the same response.

**Guidelines:**
- Call SQL first to retrieve the data, then use Python if you need to visualize or further analyze it
- After getting results, provide insights and context, not just raw data
- If a query might be expensive or return many rows, add appropriate LIMIT clauses
- For run_python tool calls, arguments MUST be valid JSON with a non-empty `code` string containing executable Python.
- Never emit empty `{}` tool arguments, empty `code`, nested JSON-in-`code`, or comments-only placeholder code.
- Never write Python code that calls backend tools such as run_sql(), run_python(), or time_travel().
- Tools must be invoked as tool calls only. Python code must be standard Python (pandas/numpy/matplotlib/etc.) and must not reference tool functions.
- In Python, use `LATEST_SQL_RESULT` (dict) and `LATEST_SQL_DF` (pandas DataFrame, when available), which are auto-injected from the latest successful SQL result.
- Do not invent or simulate dataset rows in Python. If more fields are needed, call run_sql again to fetch exactly those columns.
- If the user context includes `output_type=report`, create report-ready artifacts: save chart images (PNG) and a downloadable PDF named `report.pdf`.
- For PDF generation in Python, use `from matplotlib.backends.backend_pdf import PdfPages` and write the report pages to `report.pdf`.
- Always review the artifact inventory before creating new files. Reuse existing artifacts whenever possible instead of regenerating identical outputs.

The user is expecting you to help them explore and understand their data. Use the appropriate tool(s) to deliver helpful analysis and insights."""

_ARTIFACT_INVENTORY_MAX_ITEMS = 40
_TOOL_SUMMARY_MAX_CHARS = 3_000


def _truncate_text(value: Any, *, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + "...(truncated)"


def _collect_artifact_inventory(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {event.get("id"): event for event in events}
    deduped_by_name: dict[str, dict[str, Any]] = {}

    for event in events:
        if event.get("type") != "tool_result_python":
            continue

        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue

        artifacts = payload.get("artifacts")
        if not isinstance(artifacts, list):
            continue

        parent = by_id.get(event.get("parent_event_id")) or {}
        parent_payload = parent.get("payload") if isinstance(parent, dict) else {}
        source_call_id = None
        if isinstance(parent_payload, dict):
            raw_call_id = parent_payload.get("call_id")
            if isinstance(raw_call_id, str) and raw_call_id.strip():
                source_call_id = raw_call_id.strip()

        created_at = event.get("created_at")
        created_at_text = str(created_at) if created_at is not None else ""

        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            name = str(artifact.get("name") or "").strip()
            if not name:
                continue

            name_key = name.lower()
            entry = {
                "artifact_id": str(artifact.get("artifact_id") or ""),
                "name": name,
                "type": str(artifact.get("type") or "file"),
                "created_at": created_at_text,
                "source_call_id": source_call_id,
                "producer": "run_python",
            }

            if name_key in deduped_by_name:
                del deduped_by_name[name_key]
            deduped_by_name[name_key] = entry

    inventory = list(deduped_by_name.values())
    if len(inventory) > _ARTIFACT_INVENTORY_MAX_ITEMS:
        inventory = inventory[-_ARTIFACT_INVENTORY_MAX_ITEMS:]
    return inventory


def _artifact_inventory_system_message(events: list[dict[str, Any]]) -> ChatMessage:
    inventory = _collect_artifact_inventory(events)
    payload = {
        "artifacts": inventory,
        "artifact_count": len(inventory),
        "instructions": (
            "Before creating new files, check this inventory and reuse existing "
            "artifacts when they satisfy the request."
        ),
    }
    return ChatMessage(
        role="system",
        content=(
            "Artifact inventory for this worldline (always consult before creating files):\n"
            + json.dumps(payload, ensure_ascii=True, default=str)
        ),
    )


def _summarize_sql_tool_result(payload: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "error": payload.get("error"),
        "execution_ms": payload.get("execution_ms"),
    }

    columns = payload.get("columns")
    if isinstance(columns, list):
        summary["columns"] = [
            str(column.get("name") or "")
            for column in columns
            if isinstance(column, dict)
        ][:32]

    rows = payload.get("rows")
    if isinstance(rows, list):
        summary["row_count"] = len(rows)
        if rows:
            summary["sample_rows"] = rows[:5]

    return summary


def _summarize_python_tool_result(payload: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "error": payload.get("error"),
        "execution_ms": payload.get("execution_ms"),
    }

    artifacts = payload.get("artifacts")
    if isinstance(artifacts, list):
        compact_artifacts = []
        for artifact in artifacts[:40]:
            if not isinstance(artifact, dict):
                continue
            compact_artifacts.append(
                {
                    "artifact_id": str(artifact.get("artifact_id") or ""),
                    "name": str(artifact.get("name") or ""),
                    "type": str(artifact.get("type") or "file"),
                }
            )
        summary["artifact_count"] = len(compact_artifacts)
        summary["artifacts"] = compact_artifacts

    stdout = payload.get("stdout")
    if stdout:
        summary["stdout_tail"] = _truncate_text(stdout, limit=700)
    stderr = payload.get("stderr")
    if stderr:
        summary["stderr_tail"] = _truncate_text(stderr, limit=500)

    previews = payload.get("previews")
    if isinstance(previews, dict):
        dataframes = previews.get("dataframes")
        if isinstance(dataframes, list) and dataframes:
            summary["preview_dataframes"] = dataframes[:3]

    return summary


def _serialize_tool_result(event_type: str, payload: dict[str, Any]) -> str:
    if event_type == "tool_result_sql":
        summary_payload = _summarize_sql_tool_result(payload)
    elif event_type == "tool_result_python":
        summary_payload = _summarize_python_tool_result(payload)
    else:
        summary_payload = payload

    summary = json.dumps(summary_payload, ensure_ascii=True, default=str)
    if len(summary) > _TOOL_SUMMARY_MAX_CHARS:
        summary = summary[:_TOOL_SUMMARY_MAX_CHARS] + "...(truncated)"
    return summary


def build_llm_messages_from_events(events: list[dict[str, Any]]) -> list[ChatMessage]:
    messages: list[ChatMessage] = [
        ChatMessage(role="system", content=SYSTEM_PROMPT),
        _artifact_inventory_system_message(events),
    ]

    events_chrono = list(events)
    by_id = {e["id"]: e for e in events}

    pending_plan: str | None = None
    pending_tool_calls: list[dict[str, Any]] = []
    assistant_emitted_for_turn = False

    for event in events_chrono:
        event_type = str(event.get("type") or "")
        payload = event.get("payload", {})

        if event_type == "user_message":
            pending_plan = None
            pending_tool_calls = []
            assistant_emitted_for_turn = False
            text = payload.get("text")
            if text:
                messages.append(ChatMessage(role="user", content=str(text)))
            continue

        if event_type == "assistant_plan":
            pending_plan = (payload.get("text") or "").strip() or None
            pending_tool_calls = []
            assistant_emitted_for_turn = False
            continue

        if event_type == "assistant_message":
            if pending_tool_calls and not assistant_emitted_for_turn:
                tool_calls_spec = [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["arguments"] or {}),
                        },
                    }
                    for tc in pending_tool_calls
                ]
                content = (pending_plan or "").strip()
                messages.append(
                    ChatMessage(
                        role="assistant",
                        content=content or "",
                        tool_calls=tool_calls_spec,
                    )
                )
                assistant_emitted_for_turn = True

            pending_plan = None
            pending_tool_calls = []
            assistant_emitted_for_turn = False
            text = payload.get("text")
            if text:
                messages.append(ChatMessage(role="assistant", content=str(text)))
            continue

        if event_type in {"tool_call_sql", "tool_call_python"}:
            if assistant_emitted_for_turn:
                pending_tool_calls = []
                pending_plan = None
                assistant_emitted_for_turn = False
            name = "run_sql" if event_type == "tool_call_sql" else "run_python"
            args = dict(payload)
            args.pop("call_id", None)
            call_id = payload.get("call_id") or event.get("id", "")
            pending_tool_calls.append({"id": call_id, "name": name, "arguments": args})
            continue

        if event_type in {"tool_result_sql", "tool_result_python"}:
            parent_id = event.get("parent_event_id") or ""
            parent = by_id.get(parent_id, {})
            call_id = (parent.get("payload") or {}).get("call_id") or parent_id
            summary = _serialize_tool_result(event_type, payload)
            if pending_tool_calls and not assistant_emitted_for_turn:
                tool_calls_spec = [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["arguments"] or {}),
                        },
                    }
                    for tc in pending_tool_calls
                ]
                content = (pending_plan or "").strip()
                messages.append(
                    ChatMessage(
                        role="assistant",
                        content=content or "",
                        tool_calls=tool_calls_spec,
                    )
                )
                assistant_emitted_for_turn = True
            messages.append(
                ChatMessage(
                    role="tool",
                    content=summary,
                    tool_call_id=call_id or None,
                )
            )

    return messages
