"""Semantic layer types for QuerySpec and ontology entities."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class Dataset:
    """A dataset (table) in the semantic catalog."""

    name: str
    schema: str
    columns: list[Column]
    row_count: int | None = None
    source: Literal["native", "imported", "external"] = "native"


@dataclass
class Column:
    """A column in a dataset."""

    name: str
    data_type: str
    role: Literal["dimension", "measure", "time", "unknown"] = "unknown"
    semantic_type: str | None = None  # e.g., "currency", "percentage", "timestamp"


@dataclass
class SemanticCatalog:
    """Complete semantic catalog for a worldline."""

    datasets: list[Dataset]

    def get_dataset(self, name: str) -> Dataset | None:
        """Get dataset by name."""
        for dataset in self.datasets:
            if dataset.name.lower() == name.lower():
                return dataset
        return None

    def get_column(self, dataset_name: str, column_name: str) -> Column | None:
        """Get column from a dataset."""
        dataset = self.get_dataset(dataset_name)
        if not dataset:
            return None
        for col in dataset.columns:
            if col.name.lower() == column_name.lower():
                return col
        return None


@dataclass
class QuerySpec:
    """User query specification for semantic SQL generation."""

    # What to select
    metrics: list[Metric] = field(default_factory=list)
    dimensions: list[str] = field(default_factory=list)

    # Filtering
    filters: list[Filter] = field(default_factory=list)
    time_range: TimeRange | None = None

    # Aggregation
    grain: list[str] = field(default_factory=list)  # Group by these columns

    # Ordering and limits
    order_by: list[OrderBy] = field(default_factory=list)
    limit: int | None = 1000

    # Source
    dataset: str | None = None  # Primary dataset/table


@dataclass
class Metric:
    """A metric to calculate."""

    column: str
    aggregation: Literal["sum", "avg", "count", "count_distinct", "min", "max"] = "sum"
    alias: str | None = None


@dataclass
class Filter:
    """A filter condition."""

    column: str
    operator: Literal[
        "eq",
        "ne",
        "gt",
        "gte",
        "lt",
        "lte",
        "in",
        "not_in",
        "like",
        "is_null",
        "is_not_null",
    ]
    value: str | int | float | list | None = None


@dataclass
class TimeRange:
    """Time range filter."""

    column: str
    start: str | None = None  # ISO date string or relative like "-30 days"
    end: str | None = None


@dataclass
class OrderBy:
    """Ordering specification."""

    column: str
    direction: Literal["asc", "desc"] = "desc"


@dataclass
class ResolutionResult:
    """Result of resolving user terms to semantic entities."""

    query_spec: QuerySpec
    confidence: float  # 0.0 to 1.0
    unmatched_terms: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)


# Type aliases for common patterns
FilterOperator = Literal[
    "eq",
    "ne",
    "gt",
    "gte",
    "lt",
    "lte",
    "in",
    "not_in",
    "like",
    "is_null",
    "is_not_null",
]
ColumnRole = Literal["dimension", "measure", "time", "unknown"]
Aggregation = Literal["sum", "avg", "count", "count_distinct", "min", "max"]
