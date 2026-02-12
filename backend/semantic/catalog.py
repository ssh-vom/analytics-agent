"""Semantic catalog builder from DuckDB schema introspection."""

from __future__ import annotations

from typing import TYPE_CHECKING

from semantic.types import (
    SemanticCatalog,
    Dataset,
    Column,
    ColumnRole,
    ForeignKey,
    Join,
)

if TYPE_CHECKING:
    from typing import Any
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
            source: str = "native"
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


def detect_foreign_keys(catalog: SemanticCatalog) -> list[ForeignKey]:
    """Detect potential foreign key relationships by naming convention and data.

    Looks for columns ending in '_id' and matches them to tables with corresponding primary keys.
    """
    fks: list[ForeignKey] = []

    # Build map of potential primary keys (id columns)
    pk_candidates: dict[
        str, list[tuple[str, str]]
    ] = {}  # column_name -> [(dataset, column)]

    for dataset in catalog.datasets:
        for col in dataset.columns:
            col_lower = col.name.lower()
            # Look for id columns
            if col_lower == "id" or col_lower.endswith("_id"):
                if col_lower not in pk_candidates:
                    pk_candidates[col_lower] = []
                pk_candidates[col_lower].append((dataset.name, col.name))

    def normalize_table_name(name: str) -> str:
        """Normalize table name for matching (handle singular/plural)."""
        # Remove common suffixes
        if name.endswith("es"):
            name = name[:-2]
        elif name.endswith("s"):
            name = name[:-1]
        return name

    # Look for FK patterns: table_name_id -> table_name.id
    for dataset in catalog.datasets:
        for col in dataset.columns:
            col_lower = col.name.lower()

            # Skip if this is an id column itself
            if col_lower == "id":
                continue

            # Pattern: something_id -> something.id
            if col_lower.endswith("_id"):
                # Extract table name (e.g., "customer_id" -> "customer")
                potential_table = col_lower[:-3]  # Remove "_id"
                potential_table_normalized = normalize_table_name(potential_table)

                # Look for a table with this name that has an "id" column
                for target_dataset in catalog.datasets:
                    target_name_lower = target_dataset.name.split(".")[-1].lower()
                    target_name_normalized = normalize_table_name(target_name_lower)

                    # Match exact or normalized (singular/plural)
                    if (
                        target_name_lower == potential_table
                        or target_name_normalized == potential_table_normalized
                    ):
                        # Found potential match - check if target has id column
                        for target_col in target_dataset.columns:
                            if target_col.name.lower() == "id":
                                fks.append(
                                    ForeignKey(
                                        from_dataset=dataset.name,
                                        from_column=col.name,
                                        to_dataset=target_dataset.name,
                                        to_column=target_col.name,
                                    )
                                )
                                break

    return fks


class JoinGraph:
    """Graph of joinable tables for path finding."""

    def __init__(self, catalog: SemanticCatalog, foreign_keys: list[ForeignKey]):
        self.catalog = catalog
        self.foreign_keys = foreign_keys
        self._adjacency: dict[str, list[tuple[str, ForeignKey]]] = {}
        self._build_graph()

    def _build_graph(self) -> None:
        """Build adjacency list from foreign keys."""
        # Add all datasets as nodes
        for dataset in self.catalog.datasets:
            if dataset.name not in self._adjacency:
                self._adjacency[dataset.name] = []

        # Add edges from foreign keys
        for fk in self.foreign_keys:
            # From -> To (left join)
            self._adjacency[fk.from_dataset].append((fk.to_dataset, fk))

    def find_join_path(self, from_table: str, to_table: str) -> list[ForeignKey] | None:
        """Find shortest join path between two tables using BFS."""
        if from_table == to_table:
            return []

        if from_table not in self._adjacency or to_table not in self._adjacency:
            return None

        # BFS
        visited: set[str] = {from_table}
        queue: list[tuple[str, list[ForeignKey]]] = [(from_table, [])]

        while queue:
            current, path = queue.pop(0)

            for next_table, fk in self._adjacency.get(current, []):
                if next_table == to_table:
                    return path + [fk]

                if next_table not in visited:
                    visited.add(next_table)
                    queue.append((next_table, path + [fk]))

        return None

    def get_related_tables(self, table: str) -> list[str]:
        """Get all tables directly joinable with given table."""
        return [t for t, _ in self._adjacency.get(table, [])]


