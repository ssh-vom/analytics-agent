"""Semantic resolver - entry point for query resolution.

This module provides the main resolve_query function that uses LLM-based
resolution with fallback to legacy regex-based resolution.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from semantic.types import ResolutionResult, SemanticCatalog
from semantic.resolver_llm import resolve_query_with_llm
from .resolver_legacy import resolve_query as resolve_query_legacy

if TYPE_CHECKING:
    from chat.llm_client import LlmClient

logger = logging.getLogger(__name__)


async def resolve_query(
    user_query: str,
    catalog: SemanticCatalog,
    llm_client: LlmClient | None = None,
    use_llm: bool = True,
    conversation_context: str | None = None,
) -> ResolutionResult:
    """Resolve user query to structured QuerySpec.

    Uses LLM-based resolution when available and enabled, falling back
    to legacy regex-based resolution on failure.

    Args:
        user_query: Natural language query from user
        catalog: Semantic catalog with schema information
        llm_client: LLM client for intelligent resolution (optional)
        use_llm: Whether to attempt LLM-based resolution
        conversation_context: Optional recent conversation context

    Returns:
        ResolutionResult with QuerySpec and confidence score
    """
    if use_llm and llm_client:
        try:
            logger.debug(f"Attempting LLM resolution for: {user_query[:50]}...")
            result = await resolve_query_with_llm(
                user_query=user_query,
                catalog=catalog,
                llm_client=llm_client,
                conversation_context=conversation_context,
            )

            # If LLM resolution has decent confidence, use it
            if result.confidence >= 0.3:
                logger.debug(
                    f"LLM resolution successful with confidence: {result.confidence}"
                )
                return result
            else:
                logger.debug(
                    f"LLM resolution low confidence ({result.confidence}), trying legacy"
                )

        except Exception as e:
            logger.warning(f"LLM resolution failed: {e}")

    # Fallback to legacy regex-based resolver
    logger.debug("Using legacy regex-based resolver")
    return resolve_query_legacy(catalog, user_query)
