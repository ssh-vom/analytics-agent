"""
Seed data management for importing CSV files and attaching external DuckDB databases.

This module provides functionality to:
1. Import CSV files into worldline DuckDB instances
2. Attach external read-only DuckDB databases
3. Manage data source metadata
"""

import duckdb
import re
from pathlib import Path
from typing import Any
from uuid import uuid4
from fastapi import HTTPException
from pydantic import BaseModel

try:
    from backend import meta
    from backend.duckdb_manager import ensure_worldline_db, worldline_db_path
except ModuleNotFoundError:
    import meta
    from duckdb_manager import ensure_worldline_db, worldline_db_path

# Constants
MAX_CSV_FILE_SIZE = 100 * 1024 * 1024  # 100MB limit
ALLOWED_EXTENSIONS = {".csv"}
TEMP_UPLOAD_DIR = meta.DB_DIR / "temp_uploads"


def _sanitize_table_name(name: str) -> str:
    """
    Sanitize a table name to prevent SQL injection.
    Only allows alphanumeric characters and underscores.
    Must start with a letter.
    """
    # Remove any non-alphanumeric characters except underscore
    sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    # Ensure it starts with a letter
    if sanitized and sanitized[0].isdigit():
        sanitized = "table_" + sanitized
    # Limit length
    if len(sanitized) > 63:
        sanitized = sanitized[:63]
    return sanitized


def _generate_table_name_from_filename(filename: str) -> str:
    """Generate a valid table name from a filename."""
    # Remove extension
    base_name = Path(filename).stem
    # Sanitize
    sanitized = _sanitize_table_name(base_name)
    # Add uniqueness
    return f"{sanitized}_{uuid4().hex[:8]}"


class CSVImportResult(BaseModel):
    """Result of a CSV import operation."""

    table_name: str
    row_count: int
    columns: list[dict[str, str]]
    import_time_ms: int


class ExternalDBAttachment(BaseModel):
    """Metadata for an attached external database."""

    alias: str
    db_path: str
    attached_at: str


def _ensure_external_sources_table(conn: duckdb.DuckDBPyConnection) -> None:
    """Ensure the _external_sources metadata table exists."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _external_sources (
            alias VARCHAR PRIMARY KEY,
            db_path VARCHAR NOT NULL,
            db_type VARCHAR NOT NULL DEFAULT 'duckdb',
            attached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


def _ensure_import_history_table(conn: duckdb.DuckDBPyConnection) -> None:
    """Ensure the _csv_import_history metadata table exists."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _csv_import_history (
            id VARCHAR PRIMARY KEY,
            table_name VARCHAR NOT NULL,
            source_filename VARCHAR NOT NULL,
            row_count INTEGER NOT NULL,
            imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


def import_csv_to_worldline(
    worldline_id: str,
    file_path: Path,
    table_name: str | None = None,
    if_exists: str = "fail",
) -> CSVImportResult:
    """
    Import a CSV file into a worldline's DuckDB.

    Args:
        worldline_id: The worldline to import into
        file_path: Path to the CSV file
        table_name: Optional table name (auto-generated from filename if not provided)
        if_exists: Behavior if table exists: "fail", "replace", "append"

    Returns:
        CSVImportResult with details about the import

    Raises:
        HTTPException: If file is invalid, too large, or import fails
    """
    import time

    # Validate file
    if not file_path.exists():
        raise HTTPException(status_code=400, detail=f"File not found: {file_path}")

    if file_path.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type. Only {', '.join(ALLOWED_EXTENSIONS)} allowed",
        )

    file_size = file_path.stat().st_size
    if file_size > MAX_CSV_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Max size: {MAX_CSV_FILE_SIZE / 1024 / 1024:.1f}MB",
        )

    # Generate or sanitize table name
    if table_name is None:
        table_name = _generate_table_name_from_filename(file_path.name)
    else:
        table_name = _sanitize_table_name(table_name)

    # Ensure worldline DB exists
    db_path = ensure_worldline_db(worldline_id)

    started = time.perf_counter()
    conn = duckdb.connect(str(db_path))

    try:
        # Ensure metadata tables exist
        _ensure_import_history_table(conn)

        # Check if table exists
        existing_tables = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name = ?",
            (table_name,),
        ).fetchall()

        if existing_tables:
            if if_exists == "fail":
                raise HTTPException(
                    status_code=400,
                    detail=f"Table '{table_name}' already exists. Use if_exists='replace' or 'append'",
                )
            elif if_exists == "replace":
                conn.execute(f'DROP TABLE IF EXISTS "{table_name}"')
            # if "append", we don't drop

        # Read CSV and create table
        # Use read_csv_auto for automatic type detection
        read_csv_query = f"""
            CREATE TABLE "{table_name}" AS 
            SELECT * FROM read_csv_auto('{str(file_path)}', header=true, auto_detect=true)
        """

        if if_exists == "append" and existing_tables:
            read_csv_query = f"""
                INSERT INTO "{table_name}" 
                SELECT * FROM read_csv_auto('{str(file_path)}', header=true, auto_detect=true)
            """

        conn.execute(read_csv_query)

        # Get row count and schema
        row_count_result = conn.execute(
            f'SELECT COUNT(*) FROM "{table_name}"'
        ).fetchone()
        row_count = row_count_result[0] if row_count_result else 0

        # Get column info
        columns_result = conn.execute(f"""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name = '{table_name}'
            ORDER BY ordinal_position
        """).fetchall()

        columns = [{"name": col[0], "type": col[1]} for col in columns_result]

        # Record in import history
        import_id = f"import_{uuid4().hex[:12]}"
        conn.execute(
            """
            INSERT INTO _csv_import_history (id, table_name, source_filename, row_count)
            VALUES (?, ?, ?, ?)
        """,
            (import_id, table_name, file_path.name, row_count),
        )

        conn.commit()

        elapsed_ms = int((time.perf_counter() - started) * 1000)

        return CSVImportResult(
            table_name=table_name,
            row_count=row_count,
            columns=columns,
            import_time_ms=elapsed_ms,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"CSV import failed: {str(e)}")
    finally:
        conn.close()


