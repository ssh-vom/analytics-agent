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

if (__package__ or "").startswith("backend"):
    from backend import meta
    from backend.duckdb_manager import (
        attach_read_only_database,
        open_worldline_connection,
        worldline_db_path,
    )
else:
    import meta
    from duckdb_manager import (
        attach_read_only_database,
        open_worldline_connection,
        worldline_db_path,
    )

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


def _ensure_semantic_overrides_table(conn: duckdb.DuckDBPyConnection) -> None:
    """Ensure the _semantic_overrides metadata table exists."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _semantic_overrides (
            id VARCHAR PRIMARY KEY,
            table_name VARCHAR NOT NULL,
            column_name VARCHAR NOT NULL,
            role VARCHAR NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(table_name, column_name)
        )
    """)


def import_csv_to_worldline(
    worldline_id: str,
    file_path: Path,
    table_name: str | None = None,
    source_filename: str | None = None,
    if_exists: str = "fail",
) -> CSVImportResult:
    """
    Import a CSV file into a worldline's DuckDB.

    Args:
        worldline_id: The worldline to import into
        file_path: Path to the CSV file
        table_name: Optional table name (auto-generated from filename if not provided)
        source_filename: Original uploaded filename used for metadata and table auto-naming
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

    source_filename_for_metadata = (source_filename or "").strip() or file_path.name

    # Generate or sanitize table name
    if table_name is None:
        table_name = _generate_table_name_from_filename(source_filename_for_metadata)
    else:
        table_name = _sanitize_table_name(table_name)

    started = time.perf_counter()
    conn = open_worldline_connection(worldline_id)

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
            (import_id, table_name, source_filename_for_metadata, row_count),
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

    conn = open_worldline_connection(worldline_id)

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
        attach_read_only_database(conn, alias=alias, db_path=str(external_path))

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
    sanitized_alias = _sanitize_table_name(alias)
    db_path = worldline_db_path(worldline_id)

    if not db_path.exists():
        raise HTTPException(status_code=404, detail="Worldline database not found")

    conn = open_worldline_connection(worldline_id)

    try:
        _ensure_external_sources_table(conn)
        existing = conn.execute(
            "SELECT alias FROM _external_sources WHERE alias = ?",
            (sanitized_alias,),
        ).fetchone()
        if existing is None:
            raise HTTPException(
                status_code=404,
                detail=f"Attached database alias not found: {sanitized_alias}",
            )

        try:
            conn.execute(f'DETACH "{sanitized_alias}"')
        except Exception:
            pass

        # Remove from metadata
        conn.execute(
            "DELETE FROM _external_sources WHERE alias = ?", (sanitized_alias,)
        )
        conn.commit()

        return {"alias": sanitized_alias, "status": "detached"}

    except HTTPException:
        raise
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

    conn = open_worldline_connection(worldline_id)

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

    conn = open_worldline_connection(worldline_id)

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

    conn = open_worldline_connection(worldline_id)

    try:
        # Get native tables from main schema only and dedupe by table name.
        # Some DuckDB versions can expose duplicate rows via information_schema views.
        native_tables = []
        table_rows = conn.execute(
            """
            SELECT DISTINCT table_name
            FROM information_schema.tables
            WHERE table_schema = 'main'
              AND table_type = 'BASE TABLE'
            ORDER BY table_name
            """
        ).fetchall()

        for row in table_rows:
            table_name = row[0]
            if table_name.startswith("_"):
                continue

            columns = conn.execute(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'main'
                  AND table_name = ?
                ORDER BY ordinal_position
                """,
                (table_name,),
            ).fetchall()

            native_tables.append(
                {
                    "schema": "main",
                    "name": table_name,
                    "columns": [{"name": c[0], "type": c[1]} for c in columns],
                }
            )

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


def get_semantic_overrides(worldline_id: str) -> list[dict[str, Any]]:
    """
    Get all semantic overrides for a worldline.

    Args:
        worldline_id: The worldline to query

    Returns:
        List of override records with table_name, column_name, role
    """
    db_path = worldline_db_path(worldline_id)

    if not db_path.exists():
        return []

    conn = open_worldline_connection(worldline_id)

    try:
        # Check if overrides table exists
        table_exists = conn.execute("""
            SELECT table_name FROM information_schema.tables 
            WHERE table_name = '_semantic_overrides'
        """).fetchone()

        if not table_exists:
            return []

        results = conn.execute("""
            SELECT table_name, column_name, role, updated_at
            FROM _semantic_overrides
            ORDER BY table_name, column_name
        """).fetchall()

        return [
            {
                "table_name": r[0],
                "column_name": r[1],
                "role": r[2],
                "updated_at": r[3],
            }
            for r in results
        ]

    except Exception:
        return []
    finally:
        conn.close()


def set_semantic_overrides(
    worldline_id: str, overrides: list[dict[str, str]]
) -> list[dict[str, Any]]:
    """
    Batch set semantic overrides for a worldline.
    Replaces all existing overrides with the new set.

    Args:
        worldline_id: The worldline to update
        overrides: List of overrides, each with table_name, column_name, role

    Returns:
        The updated list of overrides
    """
    conn = open_worldline_connection(worldline_id)

    try:
        _ensure_semantic_overrides_table(conn)

        # Clear existing overrides
        conn.execute("DELETE FROM _semantic_overrides")

        # Insert new overrides
        for override in overrides:
            override_id = f"override_{uuid4().hex[:12]}"
            conn.execute(
                """
                INSERT INTO _semantic_overrides (id, table_name, column_name, role)
                VALUES (?, ?, ?, ?)
                """,
                (
                    override_id,
                    override["table_name"],
                    override["column_name"],
                    override["role"],
                ),
            )

        conn.commit()

        # Return the updated overrides
        return get_semantic_overrides(worldline_id)

    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"Failed to set overrides: {str(e)}"
        )
    finally:
        conn.close()


def delete_semantic_override(
    worldline_id: str, table_name: str, column_name: str
) -> dict[str, Any]:
    """
    Delete a single semantic override.

    Args:
        worldline_id: The worldline to update
        table_name: The table name
        column_name: The column name

    Returns:
        Status of the delete operation
    """
    conn = open_worldline_connection(worldline_id)

    try:
        _ensure_semantic_overrides_table(conn)

        # Check if override exists
        existing = conn.execute(
            """
            SELECT id FROM _semantic_overrides 
            WHERE table_name = ? AND column_name = ?
            """,
            (table_name, column_name),
        ).fetchone()

        if not existing:
            raise HTTPException(
                status_code=404,
                detail=f"Override not found for {table_name}.{column_name}",
            )

        conn.execute(
            """
            DELETE FROM _semantic_overrides 
            WHERE table_name = ? AND column_name = ?
            """,
            (table_name, column_name),
        )
        conn.commit()

        return {
            "table_name": table_name,
            "column_name": column_name,
            "status": "deleted",
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"Failed to delete override: {str(e)}"
        )
    finally:
        conn.close()
