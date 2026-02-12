"""Term resolver - maps user language to semantic entities."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from semantic.types import (
    QuerySpec,
    Metric,
    Filter,
    TimeRange,
    OrderBy,
    ResolutionResult,
    SemanticCatalog,
    Dataset,
    Column,
    Aggregation,
)

if TYPE_CHECKING:
    pass


def normalize_term(term: str) -> str:
    """Normalize a user term for matching."""
    # Remove common filler words
    fillers = {"the", "a", "an", "of", "by", "for", "in", "on", "at"}
    words = term.lower().split()
    words = [w for w in words if w not in fillers]
    return " ".join(words)


def score_column_match(column: Column, user_term: str) -> float:
    """Score how well a column matches a user term."""
    user_normalized = normalize_term(user_term)
    user_words = set(user_normalized.split())

    col_normalized = normalize_term(column.name)
    col_words = set(col_normalized.split())

    # Exact match
    if col_normalized == user_normalized:
        return 1.0

    # Contains match
    if user_normalized in col_normalized or col_normalized in user_normalized:
        return 0.8

    # Word overlap
    overlap = user_words & col_words
    if overlap:
        return len(overlap) / max(len(user_words), len(col_words)) * 0.6

    return 0.0


def resolve_column_reference(
    catalog: SemanticCatalog,
    user_term: str,
    dataset_hint: str | None = None,
    role_hint: str | None = None,
) -> tuple[Column, str] | None:
    """Resolve a user term to a specific column in a dataset.

    Returns:
        Tuple of (column, dataset_name) or None if no match
    """
    best_match: tuple[Column, str, float] | None = None

    for dataset in catalog.datasets:
        # Skip if dataset hint doesn't match
        if dataset_hint and dataset.name != dataset_hint:
            if not any(
                dataset_hint.lower() in d.lower()
                for d in [dataset.name, dataset.schema]
            ):
                continue

        for column in dataset.columns:
            # Skip if role hint doesn't match
            if role_hint and column.role != role_hint:
                continue

            score = score_column_match(column, user_term)

            # Boost score for measures when looking for metrics
            if role_hint == "measure" and column.role == "measure":
                score *= 1.2

            if score > 0.5:  # Threshold
                if best_match is None or score > best_match[2]:
                    best_match = (column, dataset.name, score)

    if best_match:
        return (best_match[0], best_match[1])
    return None


def parse_aggregation(user_text: str) -> Aggregation | None:
    """Parse aggregation intent from user text."""
    aggregations = {
        "sum": ["sum", "total", "add up", "aggregate"],
        "avg": ["average", "mean", "typical"],
        "count": ["count", "number of", "how many"],
        "count_distinct": ["unique count", "distinct count", "how many different"],
        "min": ["minimum", "lowest", "smallest", "least"],
        "max": ["maximum", "highest", "largest", "most"],
    }

    text_lower = user_text.lower()
    for agg, keywords in aggregations.items():
        for keyword in keywords:
            if keyword in text_lower:
                return agg
    return None


def parse_time_range(user_text: str) -> tuple[str | None, str | None]:
    """Parse time range from user text. Returns (start, end)."""
    # Relative patterns
    relative_patterns = {
        r"last\s+(\d+)\s+days?": lambda m: (f"-{m.group(1)} days", None),
        r"last\s+(\d+)\s+weeks?": lambda m: (f"-{int(m.group(1)) * 7} days", None),
        r"last\s+(\d+)\s+months?": lambda m: (f"-{m.group(1)} months", None),
        r"this\s+week": lambda m: ("-7 days", None),
        r"this\s+month": lambda m: ("-1 month", None),
        r"this\s+year": lambda m: ("-1 year", None),
        r"yesterday": lambda m: ("-1 day", "-1 day"),
        r"today": lambda m: ("0 days", "0 days"),
    }

    for pattern, handler in relative_patterns.items():
        match = re.search(pattern, user_text, re.IGNORECASE)
        if match:
            return handler(match)

    return None, None


def parse_filter(user_text: str) -> Filter | None:
    """Parse a simple filter from user text."""
    # Pattern: "where X is Y" or "X equals Y"
    filter_patterns = [
        (r"(\w+)\s+(?:is|=|equals?)\s+(.+)", "eq"),
        (r"(\w+)\s+greater\s+than\s+(\d+)", "gt"),
        (r"(\w+)\s+less\s+than\s+(\d+)", "lt"),
        (r"(\w+)\s+not\s+(?:is|=)\s+(.+)", "ne"),
    ]

    for pattern, op in filter_patterns:
        match = re.search(pattern, user_text, re.IGNORECASE)
        if match:
            return Filter(
                column=match.group(1).strip(), operator=op, value=match.group(2).strip()
            )

    return None


def resolve_query(
    catalog: SemanticCatalog,
    user_message: str,
    dataset_hint: str | None = None,
) -> ResolutionResult:
    """Resolve a user message to a QuerySpec.

    Uses heuristics to extract:
    - Metrics (what to calculate)
    - Dimensions (what to group by)
    - Filters (what to include/exclude)
    - Time ranges (when)

    Returns ResolutionResult with confidence score.
    """
    query_spec = QuerySpec()
    unmatched_terms = []

    # Extract potential metrics
    metric_patterns = [
        r"(?:show|get|calculate|what is|how much)\s+(.+?)(?:\s+by\s+|\s+for\s+|\?|$)",
        r"sum of\s+(.+?)(?:\s+by\s+|\?|$)",
        r"total\s+(.+?)(?:\s+by\s+|\?|$)",
        r"average\s+(.+?)(?:\s+by\s+|\?|$)",
    ]

    metrics_found = []
    for pattern in metric_patterns:
        match = re.search(pattern, user_message, re.IGNORECASE)
        if match:
            metric_text = match.group(1).strip()
            agg = parse_aggregation(user_message) or "sum"

            # Try to resolve the metric to a column
            col_ref = resolve_column_reference(
                catalog, metric_text, dataset_hint, "measure"
            )
            if col_ref:
                column, dataset = col_ref
                metrics_found.append(
                    Metric(
                        column=column.name,
                        aggregation=agg,
                        alias=f"{agg}_{column.name}",
                    )
                )
                if not query_spec.dataset:
                    query_spec.dataset = dataset

    query_spec.metrics = metrics_found

    # Extract potential dimensions (group by)
    dimension_pattern = r"by\s+(.+?)(?:\s+for\s+|\s+where\s+|\?|$)"
    dim_match = re.search(dimension_pattern, user_message, re.IGNORECASE)
    if dim_match:
        dim_text = dim_match.group(1).strip()
        # Could be comma-separated
        dims = [d.strip() for d in dim_text.split(",")]

        for dim in dims:
            col_ref = resolve_column_reference(catalog, dim, dataset_hint, "dimension")
            if col_ref:
                column, dataset = col_ref
                query_spec.dimensions.append(column.name)
                if not query_spec.dataset:
                    query_spec.dataset = dataset

    # Extract time range
    time_start, time_end = parse_time_range(user_message)
    if time_start or time_end:
        # Find a time column
        for dataset in catalog.datasets:
            if dataset_hint and dataset.name != dataset_hint:
                continue
            for col in dataset.columns:
                if col.role == "time":
                    query_spec.time_range = TimeRange(
                        column=col.name, start=time_start, end=time_end
                    )
                    if not query_spec.dataset:
                        query_spec.dataset = dataset.name
                    break

    # Extract filters
    simple_filter = parse_filter(user_message)
    if simple_filter:
        # Try to resolve column
        col_ref = resolve_column_reference(catalog, simple_filter.column, dataset_hint)
        if col_ref:
            simple_filter.column = col_ref[0].name
            query_spec.filters.append(simple_filter)

    # Calculate confidence
    confidence = 0.0

    if query_spec.metrics:
        confidence += 0.4
    if query_spec.dimensions:
        confidence += 0.3
    if query_spec.time_range:
        confidence += 0.2
    if query_spec.filters:
        confidence += 0.1

    # Boost if we have a dataset
    if query_spec.dataset:
        confidence = min(1.0, confidence * 1.2)

    # Generate suggestions if confidence is low
    suggestions = []
    if confidence < 0.5:
        suggestions = [
            "Try: 'Show total sales by region'",
            "Try: 'Average price by category last month'",
            "Try: 'Count of orders by date'",
        ]

    return ResolutionResult(
        query_spec=query_spec,
        confidence=confidence,
        unmatched_terms=unmatched_terms,
        suggestions=suggestions,
    )