def attach_external_duckdb(
    worldline_id: str,
    db_path: str,
    alias: str | None = None,
) -> ExternalDBAttachment:
    """
    Attach an external DuckDB database to a worldline (read-only).

    Args:
        worldline_id: The worldline to attach to
        db_path: Path to the external DuckDB file
        alias: Optional alias name (derived from filename if not provided)

    Returns:
        ExternalDBAttachment with attachment metadata

    Raises:
        HTTPException: If attachment fails or database not found
    """
    from datetime import datetime

    # Validate external DB path
    external_path = Path(db_path)
    if not external_path.exists():
        raise HTTPException(
            status_code=400, detail=f"Database file not found: {db_path}"
        )

    # Generate or sanitize alias
    if alias is None:
        alias = _sanitize_table_name(external_path.stem)
    else:
        alias = _sanitize_table_name(alias)

    # Ensure worldline DB exists
    worldline_db = ensure_worldline_db(worldline_id)

    conn = duckdb.connect(str(worldline_db))

    try:
        # Ensure metadata table exists
        _ensure_external_sources_table(conn)

        # Check if alias already exists
        existing = conn.execute(
            "SELECT alias FROM _external_sources WHERE alias = ?", (alias,)
        ).fetchone()

        if existing:
            # Detach existing and re-attach
            try:
                conn.execute(f'DETACH "{alias}"')
            except:
                pass  # May not be attached
            conn.execute("DELETE FROM _external_sources WHERE alias = ?", (alias,))

        # Attach the external database (read-only for safety)
        attach_query = f"""
            ATTACH '{str(external_path)}' AS "{alias}" (READ_ONLY)
        """
        conn.execute(attach_query)

        # Record in metadata
        attached_at = datetime.now().isoformat()
        conn.execute(
            """
            INSERT INTO _external_sources (alias, db_path, db_type, attached_at)
            VALUES (?, ?, 'duckdb', ?)
            ON CONFLICT (alias) DO UPDATE SET
                db_path = excluded.db_path,
                attached_at = excluded.attached_at
        """,
            (alias, str(external_path), attached_at),
        )

        conn.commit()

        return ExternalDBAttachment(
            alias=alias, db_path=str(external_path), attached_at=attached_at
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"Failed to attach database: {str(e)}"
        )
    finally:
        conn.close()


def detach_external_duckdb(worldline_id: str, alias: str) -> dict[str, Any]:
    """
    Detach an external DuckDB database from a worldline.

    Args:
        worldline_id: The worldline to detach from
        alias: The alias of the attached database

    Returns:
        Status of the detach operation
    """
    db_path = worldline_db_path(worldline_id)

    if not db_path.exists():
        raise HTTPException(status_code=404, detail="Worldline database not found")

    conn = duckdb.connect(str(db_path))

    try:
        # Detach the database
        conn.execute(f'DETACH "{alias}"')

        # Remove from metadata
        conn.execute("DELETE FROM _external_sources WHERE alias = ?", (alias,))
        conn.commit()

        return {"alias": alias, "status": "detached"}

    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"Failed to detach database: {str(e)}"
        )
    finally:
        conn.close()


