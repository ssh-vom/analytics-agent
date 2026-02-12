from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from fastapi import HTTPException

from debug_log import debug_log as _debug_log
from chat.event_store import (
    append_worldline_event,
    events_since_rowid,
    load_event_by_id,
    max_worldline_rowid,
)
from chat.llm_client import (
    ChatMessage,
    LlmClient,
    LlmResponse,
    ToolCall,
    ToolDefinition,
)
from chat.message_builder import build_llm_messages_from_events
from chat.streaming_bridge import stream_llm_response
from chat.tooling import (
    normalize_tool_arguments,
    tool_definitions,
    tool_name_to_delta_type,
    tool_signature,
)
from tools import (
    PythonToolRequest,
    SqlToolRequest,
    execute_python_tool,
    execute_sql_tool,
)
from worldlines import get_worldline_events
from worldline_service import BranchOptions, WorldlineService

logger = logging.getLogger(__name__)

_ARTIFACT_INVENTORY_HEADER = "Artifact inventory for this worldline"
_DATA_INTENT_HEADER = "SQL-to-Python data checkpoint"
_ARTIFACT_INVENTORY_MAX_ITEMS = 40
_RECENT_SIGNATURE_WINDOW = 24
_MAX_INVALID_TOOL_PAYLOAD_RETRIES: dict[str, int] = {
    "run_sql": 2,
    "run_python": 3,
}
_ARTIFACT_FILENAME_PATTERN = re.compile(
    r"['\"]([^'\"\n\r]+\.(?:csv|png|jpg|jpeg|pdf|svg|json|parquet|xlsx|html))['\"]",
    re.IGNORECASE,
)
_RERUN_HINT_PATTERN = re.compile(
    r"\b(rerun|re-run|run again|regenerate|recompute|refresh|overwrite|rebuild)\b",
    re.IGNORECASE,
)
_PYTHON_REQUIRED_HINT_PATTERN = re.compile(
    r"\b(python|plot|chart|graph|visuali[sz]e|matplotlib|pandas|data\s*frame|histogram|scatter|heatmap)\b",
    re.IGNORECASE,
)
_MAX_REQUIRED_TOOL_ENFORCEMENT_RETRIES = 2

_TURN_STATE_TRANSITIONS: dict[str, set[str]] = {
    "planning": {"data_fetching", "analyzing", "presenting", "completed", "error"},
    "data_fetching": {"analyzing", "presenting", "error", "completed"},
    "analyzing": {"data_fetching", "presenting", "error", "completed"},
    "presenting": {"analyzing", "error", "completed"},
    "error": {"planning", "completed"},
    "completed": set(),
}

