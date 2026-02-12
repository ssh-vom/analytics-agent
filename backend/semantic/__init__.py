"""Semantic layer for TextQL - LLM-powered ontology and deterministic SQL generation."""

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
from semantic.resolver_llm import resolve_query_with_llm
from semantic.compiler import compile_query_spec
from semantic.executor import SemanticExecutor, should_use_semantic_lane
from semantic.prompts import build_resolution_prompt, build_clarification_prompt
from semantic.validator import (
    validate_llm_response,
    validate_query_spec,
    build_resolution_result,
    ResolutionError,
)

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
    # Main functions
    "build_catalog_for_worldline",
    "resolve_query",
    "resolve_query_with_llm",
    "compile_query_spec",
    "SemanticExecutor",
    "should_use_semantic_lane",
    # Prompts
    "build_resolution_prompt",
    "build_clarification_prompt",
    # Validation
    "validate_llm_response",
    "validate_query_spec",
    "build_resolution_result",
    "ResolutionError",
]
