"""Validation and error handling for semantic resolution."""

from __future__ import annotations

import json
import logging
from typing import Any

from semantic.types import (
    QuerySpec,
    Metric,
    Filter,
    TimeRange,
    OrderBy,
    SemanticCatalog,
    ResolutionResult,
)

logger = logging.getLogger(__name__)


class ResolutionError(Exception):
    """Error during semantic resolution."""

    def __init__(self, message: str, retryable: bool = False):
        self.message = message
        self.retryable = retryable
        super().__init__(message)


def validate_llm_response(response_text: str) -> dict:
    """Validate and parse LLM JSON response.

    Args:
        response_text: Raw text from LLM

    Returns:
        Parsed JSON dict

    Raises:
        ResolutionError: If parsing fails
    """
    if not response_text or not response_text.strip():
        raise ResolutionError("Empty response from LLM", retryable=True)

    # Try to extract JSON from markdown code blocks
    text = response_text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse LLM response as JSON: {e}")
        raise ResolutionError(f"Invalid JSON response: {str(e)[:100]}", retryable=True)

    if not isinstance(parsed, dict):
        raise ResolutionError("Response is not a JSON object", retryable=True)

    return parsed


def validate_query_spec(data: dict, catalog: SemanticCatalog) -> QuerySpec:
    """Validate and convert LLM output to QuerySpec.

    Args:
        data: Parsed JSON from LLM
        catalog: Schema catalog for validation

    Returns:
        Validated QuerySpec

    Raises:
        ResolutionError: If validation fails
    """
    query_spec = QuerySpec()

    # Validate intent
    valid_intents = {
        "aggregation",
        "comparison",
        "trend",
        "drill_down",
        "distribution",
        "top_n",
        "unknown",
    }
    intent = data.get("intent", "unknown")
    if intent not in valid_intents:
        logger.warning(f"Unknown intent: {intent}, defaulting to 'unknown'")
        intent = "unknown"

    # Validate and extract metrics
    metrics_data = data.get("metrics", [])
    if not isinstance(metrics_data, list):
        metrics_data = []

    for metric_item in metrics_data:
        if not isinstance(metric_item, dict):
            continue

        column = metric_item.get("column")
        if not column:
            continue

        # Validate column exists in catalog
        found_column = None
        for dataset in catalog.datasets:
            for col in dataset.columns:
                if col.name.lower() == column.lower():
                    found_column = col
                    query_spec.dataset = dataset.name
                    break
            if found_column:
                break

        if not found_column:
            logger.warning(f"Metric column not found in schema: {column}")
            continue

        # Validate aggregation
        valid_aggs = {"sum", "avg", "count", "count_distinct", "min", "max"}
        agg = metric_item.get("aggregation", "sum")
        if agg not in valid_aggs:
            agg = "sum"

        query_spec.metrics.append(
            Metric(
                column=found_column.name,
                aggregation=agg,
                alias=metric_item.get("alias"),
            )
        )

    # Validate dimensions
    dimensions_data = data.get("dimensions", [])
    if not isinstance(dimensions_data, list):
        dimensions_data = []

    for dim in dimensions_data:
        if not isinstance(dim, str):
            continue

        # Validate dimension exists
        found = False
        for dataset in catalog.datasets:
            for col in dataset.columns:
                if col.name.lower() == dim.lower():
                    query_spec.dimensions.append(col.name)
                    found = True
                    if not query_spec.dataset:
                        query_spec.dataset = dataset.name
                    break
            if found:
                break

        if not found:
            logger.warning(f"Dimension column not found: {dim}")

    # Validate filters
    filters_data = data.get("filters", [])
    if not isinstance(filters_data, list):
        filters_data = []

    for filter_item in filters_data:
        if not isinstance(filter_item, dict):
            continue

        column = filter_item.get("column")
        if not column:
            continue

        # Validate filter column exists
        found = False
        for dataset in catalog.datasets:
            for col in dataset.columns:
                if col.name.lower() == column.lower():
                    found = True
                    break
            if found:
                break

        if not found:
            logger.warning(f"Filter column not found: {column}")
            continue

        valid_ops = {
            "eq",
            "ne",
            "gt",
            "gte",
            "lt",
            "lte",
            "like",
            "is_null",
            "is_not_null",
        }
        op = filter_item.get("operator", "eq")
        if op not in valid_ops:
            op = "eq"

        query_spec.filters.append(
            Filter(column=column, operator=op, value=filter_item.get("value"))
        )

    # Validate time range
    time_data = data.get("time_range")
    if isinstance(time_data, dict):
        time_col = time_data.get("column")

        # Validate time column exists and is a time type
        if time_col:
            for dataset in catalog.datasets:
                for col in dataset.columns:
                    if col.name.lower() == time_col.lower() and col.role == "time":
                        query_spec.time_range = TimeRange(
                            column=col.name,
                            start=time_data.get("start"),
                            end=time_data.get("end"),
                        )
                        if not query_spec.dataset:
                            query_spec.dataset = dataset.name
                        break
                if query_spec.time_range:
                    break

    # Validate order_by
    order_data = data.get("order_by", [])
    if isinstance(order_data, list):
        for order_item in order_data:
            if isinstance(order_item, dict):
                query_spec.order_by.append(
                    OrderBy(
                        column=order_item.get("column", ""),
                        direction=order_item.get("direction", "desc"),
                    )
                )

    # Validate limit
    limit = data.get("limit", 1000)
    if isinstance(limit, int) and 1 <= limit <= 10000:
        query_spec.limit = limit
    else:
        query_spec.limit = 1000

    return query_spec