_AUTO_REPORT_CODE = """
from datetime import datetime
from textwrap import shorten

import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import FancyBboxPatch, Rectangle

try:
    import pandas as pd
except Exception:
    pd = None


COLORS = {
    "ink": "#0F172A",
    "muted": "#475569",
    "paper": "#F7FAFC",
    "card": "#FFFFFF",
    "brand": "#1D4ED8",
    "brand_dark": "#1E3A8A",
    "accent": "#0EA5E9",
    "ok": "#047857",
    "grid": "#CBD5E1",
}

plt.rcParams.update(
    {
        "figure.facecolor": COLORS["paper"],
        "axes.facecolor": COLORS["card"],
        "axes.edgecolor": COLORS["grid"],
        "axes.labelcolor": COLORS["ink"],
        "xtick.color": COLORS["muted"],
        "ytick.color": COLORS["muted"],
        "font.size": 10,
        "axes.titleweight": "bold",
    }
)


def _load_dataframe():
    frame = None
    if "LATEST_SQL_DF" in globals() and LATEST_SQL_DF is not None:
        try:
            frame = LATEST_SQL_DF.copy()
        except Exception:
            frame = LATEST_SQL_DF

    if frame is None and "LATEST_SQL_RESULT" in globals() and pd is not None:
        payload = LATEST_SQL_RESULT or {}
        columns = [
            column.get("name", "")
            for column in (payload.get("columns") or [])
            if isinstance(column, dict)
        ]
        rows = payload.get("rows") or []
        try:
            frame = pd.DataFrame(rows, columns=columns)
        except Exception:
            frame = pd.DataFrame()

    if pd is not None and frame is None:
        frame = pd.DataFrame()
    return frame


def _numeric_columns(frame):
    if pd is None:
        return []
    out = []
    for col in frame.columns:
        try:
            series = pd.to_numeric(frame[col], errors="coerce")
            if series.notna().sum() >= 2:
                out.append(col)
        except Exception:
            continue
    return out


def _categorical_columns(frame, numeric_cols):
    out = []
    for col in frame.columns:
        if col in numeric_cols:
            continue
        try:
            non_null = frame[col].dropna()
            if non_null.empty:
                continue
            if non_null.nunique() <= 40:
                out.append(col)
        except Exception:
            continue
    return out


def _datetime_column(frame):
    if pd is None:
        return None
    for col in frame.columns:
        try:
            parsed = pd.to_datetime(frame[col], errors="coerce", utc=False)
            valid_ratio = float(parsed.notna().mean()) if len(parsed) else 0.0
            if valid_ratio >= 0.7 and parsed.nunique(dropna=True) >= 3:
                return col
        except Exception:
            continue
    return None


def _fmt_num(value):
    try:
        number = float(value)
    except Exception:
        return str(value)
    magnitude = abs(number)
    if magnitude >= 1_000_000_000:
        return f"{number/1_000_000_000:.2f}B"
    if magnitude >= 1_000_000:
        return f"{number/1_000_000:.2f}M"
    if magnitude >= 1_000:
        return f"{number/1_000:.2f}K"
    if magnitude >= 100:
        return f"{number:,.0f}"
    if magnitude >= 10:
        return f"{number:,.1f}"
    return f"{number:,.2f}"


def _fmt_cell(value):
    if value is None:
        return ""
    if isinstance(value, float):
        return _fmt_num(value)
    if isinstance(value, int):
        return f"{value:,}"
    text = str(value)
    return shorten(text.replace("\n", " "), width=28, placeholder="...")


def _render_cover(pdf, *, rows, cols, num_cols, cat_cols, generated_at):
    fig = plt.figure(figsize=(11, 8.5), facecolor=COLORS["paper"])
    ax = fig.add_axes([0, 0, 1, 1])
    ax.axis("off")

    ax.add_patch(Rectangle((0, 0.78), 1, 0.22, transform=ax.transAxes, color=COLORS["brand_dark"]))
    ax.text(0.06, 0.90, "TextQL Executive Report", color="white", fontsize=28, fontweight="bold", transform=ax.transAxes)
    ax.text(0.06, 0.84, f"Generated {generated_at}", color="#DBEAFE", fontsize=11, transform=ax.transAxes)

    metrics = [
        ("Rows", f"{rows:,}"),
        ("Columns", f"{cols:,}"),
        ("Numeric Fields", f"{num_cols:,}"),
        ("Categorical Fields", f"{cat_cols:,}"),
    ]
    x_positions = [0.06, 0.30, 0.54, 0.78]
    for (label, value), x in zip(metrics, x_positions):
        card = FancyBboxPatch(
            (x, 0.52),
            0.16,
            0.18,
            boxstyle="round,pad=0.012,rounding_size=0.01",
            facecolor=COLORS["card"],
            edgecolor=COLORS["grid"],
            linewidth=1.0,
            transform=ax.transAxes,
        )
        ax.add_patch(card)
        ax.text(x + 0.08, 0.63, value, ha="center", va="center", fontsize=18, fontweight="bold", color=COLORS["brand"], transform=ax.transAxes)
        ax.text(x + 0.08, 0.56, label, ha="center", va="center", fontsize=10, color=COLORS["muted"], transform=ax.transAxes)

    ax.text(
        0.06,
        0.40,
        "This report summarizes the latest SQL result and includes key descriptive charts.",
        color=COLORS["ink"],
        fontsize=12,
        transform=ax.transAxes,
    )
    ax.text(
        0.06,
        0.34,
        "Tip: narrow your SQL result to focused business slices for the most actionable report.",
        color=COLORS["muted"],
        fontsize=10,
        transform=ax.transAxes,
    )
    pdf.savefig(fig)
    plt.close(fig)


def _render_preview_page(pdf, frame):
    fig = plt.figure(figsize=(11, 8.5), facecolor=COLORS["paper"])
    fig.suptitle("Data Snapshot", fontsize=18, fontweight="bold", color=COLORS["ink"], y=0.97)
    ax = fig.add_axes([0.04, 0.08, 0.92, 0.82])
    ax.axis("off")

    max_cols = min(7, len(frame.columns))
    max_rows = min(14, len(frame.index))
    preview = frame.iloc[:max_rows, :max_cols].copy()

    col_labels = [shorten(str(col), width=20, placeholder="...") for col in preview.columns]
    rows = [[_fmt_cell(v) for v in row] for row in preview.values.tolist()]

    if not rows:
        ax.text(0.02, 0.5, "No rows available in the latest SQL result.", fontsize=12, color=COLORS["muted"], transform=ax.transAxes)
        pdf.savefig(fig)
        plt.close(fig)
        return

    table = ax.table(
        cellText=rows,
        colLabels=col_labels,
        cellLoc="left",
        colLoc="left",
        bbox=[0.0, 0.0, 1.0, 0.92],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(8.5)

    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor(COLORS["grid"])
        if row == 0:
            cell.set_facecolor("#E2E8F0")
            cell.set_text_props(weight="bold", color=COLORS["ink"])
        elif row % 2 == 0:
            cell.set_facecolor("#F8FAFC")
        else:
            cell.set_facecolor("#FFFFFF")

    ax.text(
        0.0,
        0.95,
        f"Showing first {max_rows} rows and first {max_cols} columns",
        fontsize=10,
        color=COLORS["muted"],
        transform=ax.transAxes,
    )
    pdf.savefig(fig)
    plt.close(fig)


def _render_numeric_summary(pdf, frame, numeric_cols):
    fig = plt.figure(figsize=(11, 8.5), facecolor=COLORS["paper"])
    fig.suptitle("Numeric Field Summary", fontsize=18, fontweight="bold", color=COLORS["ink"], y=0.97)
    ax = fig.add_axes([0.04, 0.08, 0.92, 0.82])
    ax.axis("off")

    if pd is None or not numeric_cols:
        ax.text(0.02, 0.5, "No numeric columns available for descriptive statistics.", fontsize=12, color=COLORS["muted"], transform=ax.transAxes)
        pdf.savefig(fig)
        plt.close(fig)
        return

    limited = numeric_cols[:8]
    stats = []
    for col in limited:
        series = pd.to_numeric(frame[col], errors="coerce").dropna()
        if series.empty:
            continue
        stats.append(
            [
                shorten(str(col), width=24, placeholder="..."),
                f"{int(series.count()):,}",
                _fmt_num(series.mean()),
                _fmt_num(series.median()),
                _fmt_num(series.min()),
                _fmt_num(series.max()),
            ]
        )

    if not stats:
        ax.text(0.02, 0.5, "Numeric columns were detected but no valid numeric values were found.", fontsize=12, color=COLORS["muted"], transform=ax.transAxes)
        pdf.savefig(fig)
        plt.close(fig)
        return

    table = ax.table(
        cellText=stats,
        colLabels=["Column", "Count", "Mean", "Median", "Min", "Max"],
        cellLoc="left",
        colLoc="left",
        bbox=[0.0, 0.05, 1.0, 0.87],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    for (row, _col), cell in table.get_celld().items():
        cell.set_edgecolor(COLORS["grid"])
        if row == 0:
            cell.set_facecolor("#E2E8F0")
            cell.set_text_props(weight="bold", color=COLORS["ink"])
        elif row % 2 == 0:
            cell.set_facecolor("#F8FAFC")
        else:
            cell.set_facecolor("#FFFFFF")

    ax.text(0.0, 0.95, "Computed from the latest SQL result only.", fontsize=10, color=COLORS["muted"], transform=ax.transAxes)
    pdf.savefig(fig)
    plt.close(fig)


def _render_histogram(pdf, frame, numeric_col):
    if pd is None:
        return
    series = pd.to_numeric(frame[numeric_col], errors="coerce").dropna()
    if series.empty:
        return

    fig, ax = plt.subplots(figsize=(11, 8.5), facecolor=COLORS["paper"])
    bins = min(24, max(6, int(series.shape[0] ** 0.5)))
    ax.hist(series, bins=bins, color=COLORS["brand"], alpha=0.9, edgecolor="white")
    ax.set_title(f"Distribution: {numeric_col}", fontsize=16, pad=14)
    ax.set_xlabel(str(numeric_col))
    ax.set_ylabel("Count")
    ax.grid(axis="y", alpha=0.25)
    pdf.savefig(fig)
    plt.close(fig)


def _render_category_bar(pdf, frame, category_col, numeric_col):
    if pd is None:
        return
    working = frame[[category_col, numeric_col]].copy()
    working[numeric_col] = pd.to_numeric(working[numeric_col], errors="coerce")
    working = working.dropna(subset=[numeric_col])
    if working.empty:
        return

    grouped = (
        working.groupby(category_col, dropna=False)[numeric_col]
        .sum()
        .sort_values(ascending=False)
        .head(10)
    )
    if grouped.empty:
        return

    labels = [shorten(str(idx), width=22, placeholder="...") for idx in grouped.index]
    values = grouped.values

    fig, ax = plt.subplots(figsize=(11, 8.5), facecolor=COLORS["paper"])
    bars = ax.bar(labels, values, color=COLORS["accent"], edgecolor="white")
    ax.set_title(f"Top {category_col} by {numeric_col}", fontsize=16, pad=14)
    ax.set_xlabel(str(category_col))
    ax.set_ylabel(f"Sum of {numeric_col}")
    ax.grid(axis="y", alpha=0.25)
    ax.tick_params(axis="x", rotation=20)
    for bar in bars:
        value = bar.get_height()
        ax.text(bar.get_x() + bar.get_width() / 2, value, _fmt_num(value), ha="center", va="bottom", fontsize=8, color=COLORS["muted"])
    pdf.savefig(fig)
    plt.close(fig)


def _render_trend(pdf, frame, datetime_col, numeric_col):
    if pd is None:
        return
    working = frame[[datetime_col, numeric_col]].copy()
    working[datetime_col] = pd.to_datetime(working[datetime_col], errors="coerce", utc=False)
    working[numeric_col] = pd.to_numeric(working[numeric_col], errors="coerce")
    working = working.dropna(subset=[datetime_col, numeric_col])
    if working.empty:
        return

    daily = (
        working.groupby(working[datetime_col].dt.date)[numeric_col]
        .sum()
        .sort_index()
    )
    if daily.shape[0] < 2:
        return

    fig, ax = plt.subplots(figsize=(11, 8.5), facecolor=COLORS["paper"])
    ax.plot(daily.index, daily.values, color=COLORS["ok"], linewidth=2.2, marker="o", markersize=3)
    ax.set_title(f"Trend Over Time: {numeric_col}", fontsize=16, pad=14)
    ax.set_xlabel(str(datetime_col))
    ax.set_ylabel(f"Daily sum of {numeric_col}")
    ax.grid(alpha=0.25)
    fig.autofmt_xdate()
    pdf.savefig(fig)
    plt.close(fig)


df = _load_dataframe()
row_count = int(len(df.index)) if hasattr(df, "index") else 0
column_count = int(len(df.columns)) if hasattr(df, "columns") else 0
numeric_cols = _numeric_columns(df) if pd is not None and hasattr(df, "columns") else []
categorical_cols = _categorical_columns(df, numeric_cols) if pd is not None and hasattr(df, "columns") else []
date_col = _datetime_column(df) if pd is not None and hasattr(df, "columns") else None
generated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

with PdfPages("report.pdf") as pdf:
    _render_cover(
        pdf,
        rows=row_count,
        cols=column_count,
        num_cols=len(numeric_cols),
        cat_cols=len(categorical_cols),
        generated_at=generated_at,
    )

    if pd is not None and hasattr(df, "empty") and not df.empty:
        _render_preview_page(pdf, df)
        _render_numeric_summary(pdf, df, numeric_cols)

        if numeric_cols:
            _render_histogram(pdf, df, numeric_cols[0])
        if categorical_cols and numeric_cols:
            _render_category_bar(pdf, df, categorical_cols[0], numeric_cols[0])
        if date_col and numeric_cols:
            _render_trend(pdf, df, date_col, numeric_cols[0])
    else:
        fig = plt.figure(figsize=(11, 8.5), facecolor=COLORS["paper"])
        ax = fig.add_axes([0, 0, 1, 1])
        ax.axis("off")
        ax.text(0.08, 0.72, "No tabular SQL result was available for report charts.", fontsize=14, color=COLORS["ink"], transform=ax.transAxes)
        ax.text(0.08, 0.66, "Run a SQL query first, then request a report for richer visuals.", fontsize=11, color=COLORS["muted"], transform=ax.transAxes)
        pdf.savefig(fig)
        plt.close(fig)

print("Generated report.pdf")
"""