def list_imported_tables(worldline_id: str) -> list[dict[str, Any]]:
    """
    List all tables imported from CSV files in a worldline.

    Args:
        worldline_id: The worldline to query

    Returns:
        List of import history records
    """
    db_path = worldline_db_path(worldline_id)

    if not db_path.exists():
        return []

    conn = duckdb.connect(str(db_path))

    try:
        # Check if history table exists
        table_exists = conn.execute("""
            SELECT table_name FROM information_schema.tables 
            WHERE table_name = '_csv_import_history'
        """).fetchone()

        if not table_exists:
            return []

        results = conn.execute("""
            SELECT table_name, source_filename, row_count, imported_at
            FROM _csv_import_history
            ORDER BY imported_at DESC
        """).fetchall()

        return [
            {
                "table_name": r[0],
                "source_filename": r[1],
                "row_count": r[2],
                "imported_at": r[3],
            }
            for r in results
        ]

    except Exception:
        return []
    finally:
        conn.close()


def list_attached_databases(worldline_id: str) -> list[dict[str, Any]]:
    """
    List all attached external databases in a worldline.

    Args:
        worldline_id: The worldline to query

    Returns:
        List of attached database records
    """
    db_path = worldline_db_path(worldline_id)

    if not db_path.exists():
        return []

    conn = duckdb.connect(str(db_path))

    try:
        # Check if sources table exists
        table_exists = conn.execute("""
            SELECT table_name FROM information_schema.tables 
            WHERE table_name = '_external_sources'
        """).fetchone()

        if not table_exists:
            return []

        results = conn.execute("""
            SELECT alias, db_path, db_type, attached_at
            FROM _external_sources
            ORDER BY attached_at DESC
        """).fetchall()

        attached_dbs = []
        for r in results:
            # Also get list of tables in this attached DB
            try:
                tables_result = conn.execute(f'SHOW TABLES FROM "{r[0]}"').fetchall()
                tables = [t[0] for t in tables_result]
            except:
                tables = []

            attached_dbs.append(
                {
                    "alias": r[0],
                    "db_path": r[1],
                    "db_type": r[2],
                    "attached_at": r[3],
                    "tables": tables,
                }
            )

        return attached_dbs

    except Exception:
        return []
    finally:
        conn.close()


def get_worldline_schema(worldline_id: str) -> dict[str, Any]:
    """
    Get complete schema information for a worldline including:
    - Native tables
    - Imported CSV tables
    - Attached external databases and their tables

    Args:
        worldline_id: The worldline to query

    Returns:
        Complete schema information
    """
    db_path = worldline_db_path(worldline_id)

    if not db_path.exists():
        return {"native_tables": [], "imported_tables": [], "attached_databases": []}

    conn = duckdb.connect(str(db_path))

    try:
        # Get all schemas
        schemas = conn.execute(
            "SELECT schema_name FROM information_schema.schemata"
        ).fetchall()
        schema_names = [s[0] for s in schemas]

        # Get native tables (exclude system tables and metadata tables)
        native_tables = []
        for schema in schema_names:
            if schema in ("pg_catalog", "information_schema"):
                continue

            try:
                tables = conn.execute(f"""
                    SELECT table_name 
                    FROM information_schema.tables 
                    WHERE table_schema = '{schema}'
                    AND table_name NOT LIKE '\\_%'  -- Exclude metadata tables
                """).fetchall()

                for t in tables:
                    table_name = t[0]
                    # Get columns for this table
                    columns = conn.execute(f"""
                        SELECT column_name, data_type
                        FROM information_schema.columns
                        WHERE table_schema = '{schema}'
                        AND table_name = '{table_name}'
                        ORDER BY ordinal_position
                    """).fetchall()

                    native_tables.append(
                        {
                            "schema": schema,
                            "name": table_name,
                            "columns": [{"name": c[0], "type": c[1]} for c in columns],
                        }
                    )
            except:
                pass

        # Get imported CSV history
        imported_tables = list_imported_tables(worldline_id)

        # Get attached databases
        attached_databases = list_attached_databases(worldline_id)

        return {
            "native_tables": native_tables,
            "imported_tables": imported_tables,
            "attached_databases": attached_databases,
        }

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to get schema: {str(e)}")
    finally:
        conn.close()
