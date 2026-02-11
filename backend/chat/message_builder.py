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
- Never write Python code that calls backend tools such as run_sql(), run_python(), or time_travel().
- Tools must be invoked as tool calls only. Python code must be standard Python (pandas/numpy/matplotlib/etc.) and must not reference tool functions.
- In Python, use `LATEST_SQL_RESULT` (dict) and `LATEST_SQL_DF` (pandas DataFrame, when available), which are auto-injected from the latest successful SQL result.
- Do not invent or simulate dataset rows in Python. If more fields are needed, call run_sql again to fetch exactly those columns.

The user is expecting you to help them explore and understand their data. Use the appropriate tool(s) to deliver helpful analysis and insights."""


def build_llm_messages_from_events(events: list[dict[str, Any]]) -> list[ChatMessage]:
    messages: list[ChatMessage] = [ChatMessage(role="system", content=SYSTEM_PROMPT)]

    events_chrono = list(events)
    by_id = {e["id"]: e for e in events}

    pending_plan: str | None = None
    pending_tool_calls: list[dict[str, Any]] = []
    assistant_emitted_for_turn = False

    for event in events_chrono:
        event_type = event.get("type")
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
            name = "run_sql" if "sql" in event_type else "run_python"
            args = dict(payload)
            args.pop("call_id", None)
            call_id = payload.get("call_id") or event.get("id", "")
            pending_tool_calls.append({"id": call_id, "name": name, "arguments": args})
            continue

        if event_type in {"tool_result_sql", "tool_result_python"}:
            parent_id = event.get("parent_event_id") or ""
            parent = by_id.get(parent_id, {})
            call_id = (parent.get("payload") or {}).get("call_id") or parent_id
            summary = json.dumps(payload, ensure_ascii=True, default=str)
            if len(summary) > 2_000:
                summary = summary[:2_000] + "...(truncated)"
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