def _extract_context_value(message: str, key: str) -> str | None:
    match = re.search(r"<context>(.*?)</context>", message, re.IGNORECASE | re.DOTALL)
    if match is None:
        return None

    key_prefix = f"{key.lower()}="
    context_block = match.group(1)
    for raw_line in context_block.splitlines():
        line = raw_line.strip()
        if line.startswith("-"):
            line = line[1:].strip()
        if not line.lower().startswith(key_prefix):
            continue
        return line.split("=", 1)[1].strip()

    return None


def _extract_selected_external_aliases(message: str) -> list[str] | None:
    raw_value = _extract_context_value(message, "connectors")
    if raw_value is None:
        return None

    if not raw_value or raw_value.lower() == "none":
        return []

    selected: list[str] = []
    for token in raw_value.split(","):
        alias = token.strip()
        if alias and alias not in selected:
            selected.append(alias)
    return selected


def _extract_output_type(message: str) -> str | None:
    raw_value = _extract_context_value(message, "output_type")
    if raw_value is None:
        return None

    value = raw_value.strip().lower()
    if value in {"none", "auto"}:
        return None
    if value in {"report", "dashboard"}:
        return value
    return None


def _artifact_is_pdf(artifact: Any) -> bool:
    if not isinstance(artifact, dict):
        return False

    artifact_type = str(artifact.get("type", "")).lower()
    artifact_name = str(artifact.get("name", "")).lower()
    return artifact_type == "pdf" or artifact_name.endswith(".pdf")


def _events_contain_pdf_artifact(events: list[dict[str, Any]]) -> bool:
    for event in events:
        if event.get("type") != "tool_result_python":
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        artifacts = payload.get("artifacts")
        if not isinstance(artifacts, list):
            continue
        if any(_artifact_is_pdf(artifact) for artifact in artifacts):
            return True
    return False


