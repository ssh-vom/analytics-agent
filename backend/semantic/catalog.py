"""Semantic catalog builder from DuckDB schema introspection."""

from __future__ import annotations

from typing import TYPE_CHECKING

from semantic.types import SemanticCatalog, Dataset, Column, ColumnRole

if TYPE_CHECKING:
    from duckdb import DuckDBPyConnection


def is_numeric_type(data_type: str) -> bool:
    """Check if a DuckDB type is numeric."""
    numeric_types = {
        "tinyint",
        "smallint",
        "integer",
        "bigint",
        "hugeint",
        "utinyint",
        "usmallint",
        "uinteger",
        "ubigint",
        "real",
        "double",
        "float",
        "decimal",
        "numeric",
    }
    return data_type.lower() in numeric_types


def is_timestamp_type(data_type: str) -> bool:
    """Check if a DuckDB type is a timestamp/date."""
    timestamp_types = {
        "timestamp",
        "timestamp with time zone",
        "timestamptz",
        "date",
        "time",
        "time with time zone",
        "timetz",
        "interval",
    }
    return data_type.lower() in timestamp_types


def infer_column_role(column_name: str, data_type: str) -> ColumnRole:
    """Infer semantic role from column name and type."""
    name_lower = column_name.lower()

    # Time-related columns
    if is_timestamp_type(data_type):
        return "time"

    # Common time column names
    time_patterns = {
        "date",
        "time",
        "timestamp",
        "created",
        "updated",
        "day",
        "month",
        "year",
    }
    if any(pattern in name_lower for pattern in time_patterns):
        if is_timestamp_type(data_type) or data_type.lower() in {
            "varchar",
            "text",
            "string",
        }:
            return "time"

    # ID columns are typically dimensions
    if name_lower.endswith("_id") or name_lower == "id":
        return "dimension"

    # Numeric columns are likely measures
    if is_numeric_type(data_type):
        return "measure"

    # Text columns are likely dimensions
    if data_type.lower() in {"varchar", "text", "string", "enum"}:
        return "dimension"

    return "unknown"


def infer_semantic_type(column_name: str, data_type: str) -> str | None:
    """Infer semantic type (e.g., currency, percentage) from column name."""
    name_lower = column_name.lower()

    # Currency patterns
    currency_patterns = {
        "price",
        "cost",
        "revenue",
        "sales",
        "amount",
        "value",
        "fee",
        "charge",
    }
    if any(pattern in name_lower for pattern in currency_patterns):
        if is_numeric_type(data_type):
            return "currency"

    # Percentage patterns
    pct_patterns = {"pct", "percent", "rate", "ratio", "margin", "share"}
    if any(pattern in name_lower for pattern in pct_patterns):
        if is_numeric_type(data_type):
            return "percentage"

    # Count patterns
    count_patterns = {"count", "quantity", "qty", "volume", "total"}
    if any(pattern in name_lower for pattern in count_patterns):
        if is_numeric_type(data_type):
            return "count"

    return None


def build_catalog_from_duckdb(
    conn: DuckDBPyConnection,
    schema_filter: str | None = None,
) -> SemanticCatalog:
    """Build semantic catalog by introspecting DuckDB connection.

    Args:
        conn: Active DuckDB connection
        schema_filter: Optional schema name to filter (default: 'main' and attached databases)

    Returns:
        SemanticCatalog with all discovered datasets and columns
    """
    datasets: list[Dataset] = []

    # Get all schemas (databases)
    schema_query = """
        SELECT DISTINCT schema_name 
        FROM information_schema.schemata 
        WHERE schema_name NOT IN ('information_schema', 'pg_catalog')
        ORDER BY schema_name
    """
    schemas = conn.execute(schema_query).fetchall()

    for (schema_name,) in schemas:
        # Skip system schemas
        if schema_name in ("information_schema", "pg_catalog"):
            continue

        # Skip if filtered
        if schema_filter and schema_name != schema_filter:
            continue

        # Get tables in this schema
        table_query = """
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = ?
            ORDER BY table_name
        """
        tables = conn.execute(table_query, [schema_name]).fetchall()

        for (table_name,) in tables:
            # Get column info
            column_query = """
                SELECT column_name, data_type 
                FROM information_schema.columns 
                WHERE table_schema = ? AND table_name = ?
                ORDER BY ordinal_position
            """
            columns_result = conn.execute(
                column_query, [schema_name, table_name]
            ).fetchall()

            columns: list[Column] = []
            for col_name, data_type in columns_result:
                role = infer_column_role(col_name, data_type)
                semantic_type = infer_semantic_type(col_name, data_type)
                columns.append(
                    Column(
                        name=col_name,
                        data_type=data_type,
                        role=role,
                        semantic_type=semantic_type,
                    )
                )

            # Get approximate row count (fast estimate)
            try:
                count_result = conn.execute(
                    f'SELECT COUNT(*) FROM "{schema_name}"."{table_name}"'
                ).fetchone()
                row_count = count_result[0] if count_result else None
            except Exception:
                row_count = None

            # Determine source type
            source: ColumnRole = "native"
            if schema_name != "main":
                source = "external"

            datasets.append(
                Dataset(
                    name=f"{schema_name}.{table_name}",
                    schema=schema_name,
                    columns=columns,
                    row_count=row_count,
                    source=source,
                )
            )

    return SemanticCatalog(datasets=datasets)


def build_catalog_for_worldline(
    worldline_id: str,
    duckdb_manager: Any,
) -> SemanticCatalog | None:
    """Build catalog for a specific worldline using its DuckDB connection."""
    try:
        conn = duckdb_manager.get_connection(worldline_id)
        if not conn:
            return None
        return build_catalog_from_duckdb(conn)
    except Exception as e:
        # Log error but don't crash - fall back to agentic mode
        import logging

        logging.getLogger(__name__).warning(
            f"Failed to build semantic catalog for worldline {worldline_id}: {e}"
        )
        return None
