"""Semantic query executor - integrates semantic layer with chat engine."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from semantic.catalog import build_catalog_for_worldline
from semantic.resolver import resolve_query
from semantic.compiler import compile_query_spec, get_compilation_summary
from semantic.types import ResolutionResult

if TYPE_CHECKING:
    from duckdb_manager import DuckDBManager

logger = logging.getLogger(__name__)

# Confidence threshold for using deterministic lane
DETERMINISTIC_CONFIDENCE_THRESHOLD = 0.6


class SemanticExecutor:
    """Executes queries through the semantic layer when confidence is high."""

    def __init__(self, duckdb_manager: DuckDBManager):
        self.duckdb_manager = duckdb_manager
        self._catalog_cache: dict[str, Any] = {}  # worldline_id -> catalog

    def get_or_build_catalog(self, worldline_id: str) -> SemanticCatalog | None:
        """Get cached catalog or build new one."""
        if worldline_id in self._catalog_cache:
            return self._catalog_cache[worldline_id]

        catalog = build_catalog_for_worldline(worldline_id, self.duckdb_manager)
        if catalog:
            self._catalog_cache[worldline_id] = catalog
        return catalog

    def can_handle_query(
        self,
        user_message: str,
        worldline_id: str,
    ) -> tuple[bool, ResolutionResult | None]:
        """Check if this query can be handled deterministically.

        Returns:
            Tuple of (can_handle, resolution_result)
        """
        catalog = self.get_or_build_catalog(worldline_id)
        if not catalog:
            return False, None

        if not catalog.datasets:
            return False, None

        resolution = resolve_query(catalog, user_message)

        # Need minimum confidence and at least one metric
        if resolution.confidence < DETERMINISTIC_CONFIDENCE_THRESHOLD:
            return False, resolution

        if not resolution.query_spec.metrics:
            return False, resolution

        return True, resolution

    def execute_query(
        self,
        resolution: ResolutionResult,
        worldline_id: str,
    ) -> dict:
        """Execute a resolved query.

        Returns:
            Dict with sql, summary, and results preview
        """
        catalog = self.get_or_build_catalog(worldline_id)
        if not catalog:
            raise ValueError("No catalog available for worldline")

        # Compile to SQL
        sql = compile_query_spec(resolution.query_spec, catalog)
        if not sql:
            raise ValueError("Failed to compile query")

        # Get human-readable summary
        summary = get_compilation_summary(resolution.query_spec)

        return {
            "sql": sql,
            "summary": summary,
            "confidence": resolution.confidence,
            "unmatched_terms": resolution.unmatched_terms,
            "suggestions": resolution.suggestions,
        }

    def clear_cache(self, worldline_id: str | None = None):
        """Clear catalog cache."""
        if worldline_id:
            self._catalog_cache.pop(worldline_id, None)
        else:
            self._catalog_cache.clear()


def should_use_semantic_lane(
    user_message: str,
    executor: SemanticExecutor,
    worldline_id: str,
) -> tuple[bool, ResolutionResult | None]:
    """Determine if query should use semantic (deterministic) lane.

    This is the entry point for dual-lane execution decision.
    """
    # Simple heuristics first
    analysis_patterns = [
        "sum",
        "total",
        "average",
        "count",
        "by",
        "group",
        "show me",
        "what is",
        "how much",
        "calculate",
    ]

    msg_lower = user_message.lower()
    has_analysis_intent = any(p in msg_lower for p in analysis_patterns)

    if not has_analysis_intent:
        # Likely a conversation or command, use agentic
        return False, None

    # Try semantic resolution
    return executor.can_handle_query(user_message, worldline_id)