@dataclass
class ChatEngine:
    llm_client: LlmClient
    max_iterations: int = 20
    max_output_tokens: int | None = 1500
    worldline_service: WorldlineService = field(default_factory=WorldlineService)

    async def run_turn(
        self,
        worldline_id: str,
        message: str,
        on_event: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
        on_delta: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        if not message or not message.strip():
            raise HTTPException(status_code=400, detail="message must not be empty")

        allowed_external_aliases = _extract_selected_external_aliases(message)
        requested_output_type = _extract_output_type(message)

        # #region agent log
        _debug_log(
            run_id="initial",
            hypothesis_id="H3_H4",
            location="backend/chat/engine.py:run_turn:start",
            message="Starting chat turn",
            data={
                "worldline_id": worldline_id,
                "message_preview": message[:200],
            },
        )
        # #endregion

        active_worldline_id = worldline_id
        starting_rowid_by_worldline = {
            active_worldline_id: self._max_worldline_rowid(active_worldline_id)
        }
        user_event = self._append_worldline_event(
            worldline_id=active_worldline_id,
            event_type="user_message",
            payload={"text": message},
        )
        if on_event is not None:
            await on_event(active_worldline_id, user_event)

        history_events = await self._load_worldline_events(active_worldline_id)
        messages = build_llm_messages_from_events(list(history_events))
        artifact_inventory = self._artifact_inventory_from_events(history_events)
        data_intent_summary = self._data_intent_from_events(history_events)
        recent_successful_tool_signatures = self._recent_successful_tool_signatures(
            worldline_id=active_worldline_id,
            events=history_events,
            limit=_RECENT_SIGNATURE_WINDOW,
        )
        recent_artifact_names = self._artifact_name_set(artifact_inventory)
        user_requested_rerun = self._user_requested_rerun(message)
        required_terminal_tools = self._required_terminal_tools(
            message=message,
            requested_output_type=requested_output_type,
        )
        # #region agent log
        _debug_log(
            run_id="initial",
            hypothesis_id="H6_H7",
            location="backend/chat/engine.py:run_turn:built_messages",
            message="Built LLM messages for turn",
            data={
                "worldline_id": active_worldline_id,
                "message_count": len(messages),
                "tail": [
                    {
                        "role": msg.role,
                        "content_preview": (msg.content or "")[:140],
                        "has_tool_calls": bool(msg.tool_calls),
                        "tool_call_count": len(msg.tool_calls or []),
                    }
                    for msg in messages[-6:]
                ],
            },
        )
        # #endregion
        final_text: str | None = None
        successful_tool_signatures: set[str] = set()
        successful_tool_results: dict[str, dict[str, Any]] = {}
        turn_artifact_names: set[str] = set()
        sql_success_count = 0
        python_success_count = 0
        empty_response_retries = 0
        required_tool_enforcement_failures = 0
        invalid_tool_payload_failures: dict[str, int] = {
            "run_sql": 0,
            "run_python": 0,
        }
        turn_state = "planning"
        state_transitions: list[dict[str, Any]] = [
            {"from": None, "to": "planning", "reason": "turn_started"}
        ]
        await self._emit_state_transition_delta(
            worldline_id=active_worldline_id,
            transition=state_transitions[0],
            on_delta=on_delta,
        )

        for _ in range(self.max_iterations):
            self._upsert_artifact_inventory_message(messages, artifact_inventory)
            self._upsert_data_intent_message(messages, data_intent_summary)
            # ----- LLM call: stream when on_delta is available, else batch -----
            if on_delta is not None:
                response = await self._stream_llm_response(
                    worldline_id=active_worldline_id,
                    messages=messages,
                    tools=self._tool_definitions(include_python=True),
                    on_delta=on_delta,
                )
            else:
                response = await self.llm_client.generate(
                    messages=messages,
                    tools=self._tool_definitions(include_python=True),
                    max_output_tokens=self.max_output_tokens,
                )

            # ----- Emit assistant_plan if text accompanies tool calls ----------
            if response.text and response.tool_calls:
                plan_event = self._append_worldline_event(
                    worldline_id=active_worldline_id,
                    event_type="assistant_plan",
                    payload={"text": response.text},
                )
                if on_event is not None:
                    await on_event(active_worldline_id, plan_event)

            if response.text:
                messages.append(ChatMessage(role="assistant", content=response.text))

            if response.tool_calls:
                repeated_call_detected = False
                invalid_payload_retry_requested = False
                for raw_tool_call in response.tool_calls:
                    tool_name = (raw_tool_call.name or "").strip()
                    normalized_arguments = normalize_tool_arguments(
                        tool_name,
                        dict(raw_tool_call.arguments or {}),
                    )
                    tool_call = ToolCall(
                        id=raw_tool_call.id,
                        name=raw_tool_call.name,
                        arguments=normalized_arguments,
                    )
                    delta_type = self._tool_name_to_delta_type(tool_name)
                    # #region agent log
                    _debug_log(
                        run_id="initial",
                        hypothesis_id="H3_H4",
                        location="backend/chat/engine.py:run_turn:tool_call",
                        message="Model emitted tool call",
                        data={
                            "worldline_id": active_worldline_id,
                            "tool_name": tool_name,
                            "tool_call_id": tool_call.id,
                            "tool_args_preview": json.dumps(
                                tool_call.arguments or {},
                                ensure_ascii=True,
                                default=str,
                            )[:220],
                        },
                    )
                    # #endregion

                    if tool_name == "run_sql":
                        turn_state = await self._transition_state_and_emit(
                            current_state=turn_state,
                            to_state="data_fetching",
                            reason="tool_call:run_sql",
                            transitions=state_transitions,
                            worldline_id=active_worldline_id,
                            on_delta=on_delta,
                        )
                    elif tool_name == "run_python":
                        turn_state = await self._transition_state_and_emit(
                            current_state=turn_state,
                            to_state="analyzing",
                            reason="tool_call:run_python",
                            transitions=state_transitions,
                            worldline_id=active_worldline_id,
                            on_delta=on_delta,
                        )

                    payload_error = self._validate_tool_payload(
                        tool_name=tool_name,
                        arguments=tool_call.arguments or {},
                    )
                    if payload_error is not None:
                        if on_delta is not None and delta_type is not None:
                            await on_delta(
                                active_worldline_id,
                                {
                                    "type": delta_type,
                                    "call_id": tool_call.id or None,
                                    "skipped": True,
                                    "reason": "invalid_tool_payload",
                                    "error": payload_error,
                                },
                            )

                        turn_state = await self._transition_state_and_emit(
                            current_state=turn_state,
                            to_state="error",
                            reason=f"invalid_tool_payload:{tool_name}",
                            transitions=state_transitions,
                            worldline_id=active_worldline_id,
                            on_delta=on_delta,
                        )
                        turn_state = await self._transition_state_and_emit(
                            current_state=turn_state,
                            to_state="planning",
                            reason="recover_after_invalid_tool_payload",
                            transitions=state_transitions,
                            worldline_id=active_worldline_id,
                            on_delta=on_delta,
                        )

                        invalid_tool_payload_failures[tool_name] = (
                            invalid_tool_payload_failures.get(tool_name, 0) + 1
                        )
                        max_retries = _MAX_INVALID_TOOL_PAYLOAD_RETRIES.get(
                            tool_name, 2
                        )

                        if invalid_tool_payload_failures[tool_name] <= max_retries:
                            messages.append(
                                ChatMessage(
                                    role="system",
                                    content=self._build_tool_payload_correction_message(
                                        tool_name=tool_name,
                                        payload_error=payload_error,
                                        data_intent_summary=data_intent_summary,
                                    ),
                                )
                            )
                            invalid_payload_retry_requested = True
                            break

                        if tool_name == "run_python":
                            final_text = (
                                "I stopped after repeated invalid Python tool payloads "
                                "(empty/invalid `code`). I can continue once the model emits "
                                "a valid run_python call."
                            )
                        else:
                            final_text = (
                                "I stopped after repeated invalid SQL tool payloads "
                                "(empty/invalid `sql`). I can continue once the model emits "
                                "a valid run_sql call."
                            )
                        repeated_call_detected = True
                        break

                    signature = self._tool_signature(
                        worldline_id=active_worldline_id,
                        tool_call=tool_call,
                    )
                    if signature in successful_tool_signatures:
                        if on_delta is not None and delta_type is not None:
                            await on_delta(
                                active_worldline_id,
                                {
                                    "type": delta_type,
                                    "call_id": tool_call.id or None,
                                    "skipped": True,
                                    "reason": "repeated_identical_tool_call",
                                },
                            )
                        final_text = (
                            "I stopped because the model repeated the same tool call "
                            "with identical arguments in this turn."
                        )
                        repeated_call_detected = True
                        break

                    if (
                        not user_requested_rerun
                        and signature in recent_successful_tool_signatures
                    ):
                        if on_delta is not None and delta_type is not None:
                            await on_delta(
                                active_worldline_id,
                                {
                                    "type": delta_type,
                                    "call_id": tool_call.id or None,
                                    "skipped": True,
                                    "reason": "recent_identical_successful_tool_call",
                                },
                            )
                        final_text = (
                            "I skipped this tool call because an identical successful "
                            "call already ran recently in this worldline. "
                            "If you want a fresh rerun, ask me to rerun or overwrite it."
                        )
                        repeated_call_detected = True
                        break

                    if tool_name == "run_python" and not user_requested_rerun:
                        code = (tool_call.arguments or {}).get("code")
                        if isinstance(code, str) and code.strip():
                            candidate_names = (
                                self._extract_artifact_names_from_python_code(code)
                            )
                            if candidate_names:
                                existing_names = sorted(
                                    {
                                        name
                                        for name in candidate_names
                                        if name in recent_artifact_names
                                        or name in turn_artifact_names
                                    }
                                )
                                if existing_names and len(existing_names) == len(
                                    candidate_names
                                ):
                                    if on_delta is not None and delta_type is not None:
                                        await on_delta(
                                            active_worldline_id,
                                            {
                                                "type": delta_type,
                                                "call_id": tool_call.id or None,
                                                "skipped": True,
                                                "reason": "duplicate_artifact_prevented",
                                                "artifact_names": existing_names,
                                            },
                                        )
                                    final_text = (
                                        "I skipped Python execution because it would recreate "
                                        "existing artifacts: "
                                        + ", ".join(existing_names)
                                        + ". Ask me to rerun or overwrite if you want new files."
                                    )
                                    repeated_call_detected = True
                                    break

                    # Note: tool call deltas were already streamed in _stream_llm_response.
                    # For the non-streaming path (on_delta is None), no deltas are emitted.

                    # Small delay between tool calls to avoid burning through API requests too fast
                    await asyncio.sleep(0.4)

                    tool_result, switched_worldline_id = await self._execute_tool_call(
                        worldline_id=active_worldline_id,
                        tool_call=tool_call,
                        carried_user_message=message,
                        allowed_external_aliases=allowed_external_aliases,
                        on_event=on_event,
                    )
                    if (
                        switched_worldline_id
                        and switched_worldline_id != active_worldline_id
                    ):
                        active_worldline_id = switched_worldline_id
                        starting_rowid_by_worldline.setdefault(active_worldline_id, 0)
                        history_events = await self._load_worldline_events(
                            active_worldline_id
                        )
                        messages = build_llm_messages_from_events(list(history_events))
                        artifact_inventory = self._artifact_inventory_from_events(
                            history_events
                        )
                        data_intent_summary = self._data_intent_from_events(
                            history_events
                        )
                        recent_successful_tool_signatures = (
                            self._recent_successful_tool_signatures(
                                worldline_id=active_worldline_id,
                                events=history_events,
                                limit=_RECENT_SIGNATURE_WINDOW,
                            )
                        )
                        recent_artifact_names = self._artifact_name_set(
                            artifact_inventory
                        )
                        turn_artifact_names.clear()
                        # Reset per-turn state for the new worldline context
                        sql_success_count = 0
                        python_success_count = 0
                        successful_tool_signatures.clear()
                        turn_state = await self._transition_state_and_emit(
                            current_state=turn_state,
                            to_state="planning",
                            reason="worldline_switched",
                            transitions=state_transitions,
                            worldline_id=active_worldline_id,
                            on_delta=on_delta,
                        )

                    serialized = json.dumps(
                        tool_result,
                        ensure_ascii=True,
                        default=str,
                    )
                    if len(serialized) > 12_000:
                        serialized = serialized[:12_000] + "...(truncated)"
                    messages.append(
                        ChatMessage(
                            role="tool",
                            content=serialized,
                            tool_call_id=tool_call.id or None,
                        )
                    )

                    if (
                        tool_name == "run_python"
                        and self._is_empty_python_payload_error(tool_result)
                    ):
                        invalid_tool_payload_failures["run_python"] += 1
                        if (
                            invalid_tool_payload_failures["run_python"]
                            <= _MAX_INVALID_TOOL_PAYLOAD_RETRIES["run_python"]
                        ):
                            messages.append(
                                ChatMessage(
                                    role="system",
                                    content=self._build_tool_payload_correction_message(
                                        tool_name="run_python",
                                        payload_error=(
                                            "run_python requires a non-empty `code` "
                                            "string containing executable Python"
                                        ),
                                        data_intent_summary=data_intent_summary,
                                    ),
                                )
                            )
                            invalid_payload_retry_requested = True
                            break
                        else:
                            final_text = (
                                "I stopped after repeated invalid Python tool payloads "
                                "(empty/invalid `code`). I'll continue once a valid Python "
                                "tool call is provided."
                            )
                            repeated_call_detected = True
                            break

                    if not tool_result.get("error"):
                        successful_tool_signatures.add(signature)
                        recent_successful_tool_signatures.add(signature)
                        successful_tool_results[signature] = tool_result
                        if tool_name == "run_sql":
                            sql_success_count += 1
                            turn_state = await self._transition_state_and_emit(
                                current_state=turn_state,
                                to_state="analyzing",
                                reason="tool_result:run_sql_success",
                                transitions=state_transitions,
                                worldline_id=active_worldline_id,
                                on_delta=on_delta,
                            )
                            data_intent_summary = self._build_data_intent_summary(
                                sql=(tool_call.arguments or {}).get("sql"),
                                sql_result=tool_result,
                            )
                        if tool_name == "run_python":
                            python_success_count += 1
                            turn_state = await self._transition_state_and_emit(
                                current_state=turn_state,
                                to_state="analyzing",
                                reason="tool_result:run_python_success",
                                transitions=state_transitions,
                                worldline_id=active_worldline_id,
                                on_delta=on_delta,
                            )
                            new_inventory_entries = (
                                self._artifact_inventory_from_tool_result(
                                    tool_result,
                                    source_call_id=tool_call.id,
                                    producer="run_python",
                                )
                            )
                            if new_inventory_entries:
                                artifact_inventory = self._merge_artifact_inventory(
                                    artifact_inventory,
                                    new_inventory_entries,
                                )
                                for entry in new_inventory_entries:
                                    name = str(entry.get("name") or "").strip().lower()
                                    if not name:
                                        continue
                                    recent_artifact_names.add(name)
                                    turn_artifact_names.add(name)
                    else:
                        turn_state = await self._transition_state_and_emit(
                            current_state=turn_state,
                            to_state="error",
                            reason=f"tool_result:{tool_name}_error",
                            transitions=state_transitions,
                            worldline_id=active_worldline_id,
                            on_delta=on_delta,
                        )
                        turn_state = await self._transition_state_and_emit(
                            current_state=turn_state,
                            to_state="planning",
                            reason="recover_after_tool_error",
                            transitions=state_transitions,
                            worldline_id=active_worldline_id,
                            on_delta=on_delta,
                        )
                if repeated_call_detected:
                    if final_text:
                        turn_state = await self._transition_state_and_emit(
                            current_state=turn_state,
                            to_state="presenting",
                            reason="guard_stop",
                            transitions=state_transitions,
                            worldline_id=active_worldline_id,
                            on_delta=on_delta,
                        )
                    break
                if invalid_payload_retry_requested:
                    continue
                continue

            if response.text:
                missing_required_tools = self._missing_required_terminal_tools(
                    required_tools=required_terminal_tools,
                    sql_success_count=sql_success_count,
                    python_success_count=python_success_count,
                )
                if missing_required_tools:
                    required_tool_enforcement_failures += 1
                    turn_state = await self._transition_state_and_emit(
                        current_state=turn_state,
                        to_state="error",
                        reason=(
                            "required_tool_missing:"
                            + ",".join(sorted(missing_required_tools))
                        ),
                        transitions=state_transitions,
                        worldline_id=active_worldline_id,
                        on_delta=on_delta,
                    )

                    if (
                        required_tool_enforcement_failures
                        <= _MAX_REQUIRED_TOOL_ENFORCEMENT_RETRIES
                    ):
                        messages.append(
                            ChatMessage(
                                role="system",
                                content=self._build_required_tool_enforcement_message(
                                    missing_required_tools=missing_required_tools,
                                    data_intent_summary=data_intent_summary,
                                ),
                            )
                        )
                        turn_state = await self._transition_state_and_emit(
                            current_state=turn_state,
                            to_state="planning",
                            reason="retry_after_missing_required_tool",
                            transitions=state_transitions,
                            worldline_id=active_worldline_id,
                            on_delta=on_delta,
                        )
                        continue

                    final_text = (
                        "I stopped because the model kept replying without required tool "
                        "execution (" + ", ".join(sorted(missing_required_tools)) + ")."
                    )
                    turn_state = await self._transition_state_and_emit(
                        current_state=turn_state,
                        to_state="presenting",
                        reason="required_tool_missing_stop",
                        transitions=state_transitions,
                        worldline_id=active_worldline_id,
                        on_delta=on_delta,
                    )
                    break

                turn_state = await self._transition_state_and_emit(
                    current_state=turn_state,
                    to_state="presenting",
                    reason="assistant_text_ready",
                    transitions=state_transitions,
                    worldline_id=active_worldline_id,
                    on_delta=on_delta,
                )
                final_text = response.text
                break

            # The LLM returned no text and no tool calls.  This typically
            # happens when the conversation history confuses the model.
            # Retry once by continuing the loop (which makes a fresh LLM
            # call); give up on the second empty response.
            empty_response_retries += 1
            if empty_response_retries <= 1:
                logger.warning(
                    "Empty LLM response (no text, no tool_calls). "
                    "messages=%d, last_role=%s. Retrying (attempt %d).",
                    len(messages),
                    messages[-1].role if messages else "N/A",
                    empty_response_retries,
                )
                continue

            logger.warning(
                "Empty LLM response persisted after retry. "
                "messages=%d, last_role=%s. Giving up.",
                len(messages),
                messages[-1].role if messages else "N/A",
            )
            final_text = (
                "I wasn't able to generate a response for that request. "
                "Could you try rephrasing your question?"
            )
            turn_state = await self._transition_state_and_emit(
                current_state=turn_state,
                to_state="presenting",
                reason="empty_llm_response_fallback",
                transitions=state_transitions,
                worldline_id=active_worldline_id,
                on_delta=on_delta,
            )
            break

        if final_text is None:
            final_text = (
                "I reached the tool-loop limit before producing a final answer."
            )
            turn_state = await self._transition_state_and_emit(
                current_state=turn_state,
                to_state="presenting",
                reason="max_iterations_reached",
                transitions=state_transitions,
                worldline_id=active_worldline_id,
                on_delta=on_delta,
            )

        if requested_output_type == "report":
            report_generated = await self._ensure_report_pdf_artifact(
                worldline_id=active_worldline_id,
                starting_rowid=starting_rowid_by_worldline[active_worldline_id],
                on_event=on_event,
            )
            if report_generated:
                final_text = f"{final_text}\n\nGenerated downloadable report artifact: report.pdf."

        turn_state = await self._transition_state_and_emit(
            current_state=turn_state,
            to_state="completed",
            reason="assistant_message_persisted",
            transitions=state_transitions,
            worldline_id=active_worldline_id,
            on_delta=on_delta,
        )
        _debug_log(
            run_id="initial",
            hypothesis_id="STATE_MACHINE_PHASE2",
            location="backend/chat/engine.py:run_turn:state_transitions",
            message="Completed turn with explicit state transitions",
            data={
                "worldline_id": active_worldline_id,
                "transitions": state_transitions,
                "final_state": turn_state,
                "sql_success_count": sql_success_count,
                "python_success_count": python_success_count,
                "required_tools": sorted(required_terminal_tools),
                "required_tool_enforcement_failures": required_tool_enforcement_failures,
                "invalid_tool_payload_failures": invalid_tool_payload_failures,
            },
        )

        assistant_payload = {
            "text": final_text,
            "state_trace": state_transitions,
            "state_final": turn_state,
            "turn_stats": {
                "required_tools": sorted(required_terminal_tools),
                "sql_success_count": sql_success_count,
                "python_success_count": python_success_count,
                "invalid_tool_payload_failures": invalid_tool_payload_failures,
                "required_tool_enforcement_failures": required_tool_enforcement_failures,
            },
        }
        assistant_event = self._append_worldline_event(
            worldline_id=active_worldline_id,
            event_type="assistant_message",
            payload=assistant_payload,
        )
        if on_event is not None:
            await on_event(active_worldline_id, assistant_event)

        events = self._events_since_rowid(
            worldline_id=active_worldline_id,
            rowid=starting_rowid_by_worldline[active_worldline_id],
        )

        return (
            active_worldline_id,
            events,
        )

    # ---- real-time streaming bridge -----------------------------------------

    async def _stream_llm_response(
        self,
        *,
        worldline_id: str,
        messages: list[ChatMessage],
        tools: list[ToolDefinition],
        on_delta: Callable[[str, dict[str, Any]], Awaitable[None]],
    ) -> LlmResponse:
        return await stream_llm_response(
            llm_client=self.llm_client,
            worldline_id=worldline_id,
            messages=messages,
            tools=tools,
            max_output_tokens=self.max_output_tokens,
            on_delta=on_delta,
        )

    def _tool_name_to_delta_type(self, tool_name: str) -> str | None:
        return tool_name_to_delta_type(tool_name)

    def _tool_definitions(self, *, include_python: bool = True) -> list[ToolDefinition]:
        return tool_definitions(include_python=include_python)

    # ---- tool execution -----------------------------------------------------

    async def _execute_tool_call(
        self,
        worldline_id: str,
        tool_call: ToolCall,
        carried_user_message: str,
        allowed_external_aliases: list[str] | None,
        on_event: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    ) -> tuple[dict[str, Any], str | None]:
        name = (tool_call.name or "").strip()
        args = tool_call.arguments or {}

        if name == "run_sql":
            sql = args.get("sql")
            if not isinstance(sql, str) or not sql.strip():
                return {"error": "run_sql requires a non-empty 'sql' string"}, None

            raw_limit = args.get("limit", 100)
            try:
                limit = int(raw_limit)
            except (TypeError, ValueError):
                limit = 100
            limit = max(1, min(limit, 10_000))

            try:
                result = await execute_sql_tool(
                    SqlToolRequest(
                        worldline_id=worldline_id,
                        sql=sql,
                        limit=limit,
                        allowed_external_aliases=allowed_external_aliases,
                        call_id=tool_call.id or None,
                    ),
                    on_event=(
                        None
                        if on_event is None
                        else lambda event: on_event(worldline_id, event)
                    ),
                )
                return result, None
            except HTTPException as exc:
                return {"error": str(exc.detail), "status_code": exc.status_code}, None
            except Exception as exc:  # pragma: no cover
                return {"error": str(exc)}, None

        if name == "run_python":
            code = args.get("code")
            if not isinstance(code, str) or not code.strip():
                # #region agent log
                _debug_log(
                    run_id="initial",
                    hypothesis_id="H8",
                    location="backend/chat/engine.py:_execute_tool_call:run_python_invalid_args",
                    message="run_python call missing/invalid code argument",
                    data={
                        "worldline_id": worldline_id,
                        "call_id": tool_call.id,
                        "args_keys": sorted(list(args.keys())),
                        "args_preview": json.dumps(
                            args, ensure_ascii=True, default=str
                        )[:220],
                    },
                )
                # #endregion
                return {"error": "run_python requires a non-empty 'code' string"}, None

            raw_timeout = args.get("timeout", 30)
            try:
                timeout = int(raw_timeout)
            except (TypeError, ValueError):
                timeout = 30
            timeout = max(1, min(timeout, 120))

            try:
                result = await execute_python_tool(
                    PythonToolRequest(
                        worldline_id=worldline_id,
                        code=code,
                        timeout=timeout,
                        call_id=tool_call.id or None,
                    ),
                    on_event=(
                        None
                        if on_event is None
                        else lambda event: on_event(worldline_id, event)
                    ),
                )
                return result, None
            except HTTPException as exc:
                return {"error": str(exc.detail), "status_code": exc.status_code}, None
            except Exception as exc:  # pragma: no cover
                return {"error": str(exc)}, None

        if name == "time_travel":
            from_event_id = args.get("from_event_id")
            if not isinstance(from_event_id, str) or not from_event_id.strip():
                return {"error": "time_travel requires 'from_event_id'"}, None

            name_arg = args.get("name")
            branch_name = name_arg if isinstance(name_arg, str) and name_arg else None

            try:
                branch_result = self.worldline_service.branch_from_event(
                    BranchOptions(
                        source_worldline_id=worldline_id,
                        from_event_id=from_event_id,
                        name=branch_name,
                        append_events=True,
                        carried_user_message=carried_user_message,
                    )
                )
                if on_event is not None:
                    for event_id in branch_result.created_event_ids:
                        event = self._load_event_by_id(event_id)
                        await on_event(branch_result.new_worldline_id, event)
                return branch_result.to_tool_result(), branch_result.new_worldline_id
            except HTTPException as exc:
                return {"error": str(exc.detail), "status_code": exc.status_code}, None
            except Exception as exc:  # pragma: no cover
                return {"error": str(exc)}, None

        return {"error": f"unknown tool '{name}'"}, None

    async def _ensure_report_pdf_artifact(
        self,
        *,
        worldline_id: str,
        starting_rowid: int,
        on_event: Callable[[str, dict[str, Any]], Awaitable[None]] | None,
    ) -> bool:
        turn_events = self._events_since_rowid(
            worldline_id=worldline_id, rowid=starting_rowid
        )
        if _events_contain_pdf_artifact(turn_events):
            return False

        try:
            result = await execute_python_tool(
                PythonToolRequest(
                    worldline_id=worldline_id,
                    code=_AUTO_REPORT_CODE,
                    timeout=90,
                    call_id="auto_report_pdf",
                ),
                on_event=(
                    None
                    if on_event is None
                    else lambda event: on_event(worldline_id, event)
                ),
            )
        except HTTPException as exc:
            logger.warning(
                "Auto report PDF generation failed with HTTPException: %s", exc.detail
            )
            return False
        except Exception as exc:  # pragma: no cover
            logger.warning("Auto report PDF generation failed: %s", exc)
            return False

        artifacts = result.get("artifacts") if isinstance(result, dict) else None
        if not isinstance(artifacts, list):
            return False
        return any(_artifact_is_pdf(artifact) for artifact in artifacts)

    async def _persist_failed_tool_call(
        self,
        worldline_id: str,
        call_type: str,
        result_type: str,
        call_payload: dict[str, Any],
        result_payload: dict[str, Any],
        on_event: Callable[[str, dict[str, Any]], Awaitable[None]],
    ) -> None:
        """Persist a tool call and its error result so the user sees the attempt."""
        call_event = self._append_worldline_event(
            worldline_id=worldline_id,
            event_type=call_type,
            payload=call_payload,
        )
        if on_event is not None:
            await on_event(worldline_id, call_event)

        result_event = self._append_worldline_event(
            worldline_id=worldline_id,
            event_type=result_type,
            payload=result_payload,
        )
        if on_event is not None:
            await on_event(worldline_id, result_event)

    # ---- message building ---------------------------------------------------

    async def _load_worldline_events(self, worldline_id: str) -> list[dict[str, Any]]:
        timeline = await get_worldline_events(
            worldline_id=worldline_id,
            limit=250,
            cursor=None,
        )
        events = timeline.get("events", [])
        return list(events)

    async def _build_llm_messages(self, worldline_id: str) -> list[ChatMessage]:
        events = await self._load_worldline_events(worldline_id)
        return build_llm_messages_from_events(events)

    def _artifact_inventory_from_events(
        self, events: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
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

            created_at = str(event.get("created_at") or "")
            source_event_id = str(event.get("id") or "")

            for artifact in artifacts:
                if not isinstance(artifact, dict):
                    continue
                name = str(artifact.get("name") or "").strip()
                if not name:
                    continue

                key = name.lower()
                entry = {
                    "artifact_id": str(artifact.get("artifact_id") or ""),
                    "name": name,
                    "type": str(artifact.get("type") or "file"),
                    "created_at": created_at,
                    "source_call_id": source_call_id,
                    "source_event_id": source_event_id,
                    "producer": "run_python",
                }
                if key in deduped_by_name:
                    del deduped_by_name[key]
                deduped_by_name[key] = entry

        inventory = list(deduped_by_name.values())
        if len(inventory) > _ARTIFACT_INVENTORY_MAX_ITEMS:
            inventory = inventory[-_ARTIFACT_INVENTORY_MAX_ITEMS:]
        return inventory

    def _artifact_inventory_from_tool_result(
        self,
        tool_result: dict[str, Any],
        *,
        source_call_id: str | None,
        producer: str,
    ) -> list[dict[str, Any]]:
        artifacts = tool_result.get("artifacts")
        if not isinstance(artifacts, list):
            return []

        inventory: list[dict[str, Any]] = []
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            name = str(artifact.get("name") or "").strip()
            if not name:
                continue
            inventory.append(
                {
                    "artifact_id": str(artifact.get("artifact_id") or ""),
                    "name": name,
                    "type": str(artifact.get("type") or "file"),
                    "created_at": "",
                    "source_call_id": source_call_id,
                    "source_event_id": "",
                    "producer": producer,
                }
            )
        return inventory

    def _merge_artifact_inventory(
        self,
        existing: list[dict[str, Any]],
        incoming: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        deduped_by_name: dict[str, dict[str, Any]] = {}

        for entry in [*existing, *incoming]:
            name = str(entry.get("name") or "").strip()
            if not name:
                continue
            key = name.lower()
            normalized_entry = dict(entry)
            normalized_entry["name"] = name
            if key in deduped_by_name:
                del deduped_by_name[key]
            deduped_by_name[key] = normalized_entry

        merged = list(deduped_by_name.values())
        if len(merged) > _ARTIFACT_INVENTORY_MAX_ITEMS:
            merged = merged[-_ARTIFACT_INVENTORY_MAX_ITEMS:]
        return merged

    def _artifact_name_set(self, inventory: list[dict[str, Any]]) -> set[str]:
        return {
            str(entry.get("name") or "").strip().lower()
            for entry in inventory
            if str(entry.get("name") or "").strip()
        }

    def _render_artifact_inventory_message(
        self, artifact_inventory: list[dict[str, Any]]
    ) -> str:
        payload = {
            "artifact_count": len(artifact_inventory),
            "artifacts": artifact_inventory,
            "instructions": (
                "Check this inventory before creating files. Reuse existing artifacts "
                "instead of regenerating identical outputs."
            ),
        }
        return f"{_ARTIFACT_INVENTORY_HEADER} (always-on memory):\n" + json.dumps(
            payload, ensure_ascii=True, default=str
        )

    def _upsert_artifact_inventory_message(
        self,
        messages: list[ChatMessage],
        artifact_inventory: list[dict[str, Any]],
    ) -> None:
        content = self._render_artifact_inventory_message(artifact_inventory)
        memory_message = ChatMessage(role="system", content=content)
        for index, message in enumerate(messages):
            if message.role == "system" and message.content.startswith(
                _ARTIFACT_INVENTORY_HEADER
            ):
                messages[index] = memory_message
                return

        insert_index = 1 if messages else 0
        messages.insert(insert_index, memory_message)

    def _data_intent_from_events(
        self,
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

            return self._build_data_intent_summary(sql=sql, sql_result=payload)

        return None

    def _build_data_intent_summary(
        self,
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
            if self._is_numeric_sql_type(col_type):
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
            if re.search(
                r"(date|time|month|year|day|week|quarter)", name, re.IGNORECASE
            )
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

    def _is_numeric_sql_type(self, type_name: str) -> bool:
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

    def _render_data_intent_message(
        self,
        data_intent_summary: dict[str, Any],
    ) -> str:
        payload = {
            "data_intent": data_intent_summary,
            "instructions": (
                "Use this checkpoint when planning follow-up SQL/Python steps. "
                "If Python is needed, reference LATEST_SQL_RESULT/LATEST_SQL_DF "
                "instead of refetching identical data."
            ),
        }
        return f"{_DATA_INTENT_HEADER} (always-on memory):\n" + json.dumps(
            payload,
            ensure_ascii=True,
            default=str,
        )

    def _upsert_data_intent_message(
        self,
        messages: list[ChatMessage],
        data_intent_summary: dict[str, Any] | None,
    ) -> None:
        existing_index = None
        for index, message in enumerate(messages):
            if message.role == "system" and message.content.startswith(
                _DATA_INTENT_HEADER
            ):
                existing_index = index
                break

        if data_intent_summary is None:
            if existing_index is not None:
                del messages[existing_index]
            return

        memory_message = ChatMessage(
            role="system",
            content=self._render_data_intent_message(data_intent_summary),
        )
        if existing_index is not None:
            messages[existing_index] = memory_message
            return

        insert_index = 2 if len(messages) >= 2 else len(messages)
        messages.insert(insert_index, memory_message)

    def _python_code_has_executable_content(self, code: str) -> bool:
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

    def _validate_tool_payload(
        self,
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
            if not self._python_code_has_executable_content(code):
                return (
                    "run_python `code` must include executable Python and cannot be "
                    "comments/whitespace only"
                )
            return None

        return None

    def _build_tool_payload_correction_message(
        self,
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
            checkpoint = (
                json.dumps(data_intent_summary, ensure_ascii=True, default=str)
                if data_intent_summary
                else "none"
            )
            if len(checkpoint) > 900:
                checkpoint = checkpoint[:900] + "...(truncated)"
            return (
                "Correction: your last run_python payload was invalid ("
                + payload_error
                + "). Emit a fresh run_python tool call now with a non-empty executable `code` "
                "string and optional integer `timeout`. "
                "Do not emit empty args, nested JSON-in-code, or comments-only code. "
                "Example args: " + example_args + ". SQL checkpoint: " + checkpoint
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

    async def _emit_state_transition_delta(
        self,
        *,
        worldline_id: str,
        transition: dict[str, Any],
        on_delta: Callable[[str, dict[str, Any]], Awaitable[None]] | None,
    ) -> None:
        if on_delta is None:
            return

        await on_delta(
            worldline_id,
            {
                "type": "state_transition",
                "from_state": transition.get("from"),
                "to_state": transition.get("to"),
                "reason": transition.get("reason"),
            },
        )

    async def _transition_state_and_emit(
        self,
        *,
        current_state: str,
        to_state: str,
        reason: str,
        transitions: list[dict[str, Any]],
        worldline_id: str,
        on_delta: Callable[[str, dict[str, Any]], Awaitable[None]] | None,
    ) -> str:
        before_len = len(transitions)
        next_state = self._transition_state(
            current_state=current_state,
            to_state=to_state,
            reason=reason,
            transitions=transitions,
            worldline_id=worldline_id,
        )

        if len(transitions) > before_len:
            await self._emit_state_transition_delta(
                worldline_id=worldline_id,
                transition=transitions[-1],
                on_delta=on_delta,
            )

        return next_state

    def _recent_successful_tool_signatures(
        self,
        *,
        worldline_id: str,
        events: list[dict[str, Any]],
        limit: int,
    ) -> set[str]:
        if limit <= 0:
            return set()

        by_id = {event.get("id"): event for event in events}
        signatures: set[str] = set()

        for event in reversed(events):
            event_type = event.get("type")
            if event_type not in {"tool_result_sql", "tool_result_python"}:
                continue

            payload = event.get("payload")
            if not isinstance(payload, dict) or payload.get("error"):
                continue

            parent = by_id.get(event.get("parent_event_id"))
            if not isinstance(parent, dict):
                continue

            parent_type = parent.get("type")
            if parent_type == "tool_call_sql":
                tool_name = "run_sql"
            elif parent_type == "tool_call_python":
                tool_name = "run_python"
            else:
                continue

            parent_payload = parent.get("payload")
            if not isinstance(parent_payload, dict):
                continue

            args = dict(parent_payload)
            args.pop("call_id", None)
            normalized_args = normalize_tool_arguments(tool_name, args)
            signature = self._tool_signature(
                worldline_id=worldline_id,
                tool_call=ToolCall(
                    id=str(parent.get("id") or ""),
                    name=tool_name,
                    arguments=normalized_args,
                ),
            )
            signatures.add(signature)
            if len(signatures) >= limit:
                break

        return signatures

    def _extract_artifact_names_from_python_code(self, code: str) -> set[str]:
        if not isinstance(code, str) or not code.strip():
            return set()

        names: set[str] = set()
        for match in _ARTIFACT_FILENAME_PATTERN.finditer(code):
            candidate = match.group(1).strip()
            if not candidate:
                continue
            if len(candidate) > 180:
                continue
            names.add(candidate.lower())
        return names

    def _user_requested_rerun(self, message: str) -> bool:
        if not isinstance(message, str):
            return False
        return bool(_RERUN_HINT_PATTERN.search(message))

    def _required_terminal_tools(
        self,
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

    def _missing_required_terminal_tools(
        self,
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

    def _build_required_tool_enforcement_message(
        self,
        *,
        missing_required_tools: set[str],
        data_intent_summary: dict[str, Any] | None,
    ) -> str:
        missing_sorted = sorted(missing_required_tools)
        if missing_sorted == ["run_python"]:
            checkpoint = (
                json.dumps(data_intent_summary, ensure_ascii=True, default=str)
                if data_intent_summary
                else "none"
            )
            if len(checkpoint) > 900:
                checkpoint = checkpoint[:900] + "...(truncated)"
            return (
                "Your previous reply skipped required Python execution. "
                "Emit a run_python tool call now with non-empty executable `code` (not a plan). "
                "Use LATEST_SQL_RESULT/LATEST_SQL_DF and the SQL checkpoint when relevant. "
                "SQL checkpoint: " + checkpoint
            )

        return (
            "Your previous reply skipped required tool execution ("
            + ", ".join(missing_sorted)
            + "). Emit the required tool call(s) now before finalizing."
        )

    def _is_empty_python_payload_error(self, tool_result: dict[str, Any]) -> bool:
        error = tool_result.get("error") if isinstance(tool_result, dict) else None
        if not isinstance(error, str):
            return False
        lowered = error.lower()
        return "non-empty 'code'" in lowered or "empty `code`" in lowered

    def _transition_state(
        self,
        *,
        current_state: str,
        to_state: str,
        reason: str,
        transitions: list[dict[str, Any]],
        worldline_id: str,
    ) -> str:
        if current_state == to_state:
            return current_state

        allowed = _TURN_STATE_TRANSITIONS.get(current_state, set())
        if to_state not in allowed:
            logger.warning(
                "Invalid state transition: %s -> %s (%s)",
                current_state,
                to_state,
                reason,
            )
            transitions.append(
                {
                    "from": current_state,
                    "to": "error",
                    "reason": f"invalid_transition:{current_state}->{to_state}:{reason}",
                }
            )
            _debug_log(
                run_id="initial",
                hypothesis_id="STATE_MACHINE_PHASE2",
                location="backend/chat/engine.py:_transition_state:invalid",
                message="Invalid state transition attempted",
                data={
                    "worldline_id": worldline_id,
                    "from": current_state,
                    "to": to_state,
                    "reason": reason,
                },
            )
            return "error"

        transitions.append({"from": current_state, "to": to_state, "reason": reason})
        _debug_log(
            run_id="initial",
            hypothesis_id="STATE_MACHINE_PHASE2",
            location="backend/chat/engine.py:_transition_state",
            message="State transition",
            data={
                "worldline_id": worldline_id,
                "from": current_state,
                "to": to_state,
                "reason": reason,
            },
        )
        return to_state

    # ---- helpers ------------------------------------------------------------

    def _tool_signature(self, *, worldline_id: str, tool_call: ToolCall) -> str:
        return tool_signature(
            worldline_id=worldline_id,
            tool_call=tool_call,
        )

    def _append_worldline_event(
        self,
        *,
        worldline_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return append_worldline_event(
            worldline_id=worldline_id,
            event_type=event_type,
            payload=payload,
        )

    def _load_event_by_id(self, event_id: str) -> dict[str, Any]:
        return load_event_by_id(event_id)

    def _max_worldline_rowid(self, worldline_id: str) -> int:
        return max_worldline_rowid(worldline_id)

    def _events_since_rowid(
        self, *, worldline_id: str, rowid: int
    ) -> list[dict[str, Any]]:
        return events_since_rowid(worldline_id=worldline_id, rowid=rowid)