def build_joins_for_query(
    catalog: SemanticCatalog,
    primary_dataset: str,
    required_datasets: list[str],
) -> list[Join]:
    """Build optimal joins to connect primary dataset with all required datasets.

    Args:
        catalog: Semantic catalog
        primary_dataset: Main dataset for the query
        required_datasets: Additional datasets needed for the query

    Returns:
        List of Join objects connecting all tables
    """
    # Detect foreign keys
    fks = detect_foreign_keys(catalog)
    graph = JoinGraph(catalog, fks)

    joins: list[Join] = []
    connected: set[str] = {primary_dataset}

    # Iteratively add datasets until all are connected
    pending = set(required_datasets) - connected

    while pending:
        best_path: list[ForeignKey] | None = None
        best_dataset: str | None = None

        # Find shortest path from any connected table to any pending table
        for connected_table in list(connected):
            for pending_table in pending:
                path = graph.find_join_path(connected_table, pending_table)
                if path and (best_path is None or len(path) < len(best_path)):
                    best_path = path
                    best_dataset = pending_table

        if best_path is None:
            # Can't connect remaining tables
            break

        # Add joins from the path
        for fk in best_path:
            joins.append(
                Join(
                    from_dataset=fk.from_dataset,
                    to_dataset=fk.to_dataset,
                    from_column=fk.from_column,
                    to_column=fk.to_column,
                    join_type="left",
                )
            )
            connected.add(fk.to_dataset)

        if best_dataset:
            pending.discard(best_dataset)

    return joins


def build_catalog_for_worldline(
    worldline_id: str,
    duckdb_manager: Any = None,
) -> SemanticCatalog | None:
    """Build catalog for a specific worldline using its DuckDB connection."""
    import logging

    try:
        # Import here to avoid circular imports
        from duckdb_manager import open_worldline_connection
        from seed_data import get_semantic_overrides

        conn = open_worldline_connection(worldline_id)
        catalog = build_catalog_from_duckdb(conn)
        conn.close()

        # Apply user overrides
        overrides = get_semantic_overrides(worldline_id)
        if overrides:
            catalog = apply_overrides(catalog, overrides)

        return catalog
    except Exception as e:
        logging.getLogger(__name__).warning(
            f"Failed to build semantic catalog for worldline {worldline_id}: {e}"
        )
        return None


def apply_overrides(catalog: SemanticCatalog, overrides: list[dict]) -> SemanticCatalog:
    """Apply user overrides to column roles in the catalog.

    Args:
        catalog: The semantic catalog to modify
        overrides: List of overrides, each with table_name, column_name, role

    Returns:
        The modified catalog (mutated in place)
    """
    # Build lookup: (table, column) -> role
    override_map: dict[tuple[str, str], ColumnRole] = {}
    for o in overrides:
        key = (o["table_name"].lower(), o["column_name"].lower())
        role = o["role"]
        if role in ("dimension", "measure", "time", "unknown"):
            override_map[key] = role  # type: ignore

    # Apply overrides to catalog columns
    for dataset in catalog.datasets:
        # Try both full name (schema.table) and short name (table)
        dataset_name_lower = dataset.name.lower()
        short_name = dataset_name_lower.split(".")[-1]

        for col in dataset.columns:
            col_name_lower = col.name.lower()

            # Check both full table name and short name
            key_full = (dataset_name_lower, col_name_lower)
            key_short = (short_name, col_name_lower)

            if key_full in override_map:
                col.role = override_map[key_full]
            elif key_short in override_map:
                col.role = override_map[key_short]

    return catalog
