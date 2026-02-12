"""LLM-based semantic resolver - replaces regex patterns with LLM understanding."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from semantic.types import ResolutionResult, SemanticCatalog
from semantic.prompts import build_resolution_prompt, build_clarification_prompt
from semantic.validator import (
    validate_llm_response,
    build_resolution_result,
    ResolutionError,
)

if TYPE_CHECKING:
    from chat.llm_client import LlmClient

logger = logging.getLogger(__name__)


class LLMSemanticResolver:
    """Uses LLM to parse natural language into semantic QuerySpec."""

    def __init__(self, llm_client: LlmClient):
        self.llm_client = llm_client
        self.max_retries = 2

    async def resolve(
        self,
        user_query: str,
        catalog: SemanticCatalog,
        conversation_context: str | None = None,
    ) -> ResolutionResult:
        """Resolve user query to structured QuerySpec using LLM.

        Args:
            user_query: Natural language query from user
            catalog: Semantic catalog with schema information
            conversation_context: Optional recent conversation for context

        Returns:
            ResolutionResult with QuerySpec and confidence

        Raises:
            ResolutionError: If resolution fails after retries
        """
        # Build prompts
        system_prompt, user_prompt = build_resolution_prompt(
            user_query=user_query,
            catalog=catalog,
            conversation_context=conversation_context,
        )

        # Try resolution with retries
        last_error = None
        for attempt in range(self.max_retries):
            try:
                result = await self._try_resolve(system_prompt, user_prompt)
                return result
            except ResolutionError as e:
                last_error = e
                if not e.retryable:
                    break
                logger.warning(f"Resolution attempt {attempt + 1} failed, retrying...")

        # All retries failed
        logger.error(
            f"Resolution failed after {self.max_retries} attempts: {last_error}"
        )
        raise last_error or ResolutionError("Failed to resolve query")

    async def _try_resolve(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> ResolutionResult:
        """Single attempt at resolution."""
        from chat.llm_client import ChatMessage

        # Call LLM
        messages = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=user_prompt),
        ]

        try:
            response = await self.llm_client.generate(messages=messages)
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            raise ResolutionError(f"LLM generation failed: {e}", retryable=True)

        if not response.text:
            raise ResolutionError("Empty response from LLM", retryable=True)

        # Parse and validate response
        try:
            data = validate_llm_response(response.text)
        except ResolutionError:
            raise

        # Build resolution result (catalog will be passed in the outer scope)
        # This is a bit awkward - we need catalog for validation
        # So we'll do the validation in resolve() method
        raise NotImplementedError("Use resolve() method instead")

    async def generate_clarification(
        self,
        original_query: str,
        ambiguous_terms: list[str],
        possible_interpretations: list[dict],
    ) -> str:
        """Generate a natural language clarification question.

        Args:
            original_query: The original user query
            ambiguous_terms: List of unclear terms
            possible_interpretations: Possible ways to interpret

        Returns:
            Clarification question text
        """
        from chat.llm_client import ChatMessage

        prompt = build_clarification_prompt(
            original_query=original_query,
            ambiguous_terms=ambiguous_terms,
            possible_interpretations=possible_interpretations,
        )

        messages = [
            ChatMessage(
                role="system",
                content="You are a helpful assistant that asks clear, friendly clarification questions.",
            ),
            ChatMessage(role="user", content=prompt),
        ]

        try:
            response = await self.llm_client.generate(messages=messages)
            if response.text:
                return response.text.strip()
        except Exception as e:
            logger.error(f"Failed to generate clarification: {e}")

        # Fallback to simple clarification
        return self._fallback_clarification(ambiguous_terms, possible_interpretations)

    def _fallback_clarification(
        self, ambiguous_terms: list[str], interpretations: list[dict]
    ) -> str:
        """Generate simple fallback clarification."""
        parts = ["I'm not sure I understand"]

        if ambiguous_terms:
            parts.append(f'what you mean by "{", ".join(ambiguous_terms)}"')

        if interpretations:
            parts.append(". Did you mean:")
            for i, interp in enumerate(interpretations[:3], 1):
                desc = interp.get("description", f"Option {i}")
                parts.append(f"\n{i}. {desc}")

        parts.append("\n\nCould you clarify?")

        return "".join(parts)


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
    resolver = LLMSemanticResolver(llm_client)

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


# Keep old regex-based resolver as fallback for now
from semantic.resolver_legacy import resolve_query as resolve_query_regex


async def resolve_query(
    user_query: str,
    catalog: SemanticCatalog,
    llm_client: LlmClient | None = None,
    use_llm: bool = True,
    conversation_context: str | None = None,
) -> ResolutionResult:
    """Resolve query using LLM or fallback to regex.

    Args:
        user_query: Natural language query
        catalog: Schema catalog
        llm_client: Optional LLM client (required if use_llm=True)
        use_llm: Whether to use LLM-based resolution
        conversation_context: Optional context

    Returns:
        ResolutionResult
    """
    if use_llm and llm_client:
        try:
            return await resolve_query_with_llm(
                user_query=user_query,
                catalog=catalog,
                llm_client=llm_client,
                conversation_context=conversation_context,
            )
        except Exception as e:
            logger.warning(f"LLM resolution failed, falling back to regex: {e}")

    # Fallback to legacy regex-based resolver
    return resolve_query_regex(catalog, user_query)