def calculate_confidence(
    data: dict, query_spec: QuerySpec, catalog: SemanticCatalog
) -> float:
    """Calculate confidence score based on resolution quality.

    Args:
        data: Raw LLM response
        query_spec: Parsed query spec
        catalog: Schema catalog

    Returns:
        Confidence score 0.0-1.0
    """
    # Start with LLM's confidence
    llm_confidence = data.get("confidence", 0.5)
    if not isinstance(llm_confidence, (int, float)):
        llm_confidence = 0.5
    llm_confidence = max(0.0, min(1.0, llm_confidence))

    # Validation factors
    validation_score = 0.0

    # Has metrics?
    if query_spec.metrics:
        validation_score += 0.3

    # Metrics are valid columns?
    valid_metrics = 0
    for metric in query_spec.metrics:
        for dataset in catalog.datasets:
            for col in dataset.columns:
                if col.name == metric.column:
                    valid_metrics += 1
                    break
    if query_spec.metrics and valid_metrics == len(query_spec.metrics):
        validation_score += 0.2

    # Has dimensions or dataset selected?
    if query_spec.dimensions or query_spec.dataset:
        validation_score += 0.2

    # Has explicit intent (not unknown)?
    if data.get("intent") != "unknown":
        validation_score += 0.2

    # No ambiguous terms?
    ambiguous = data.get("ambiguous_terms", [])
    if not ambiguous:
        validation_score += 0.1

    # Weighted combination
    final_confidence = (llm_confidence * 0.6) + (validation_score * 0.4)

    return round(final_confidence, 2)


def extract_ambiguous_terms(data: dict) -> list[str]:
    """Extract ambiguous terms from LLM response."""
    terms = data.get("ambiguous_terms", [])
    if not isinstance(terms, list):
        return []
    return [str(t) for t in terms if isinstance(t, (str, int, float))]


def extract_suggestions(data: dict) -> list[str]:
    """Extract suggestions from LLM response."""
    suggestions = data.get("suggestions", [])
    if not isinstance(suggestions, list):
        # Try to get from interpretation
        interp = data.get("suggested_interpretation", "")
        if interp:
            return [interp]
        return []
    return [str(s) for s in suggestions if s]


def build_resolution_result(data: dict, catalog: SemanticCatalog) -> ResolutionResult:
    """Build a complete ResolutionResult from LLM response.

    Args:
        data: Parsed JSON from LLM
        catalog: Schema catalog

    Returns:
        ResolutionResult with validated QuerySpec
    """
    # Build and validate query spec
    query_spec = validate_query_spec(data, catalog)

    # Calculate final confidence
    confidence = calculate_confidence(data, query_spec, catalog)

    # Extract ambiguous terms
    ambiguous = extract_ambiguous_terms(data)

    # Extract suggestions
    suggestions = extract_suggestions(data)

    return ResolutionResult(
        query_spec=query_spec,
        confidence=confidence,
        unmatched_terms=ambiguous,
        suggestions=suggestions,
    )
