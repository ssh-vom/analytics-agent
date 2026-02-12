"""SQL compiler - converts QuerySpec to deterministic SQL."""

from __future__ import annotations

from semantic.types import (
    QuerySpec,
    Metric,
    Filter,
    TimeRange,
    OrderBy,
    SemanticCatalog,
    Dataset,
    Column,
)


def quote_identifier(identifier: str) -> str:
    """Quote SQL identifier."""
    return f'"{identifier.replace('"', '""')}"'


def compile_metric(metric: Metric, dataset: Dataset) -> str:
    """Compile a metric to SQL expression."""
    col_quoted = quote_identifier(metric.column)

    agg_funcs = {
        "sum": f"SUM({col_quoted})",
        "avg": f"AVG({col_quoted})",
        "count": f"COUNT({col_quoted})",
        "count_distinct": f"COUNT(DISTINCT {col_quoted})",
        "min": f"MIN({col_quoted})",
        "max": f"MAX({col_quoted})",
    }

    expr = agg_funcs.get(metric.aggregation, f"SUM({col_quoted})")

    if metric.alias:
        return f"{expr} AS {quote_identifier(metric.alias)}"
    return expr


def compile_filter(filter_: Filter) -> str | None:
    """Compile a filter to SQL WHERE clause."""
    col_quoted = quote_identifier(filter_.column)

    operators = {
        "eq": "=",
        "ne": "!=",
        "gt": ">",
        "gte": ">=",
        "lt": "<",
        "lte": "<=",
        "like": "LIKE",
        "is_null": "IS NULL",
        "is_not_null": "IS NOT NULL",
    }

    op_sql = operators.get(filter_.operator)
    if not op_sql:
        return None

    if filter_.operator in ("is_null", "is_not_null"):
        return f"{col_quoted} {op_sql}"

    if filter_.operator == "in" and isinstance(filter_.value, list):
        values = ", ".join(
            f"'{v}'" if isinstance(v, str) else str(v) for v in filter_.value
        )
        return f"{col_quoted} IN ({values})"

    if filter_.operator == "not_in" and isinstance(filter_.value, list):
        values = ", ".join(
            f"'{v}'" if isinstance(v, str) else str(v) for v in filter_.value
        )
        return f"{col_quoted} NOT IN ({values})"

    # String values need quotes
    if isinstance(filter_.value, str):
        escaped = filter_.value.replace("'", "''")
        return f"{col_quoted} {op_sql} '{escaped}'"

    return f"{col_quoted} {op_sql} {filter_.value}"


def compile_time_range(time_range: TimeRange) -> str | None:
    """Compile time range to SQL expression."""
    col_quoted = quote_identifier(time_range.column)
    conditions = []

    if time_range.start:
        # Handle relative dates
        if time_range.start.startswith("-"):
            conditions.append(
                f"{col_quoted} >= CURRENT_DATE + INTERVAL '{time_range.start}'"
            )
        else:
            conditions.append(f"{col_quoted} >= '{time_range.start}'")

    if time_range.end:
        if time_range.end.startswith("-"):
            conditions.append(
                f"{col_quoted} <= CURRENT_DATE + INTERVAL '{time_range.end}'"
            )
        else:
            conditions.append(f"{col_quoted} <= '{time_range.end}'")

    if not conditions:
        return None

    return " AND ".join(conditions)


def compile_query_spec(query_spec: QuerySpec, catalog: SemanticCatalog) -> str | None:
    """Compile a QuerySpec to SQL.

    Returns SQL string or None if compilation fails.
    """
    if not query_spec.dataset:
        return None

    dataset = catalog.get_dataset(query_spec.dataset)
    if not dataset:
        return None

    # Build SELECT clause
    select_parts = []

    # Add dimensions
    for dim in query_spec.dimensions:
        select_parts.append(quote_identifier(dim))

    # Add metrics
    for metric in query_spec.metrics:
        select_parts.append(compile_metric(metric, dataset))

    if not select_parts:
        return None

    select_clause = "SELECT " + ", ".join(select_parts)

    # Build FROM clause
    # Handle schema.table format
    if "." in query_spec.dataset:
        from_clause = f"FROM {query_spec.dataset}"
    else:
        from_clause = f"FROM {quote_identifier(query_spec.dataset)}"

    # Build WHERE clause
    where_conditions = []

    for filter_ in query_spec.filters:
        compiled = compile_filter(filter_)
        if compiled:
            where_conditions.append(compiled)

    if query_spec.time_range:
        time_condition = compile_time_range(query_spec.time_range)
        if time_condition:
            where_conditions.append(time_condition)

    where_clause = ""
    if where_conditions:
        where_clause = "WHERE " + " AND ".join(f"({c})" for c in where_conditions)

    # Build GROUP BY clause
    group_clause = ""
    if query_spec.dimensions:
        group_cols = ", ".join(quote_identifier(d) for d in query_spec.dimensions)
        group_clause = f"GROUP BY {group_cols}"

    # Build ORDER BY clause
    order_clause = ""
    if query_spec.order_by:
        order_parts = []
        for ob in query_spec.order_by:
            order_parts.append(f"{quote_identifier(ob.column)} {ob.direction.upper()}")
        order_clause = "ORDER BY " + ", ".join(order_parts)
    elif query_spec.metrics:
        # Default: order by first metric descending
        first_metric = query_spec.metrics[0]
        metric_alias = (
            first_metric.alias or f"{first_metric.aggregation}_{first_metric.column}"
        )
        order_clause = f"ORDER BY {quote_identifier(metric_alias)} DESC"

    # Build LIMIT clause
    limit_clause = ""
    if query_spec.limit:
        limit_clause = f"LIMIT {query_spec.limit}"

    # Assemble SQL
    sql_parts = [select_clause, from_clause]
    if where_clause:
        sql_parts.append(where_clause)
    if group_clause:
        sql_parts.append(group_clause)
    if order_clause:
        sql_parts.append(order_clause)
    if limit_clause:
        sql_parts.append(limit_clause)

    return "\n".join(sql_parts)


def get_compilation_summary(query_spec: QuerySpec) -> dict:
    """Get a human-readable summary of what the query will do."""
    summary = {
        "intent": "Analyze",
        "what": [],
        "by": [],
        "filters": [],
        "time_range": None,
    }

    # What metrics
    for metric in query_spec.metrics:
        desc = f"{metric.aggregation} of {metric.column}"
        if metric.alias:
            desc += f" (as {metric.alias})"
        summary["what"].append(desc)

    # By dimensions
    for dim in query_spec.dimensions:
        summary["by"].append(dim)

    # Filters
    for filter_ in query_spec.filters:
        summary["filters"].append(
            f"{filter_.column} {filter_.operator} {filter_.value}"
        )

    # Time range
    if query_spec.time_range:
        tr = query_spec.time_range
        summary["time_range"] = f"{tr.start or 'beginning'} to {tr.end or 'now'}"

    return summary
