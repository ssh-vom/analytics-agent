from __future__ import annotations

import logging
from typing import Any

AUTO_REPORT_CODE = r"""
from datetime import datetime

import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

try:
    import pandas as pd
except Exception:
    pd = None


def _load_frame():
    if pd is None:
        return None

    frame = None
    if "LATEST_SQL_DF" in globals() and LATEST_SQL_DF is not None:
        try:
            frame = LATEST_SQL_DF.copy()
        except Exception:
            frame = LATEST_SQL_DF

    if frame is None and "LATEST_SQL_RESULT" in globals():
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

    if frame is None:
        frame = pd.DataFrame()
    return frame


def _safe_numeric(series):
    if pd is None:
        return None
    return pd.to_numeric(series, errors="coerce")


frame = _load_frame()
rows = int(len(frame.index)) if frame is not None and hasattr(frame, "index") else 0
cols = int(len(frame.columns)) if frame is not None and hasattr(frame, "columns") else 0
generated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

with PdfPages("report.pdf") as pdf:
    # Cover page
    fig = plt.figure(figsize=(11, 8.5))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.axis("off")
    ax.text(0.06, 0.88, "TextQL Report", fontsize=28, fontweight="bold")
    ax.text(0.06, 0.82, f"Generated {generated_at}", fontsize=11)
    ax.text(0.06, 0.68, f"Rows: {rows:,}", fontsize=15)
    ax.text(0.06, 0.62, f"Columns: {cols:,}", fontsize=15)
    ax.text(
        0.06,
        0.50,
        "This report is generated from the latest SQL result in this worldline.",
        fontsize=11,
    )
    pdf.savefig(fig)
    plt.close(fig)

    if frame is not None and hasattr(frame, "empty") and not frame.empty:
        # Preview table page
        fig = plt.figure(figsize=(11, 8.5))
        ax = fig.add_axes([0.04, 0.08, 0.92, 0.82])
        ax.axis("off")
        fig.suptitle("Data Preview", fontsize=18, fontweight="bold")

        preview = frame.iloc[:14, :7].copy()
        rows_data = [[str(value)[:30] for value in row] for row in preview.values.tolist()]
        if rows_data:
            table = ax.table(
                cellText=rows_data,
                colLabels=[str(col)[:24] for col in preview.columns],
                cellLoc="left",
                colLoc="left",
                bbox=[0, 0, 1, 0.92],
            )
            table.auto_set_font_size(False)
            table.set_fontsize(8.5)
        else:
            ax.text(0.0, 0.5, "No preview rows available.", fontsize=11)
        pdf.savefig(fig)
        plt.close(fig)

        # Histogram page for first numeric column
        if pd is not None:
            numeric_col = None
            for column_name in frame.columns:
                series = _safe_numeric(frame[column_name])
                if series is None:
                    continue
                clean = series.dropna()
                if clean.shape[0] >= 2:
                    numeric_col = column_name
                    break

            if numeric_col is not None:
                series = _safe_numeric(frame[numeric_col]).dropna()
                fig, ax = plt.subplots(figsize=(11, 8.5))
                bins = min(24, max(6, int(series.shape[0] ** 0.5)))
                ax.hist(series, bins=bins, color="#2563EB", alpha=0.9, edgecolor="white")
                ax.set_title(f"Distribution: {numeric_col}", fontsize=16)
                ax.set_xlabel(str(numeric_col))
                ax.set_ylabel("Count")
                ax.grid(axis="y", alpha=0.25)
                pdf.savefig(fig)
                plt.close(fig)
    else:
        fig = plt.figure(figsize=(11, 8.5))
        ax = fig.add_axes([0, 0, 1, 1])
        ax.axis("off")
        ax.text(0.06, 0.72, "No tabular SQL result was available for chart generation.", fontsize=14)
        ax.text(0.06, 0.66, "Run a SQL query first, then request a report.", fontsize=11)
        pdf.savefig(fig)
        plt.close(fig)

print("Generated report.pdf")
"""


def artifact_is_pdf(artifact: Any) -> bool:
    if not isinstance(artifact, dict):
        return False

    artifact_type = str(artifact.get("type", "")).lower()
    artifact_name = str(artifact.get("name", "")).lower()
    return artifact_type == "pdf" or artifact_name.endswith(".pdf")


def events_contain_pdf_artifact(events: list[dict[str, Any]]) -> bool:
    for event in events:
        if event.get("type") != "tool_result_python":
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        artifacts = payload.get("artifacts")
        if not isinstance(artifacts, list):
            continue
        if any(artifact_is_pdf(artifact) for artifact in artifacts):
            return True
    return False


def assert_auto_report_code_compiles(logger: logging.Logger) -> bool:
    try:
        compile(AUTO_REPORT_CODE, "<auto_report_fallback>", "exec")
    except SyntaxError as exc:
        logger.warning(
            "Auto report code failed syntax preflight at line %s: %s",
            exc.lineno,
            exc.msg,
        )
        return False
    return True
