"""Semantic layer for TextQL - auto-inferred ontology and deterministic SQL generation."""

from semantic.types import (
    SemanticCatalog,
    Dataset,
    Column,
    QuerySpec,
    Metric,
    Filter,
    TimeRange,
    OrderBy,
    ResolutionResult,
)
from semantic.catalog import build_catalog_for_worldline
from semantic.resolver import resolve_query
from semantic.compiler import compile_query_spec
from semantic.executor import SemanticExecutor, should_use_semantic_lane

__all__ = [
    # Types
    "SemanticCatalog",
    "Dataset",
    "Column",
    "QuerySpec",
    "Metric",
    "Filter",
    "TimeRange",
    "OrderBy",
    "ResolutionResult",
    # Functions
    "build_catalog_for_worldline",
    "resolve_query",
    "compile_query_spec",
    "SemanticExecutor",
    "should_use_semantic_lane",
]
