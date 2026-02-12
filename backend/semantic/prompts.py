"""LLM prompts for semantic query resolution."""

from __future__ import annotations

from semantic.types import SemanticCatalog, Dataset, Column


SYSTEM_PROMPT = """You are a semantic query parser for a data analysis system. Your job is to extract structured information from user queries about data analysis.

You have access to the following tables and columns in the database. Each column is classified as:
- MEASURE: Numeric columns that can be aggregated (summed, averaged, etc.)
- DIMENSION: Categorical columns used for grouping
- TIME: Date/timestamp columns for time-based analysis

{schema_context}

Given a user query, extract:
1. What metrics they want to calculate (which measure columns, what aggregation)
2. What dimensions they want to group by
3. Any filters or time ranges
4. The overall intent of the query

Respond with a JSON object following this exact structure:
{{
  "intent": "aggregation" | "comparison" | "trend" | "drill_down" | "distribution" | "top_n" | "unknown",
  "metrics": [
    {{
      "column": "exact_column_name_from_schema",
      "aggregation": "sum" | "avg" | "count" | "count_distinct" | "min" | "max",
      "alias": "optional_alias"
    }}
  ],
  "dimensions": ["column_name_1", "column_name_2"],
  "filters": [
    {{
      "column": "column_name",
      "operator": "eq" | "ne" | "gt" | "gte" | "lt" | "lte" | "like" | "is_null" | "is_not_null",
      "value": "filter_value_or_null"
    }}
  ],
  "time_range": {{
    "column": "time_column_name",
    "start": "relative_time_like_-30d_or_iso_date",
    "end": "relative_time_like_now_or_iso_date"
  }},
  "confidence": 0.0_to_1.0,
  "ambiguous_terms": ["list_of_unclear_terms_from_query"],
  "suggested_interpretation": "human_readable_explanation_of_what_you_understood"
}}

Rules:
1. Only use column names that exist in the schema above
2. Aggregation defaults to "sum" for measures unless specified otherwise
3. Confidence should be high (>0.8) when the query is clear and matches schema
4. Confidence should be low (<0.5) when terms don't match or query is too vague
5. For time ranges, use relative expressions like "-7d", "-1m", "-1y", "now"
6. If the query is ambiguous, list the ambiguous terms and provide your best interpretation"""


EXAMPLE_FEW_SHOT = """
Examples:

Query: "show total sales by region last month"
{{
  "intent": "aggregation",
  "metrics": [{{"column": "sales", "aggregation": "sum"}}],
  "dimensions": ["region"],
  "time_range": {{"column": "date", "start": "-1m", "end": "now"}},
  "confidence": 0.95,
  "ambiguous_terms": [],
  "suggested_interpretation": "Sum of sales grouped by region for the last month"
}}

Query: "average price by product category"
{{
  "intent": "aggregation",
  "metrics": [{{"column": "price", "aggregation": "avg"}}],
  "dimensions": ["product_category"],
  "confidence": 0.92,
  "ambiguous_terms": [],
  "suggested_interpretation": "Average price grouped by product category"
}}

Query: "how are we doing?"
{{
  "intent": "unknown",
  "metrics": [],
  "dimensions": [],
  "confidence": 0.2,
  "ambiguous_terms": ["doing"],
  "suggested_interpretation": "Query is too vague. Could mean sales performance, customer count, or other metrics."
}}
"""


def format_schema_context(catalog: SemanticCatalog, max_columns: int = 20) -> str:
    """Format catalog schema for LLM prompt.

    Args:
        catalog: Semantic catalog with datasets
        max_columns: Maximum columns to include (to save tokens)

    Returns:
        Formatted schema string for prompt
    """
    lines = []

    for dataset in catalog.datasets:
        lines.append(f"\nTable: {dataset.name}")
        if dataset.row_count:
            lines.append(f"  ({dataset.row_count:,} rows)")

        # Sort columns: measures first, then dimensions, then time, then others
        sorted_cols = sorted(
            dataset.columns,
            key=lambda c: (
                {"measure": 0, "dimension": 1, "time": 2, "unknown": 3}.get(c.role, 4),
                c.name,
            ),
        )

        # Limit columns to save tokens
        display_cols = sorted_cols[:max_columns]
        if len(sorted_cols) > max_columns:
            lines.append(f"  ... and {len(sorted_cols) - max_columns} more columns")

        for col in display_cols:
            role_display = col.role.upper() if col.role != "unknown" else "?"
            type_display = col.data_type.lower()

            # Add semantic type hint if available
            semantic_hint = f" ({col.semantic_type})" if col.semantic_type else ""

            lines.append(
                f"  - {col.name}: {type_display} [{role_display}]{semantic_hint}"
            )

    return "\n".join(lines)


def build_resolution_prompt(
    user_query: str,
    catalog: SemanticCatalog,
    conversation_context: str | None = None,
) -> tuple[str, str]:
    """Build system and user prompts for LLM resolution.

    Args:
        user_query: The user's natural language query
        catalog: Semantic catalog for schema context
        conversation_context: Optional recent conversation history

    Returns:
        Tuple of (system_prompt, user_prompt)
    """
    schema_context = format_schema_context(catalog)

    # Build system prompt with schema
    system = SYSTEM_PROMPT.format(schema_context=schema_context)
    system += EXAMPLE_FEW_SHOT

    # Build user prompt
    user_parts = [f'Query: "{user_query}"']

    if conversation_context:
        user_parts.insert(0, f"Recent context: {conversation_context}")

    user = "\n".join(user_parts)

    return system, user


def build_clarification_prompt(
    original_query: str,
    ambiguous_terms: list[str],
    possible_interpretations: list[dict],
) -> str:
    """Build prompt for generating clarification questions.

    Args:
        original_query: The original user query
        ambiguous_terms: List of unclear terms
        possible_interpretations: Possible ways to interpret the query

    Returns:
        Prompt text for generating clarification
    """
    lines = [
        f'The user asked: "{original_query}"',
        "",
        f"Ambiguous terms: {', '.join(ambiguous_terms)}",
        "",
        "Possible interpretations:",
    ]

    for i, interp in enumerate(possible_interpretations, 1):
        lines.append(f"{i}. {interp.get('description', 'Unknown')}")
        if "sql_preview" in interp:
            lines.append(f"   SQL: {interp['sql_preview']}")

    lines.extend(
        [
            "",
            "Generate a helpful, conversational clarification question that:",
            "1. Acknowledges the ambiguity",
            "2. Presents the options clearly",
            "3. Asks the user to choose or clarify",
            "4. Keeps it brief and friendly",
        ]
    )

    return "\n".join(lines)
