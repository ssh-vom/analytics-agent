"""Semantic query executor - integrates semantic layer with chat engine."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from semantic.catalog import build_catalog_for_worldline
from semantic.resolver import resolve_query
from semantic.compiler import compile_query_spec, get_compilation_summary
from semantic.types import ResolutionResult, SemanticCatalog

if TYPE_CHECKING:
    from chat.llm_client import LlmClient

logger = logging.getLogger(__name__)

# Confidence threshold for using deterministic lane
DETERMINISTIC_CONFIDENCE_THRESHOLD = 0.8


class SemanticExecutor:
    """Executes queries through the semantic layer when confidence is high."""

    def __init__(self, llm_client: LlmClient | None = None):
        self.llm_client = llm_client
        self._catalog_cache: dict[str, SemanticCatalog] = {}

    def get_or_build_catalog(self, worldline_id: str) -> SemanticCatalog | None:
        """Get cached catalog or build new one."""
        if worldline_id in self._catalog_cache:
            return self._catalog_cache[worldline_id]

        catalog = build_catalog_for_worldline(worldline_id, duckdb_manager=None)
        if catalog:
            self._catalog_cache[worldline_id] = catalog
        return catalog

    async def can_handle_query(
        self,
        user_message: str,
        worldline_id: str,
    ) -> tuple[bool, ResolutionResult | None]:
        """Check if this query can be handled deterministically.

        Returns:
            Tuple of (can_handle, resolution_result)
        """
        catalog = self.get_or_build_catalog(worldline_id)
        if not catalog or not catalog.datasets:
            return False, None

        resolution = await resolve_query(
            user_query=user_message,
            catalog=catalog,
            llm_client=self.llm_client,
            use_llm=self.llm_client is not None,
        )

        # Need minimum confidence and at least one metric
        if resolution.confidence < DETERMINISTIC_CONFIDENCE_THRESHOLD:
            return False, resolution

        if not resolution.query_spec.metrics:
            return False, resolution

        return True, resolution

    def compile_resolution(
        self,
        resolution: ResolutionResult,
        worldline_id: str,
    ) -> dict[str, Any] | None:
        """Compile a resolution to SQL.

        Returns:
            Dict with sql and summary, or None if compilation fails
        """
        catalog = self.get_or_build_catalog(worldline_id)
        if not catalog:
            return None

        sql = compile_query_spec(resolution.query_spec, catalog)
        if not sql:
            return None

        return {
            "sql": sql,
            "summary": get_compilation_summary(resolution.query_spec),
            "confidence": resolution.confidence,
        }

    def clear_cache(self, worldline_id: str | None = None):
        """Clear catalog cache."""
        if worldline_id:
            self._catalog_cache.pop(worldline_id, None)
        else:
            self._catalog_cache.clear()


_VISUALIZATION_HINTS = (
    "plot",
    "chart",
    "graph",
    "visuali",
    "visualize",
    "matplotlib",
    "histogram",
    "scatter",
    "heatmap",
    "draw",
    "figure",
)


async def should_use_semantic_lane(
    user_message: str,
    executor: SemanticExecutor,
    worldline_id: str,
) -> tuple[bool, ResolutionResult | None]:
    """Determine if query should use semantic (deterministic) lane."""
    msg_lower = user_message.lower()
    # Prefer agentic fallback when user asks for visualization - semantic lane is SQL-only
    if any(hint in msg_lower for hint in _VISUALIZATION_HINTS):
        return False, None

    # Quick heuristic check - skip semantic for non-analytical queries
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
    if not any(p in msg_lower for p in analysis_patterns):
        return False, None

    return await executor.can_handle_query(user_message, worldline_id)
