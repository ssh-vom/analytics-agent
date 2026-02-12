"""LLM-based semantic resolver."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from semantic.types import ResolutionResult, SemanticCatalog
from semantic.prompts import build_resolution_prompt
from semantic.validator import (
    validate_llm_response,
    build_resolution_result,
    ResolutionError,
)

if TYPE_CHECKING:
    from chat.llm_client import LlmClient

logger = logging.getLogger(__name__)


async def resolve_query_with_llm(
    user_query: str,
    catalog: SemanticCatalog,
    llm_client: LlmClient,
    conversation_context: str | None = None,
) -> ResolutionResult:
    """Convenience function to resolve query with LLM.

    Args:
        user_query: Natural language query
        catalog: Schema catalog
        llm_client: LLM client instance
        conversation_context: Optional context

    Returns:
        ResolutionResult
    """
    # Build prompts
    system_prompt, user_prompt = build_resolution_prompt(
        user_query=user_query,
        catalog=catalog,
        conversation_context=conversation_context,
    )

    # Call LLM
    from chat.llm_client import ChatMessage

    messages = [
        ChatMessage(role="system", content=system_prompt),
        ChatMessage(role="user", content=user_prompt),
    ]

    try:
        response = await llm_client.generate(messages=messages)
    except Exception as e:
        logger.error(f"LLM resolution failed: {e}")
        # Return low-confidence result with error
        return ResolutionResult(
            query_spec=None,  # type: ignore
            confidence=0.0,
            unmatched_terms=[str(e)],
            suggestions=["Please try rephrasing your query"],
        )

    if not response.text:
        return ResolutionResult(
            query_spec=None,  # type: ignore
            confidence=0.0,
            unmatched_terms=["empty_response"],
            suggestions=["No response from language model"],
        )

    # Parse JSON
    try:
        data = validate_llm_response(response.text)
    except ResolutionError as e:
        logger.error(f"Failed to validate LLM response: {e}")
        return ResolutionResult(
            query_spec=None,  # type: ignore
            confidence=0.0,
            unmatched_terms=["parse_error"],
            suggestions=["Could not understand the query format"],
        )

    # Build result
    try:
        result = build_resolution_result(data, catalog)
        return result
    except Exception as e:
        logger.error(f"Failed to build resolution result: {e}")
        return ResolutionResult(
            query_spec=None,  # type: ignore
            confidence=0.0,
            unmatched_terms=["validation_error"],
            suggestions=["Query structure could not be validated"],
        )
