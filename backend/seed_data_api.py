"""
API endpoints for seed data operations - CSV import and external DB attachment.
"""

import importlib
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

if (__package__ or "").startswith("backend"):
    meta_module = importlib.import_module("backend.meta")
    seed_data_module = importlib.import_module("backend.seed_data")
    duckdb_manager_module = importlib.import_module("backend.duckdb_manager")
else:
    meta_module = importlib.import_module("meta")
    seed_data_module = importlib.import_module("seed_data")
    duckdb_manager_module = importlib.import_module("duckdb_manager")

get_conn = meta_module.get_conn
new_id = meta_module.new_id
append_event_and_advance_head = meta_module.append_event_and_advance_head
EventStoreConflictError = meta_module.EventStoreConflictError
get_worldline_row = meta_module.get_worldline_row

import_csv_to_worldline = seed_data_module.import_csv_to_worldline
attach_external_duckdb = seed_data_module.attach_external_duckdb
detach_external_duckdb = seed_data_module.detach_external_duckdb
list_imported_tables = seed_data_module.list_imported_tables
list_attached_databases = seed_data_module.list_attached_databases
get_worldline_schema = seed_data_module.get_worldline_schema
MAX_CSV_FILE_SIZE = seed_data_module.MAX_CSV_FILE_SIZE
TEMP_UPLOAD_DIR = seed_data_module.TEMP_UPLOAD_DIR
capture_worldline_snapshot = duckdb_manager_module.capture_worldline_snapshot

router = APIRouter(prefix="/api/seed-data", tags=["seed-data"])

_TABLE_TYPE_PRIORITY = {
    "native": 1,
    "external": 1,
    "imported_csv": 2,
}


def _max_csv_size_label() -> str:
    return f"{MAX_CSV_FILE_SIZE / 1024 / 1024:.1f}MB"


async def _write_upload_with_size_cap(file: UploadFile, *, destination: Path) -> None:
    bytes_written = 0
    chunk_size = 1024 * 1024

    with open(destination, "wb") as output:
        while True:
            chunk = await file.read(chunk_size)
            if not chunk:
                break
            bytes_written += len(chunk)
            if bytes_written > MAX_CSV_FILE_SIZE:
                raise HTTPException(
                    status_code=413,
                    detail=f"File too large. Max size: {_max_csv_size_label()}",
                )
            output.write(chunk)


def _require_worldline(conn, worldline_id: str):
    worldline = get_worldline_row(conn, worldline_id)
    if worldline is None:
        raise HTTPException(status_code=404, detail="Worldline not found")
    return worldline


def _append_worldline_event_with_retry(
    *,
    worldline_id: str,
    event_type: str,
    payload: dict,
    max_attempts: int = 4,
) -> str:
    attempts = max(1, max_attempts)
    for attempt in range(attempts):
        with get_conn() as conn:
            worldline = _require_worldline(conn, worldline_id)
            try:
                event_id = append_event_and_advance_head(
                    conn,
                    worldline_id=worldline_id,
                    expected_head_event_id=worldline["head_event_id"],
                    event_type=event_type,
                    payload=payload,
                )
                conn.commit()
                return event_id
            except EventStoreConflictError:
                conn.rollback()
                if attempt == attempts - 1:
                    raise HTTPException(
                        status_code=409,
                        detail=f"worldline head moved during {event_type} append",
                    )

    raise HTTPException(
        status_code=409,
        detail=f"worldline head moved during {event_type} append",
    )


def _record_snapshot_for_event(worldline_id: str, event_id: str) -> None:
    try:
        snapshot_path = capture_worldline_snapshot(worldline_id, event_id)
        with get_conn() as conn:
            conn.execute(
                "DELETE FROM snapshots WHERE worldline_id = ? AND event_id = ?",
                (worldline_id, event_id),
            )
            conn.execute(
                """
                INSERT INTO snapshots (id, worldline_id, event_id, duckdb_path)
                VALUES (?, ?, ?, ?)
                """,
                (
                    new_id("snapshot"),
                    worldline_id,
                    event_id,
                    str(snapshot_path),
                ),
            )
            conn.commit()
    except Exception:
        return


def _merge_table_entries(existing: dict, incoming: dict) -> dict:
    existing_priority = _TABLE_TYPE_PRIORITY.get(str(existing.get("type", "")), 0)
    incoming_priority = _TABLE_TYPE_PRIORITY.get(str(incoming.get("type", "")), 0)

    if incoming_priority >= existing_priority:
        preferred, secondary = incoming, existing
    else:
        preferred, secondary = existing, incoming

    merged = {**secondary, **preferred}
    if "columns" not in merged:
        if "columns" in existing:
            merged["columns"] = existing["columns"]
        elif "columns" in incoming:
            merged["columns"] = incoming["columns"]
    return merged


def _dedupe_table_entries(tables: list[dict]) -> list[dict]:
    deduped: dict[tuple[str, str], dict] = {}
    for table in tables:
        key = (str(table.get("schema", "")), str(table.get("name", "")))
        current = deduped.get(key)
        if current is None:
            deduped[key] = table
            continue
        deduped[key] = _merge_table_entries(current, table)

    return sorted(
        deduped.values(),
        key=lambda table: (str(table.get("schema", "")), str(table.get("name", ""))),
    )


class AttachExternalDBRequest(BaseModel):
    db_path: str = Field(..., description="Path to the external DuckDB file")
    alias: str | None = Field(
        default=None, description="Optional alias name (auto-generated if not provided)"
    )


class DetachExternalDBRequest(BaseModel):
    alias: str = Field(..., description="Alias of the attached database to detach")


@router.post("/worldlines/{worldline_id}/import-csv")
async def import_csv_endpoint(
    worldline_id: str,
    file: UploadFile = File(...),
    table_name: str | None = Form(default=None),
    if_exists: str = Form(default="fail"),
):
    """
    Import a CSV file into a worldline's DuckDB.

    - **file**: The CSV file to upload
    - **table_name**: Optional table name (auto-generated from filename if not provided)
    - **if_exists**: What to do if table exists: "fail", "replace", or "append"
    """
    with get_conn() as conn:
        _require_worldline(conn, worldline_id)

    # Save uploaded file temporarily
    TEMP_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = TEMP_UPLOAD_DIR / f"{new_id('temp')}_{file.filename}"

    try:
        await _write_upload_with_size_cap(file, destination=temp_path)

        # Import the CSV
        result = import_csv_to_worldline(
            worldline_id=worldline_id,
            file_path=temp_path,
            table_name=table_name,
            source_filename=file.filename,
            if_exists=if_exists,
        )

        # Record the import as an event
        event_payload = {
            "table_name": result.table_name,
            "source_filename": file.filename,
            "row_count": result.row_count,
            "columns": result.columns,
            "import_time_ms": result.import_time_ms,
        }

        event_id = _append_worldline_event_with_retry(
            worldline_id=worldline_id,
            event_type="csv_import",
            payload=event_payload,
        )
        _record_snapshot_for_event(worldline_id, event_id)

        return {
            "success": True,
            "table_name": result.table_name,
            "row_count": result.row_count,
            "columns": result.columns,
            "import_time_ms": result.import_time_ms,
            "event_id": event_id,
        }

    finally:
        # Clean up temp file
        if temp_path.exists():
            temp_path.unlink()
        await file.close()


@router.post("/worldlines/{worldline_id}/attach-duckdb")
async def attach_duckdb_endpoint(worldline_id: str, request: AttachExternalDBRequest):
    """
    Attach an external DuckDB database to a worldline (read-only).

    - **db_path**: Path to the external DuckDB file
    - **alias**: Optional alias name for the attached database
    """
    with get_conn() as conn:
        _require_worldline(conn, worldline_id)

    # Attach the external database
    result = attach_external_duckdb(
        worldline_id=worldline_id, db_path=request.db_path, alias=request.alias
    )

    # Record the attachment as an event
    event_payload = {
        "alias": result.alias,
        "db_path": result.db_path,
        "attached_at": result.attached_at,
    }

    event_id = _append_worldline_event_with_retry(
        worldline_id=worldline_id,
        event_type="external_db_attached",
        payload=event_payload,
    )
    _record_snapshot_for_event(worldline_id, event_id)

    return {
        "success": True,
        "alias": result.alias,
        "db_path": result.db_path,
        "attached_at": result.attached_at,
        "event_id": event_id,
    }


@router.post("/worldlines/{worldline_id}/detach-duckdb")
async def detach_duckdb_endpoint(worldline_id: str, request: DetachExternalDBRequest):
    """
    Detach an external DuckDB database from a worldline.

    - **alias**: The alias of the attached database to detach
    """
    with get_conn() as conn:
        _require_worldline(conn, worldline_id)

    # Detach the database
    result = detach_external_duckdb(worldline_id, request.alias)

    # Record the detachment as an event
    event_payload = {"alias": request.alias, "status": result["status"]}

    event_id = _append_worldline_event_with_retry(
        worldline_id=worldline_id,
        event_type="external_db_detached",
        payload=event_payload,
    )
    _record_snapshot_for_event(worldline_id, event_id)

    return {
        "success": True,
        "alias": request.alias,
        "status": result["status"],
        "event_id": event_id,
    }


@router.get("/worldlines/{worldline_id}/imported-tables")
async def list_imported_tables_endpoint(worldline_id: str):
    """List all tables imported from CSV files in a worldline."""
    with get_conn() as conn:
        _require_worldline(conn, worldline_id)

    tables = list_imported_tables(worldline_id)
    return {"tables": tables}


@router.get("/worldlines/{worldline_id}/attached-databases")
async def list_attached_databases_endpoint(worldline_id: str):
    """List all attached external databases in a worldline."""
    with get_conn() as conn:
        _require_worldline(conn, worldline_id)

    databases = list_attached_databases(worldline_id)
    return {"attached_databases": databases}


@router.get("/worldlines/{worldline_id}/schema")
async def get_worldline_schema_endpoint(worldline_id: str):
    """
    Get complete schema information for a worldline including:
    - Native tables
    - Imported CSV tables
    - Attached external databases and their tables
    """
    with get_conn() as conn:
        _require_worldline(conn, worldline_id)

    schema = get_worldline_schema(worldline_id)
    return schema


@router.get("/worldlines/{worldline_id}/tables")
async def list_all_tables_endpoint(
    worldline_id: str,
    include_system: bool = Query(default=False, description="Include system tables"),
):
    """
    List all tables available in a worldline, including native, imported, and attached DB tables.
    """
    with get_conn() as conn:
        _require_worldline(conn, worldline_id)

    schema = get_worldline_schema(worldline_id)

    # Build unified table list
    all_tables = []

    # Add native tables
    for table in schema["native_tables"]:
        all_tables.append(
            {
                "name": table["name"],
                "schema": table["schema"],
                "type": "native",
                "columns": table["columns"],
            }
        )

    # Add imported tables
    for table in schema["imported_tables"]:
        all_tables.append(
            {
                "name": table["table_name"],
                "schema": "main",
                "type": "imported_csv",
                "source_filename": table["source_filename"],
                "row_count": table["row_count"],
                "imported_at": table["imported_at"],
            }
        )

    # Add attached database tables
    for db in schema["attached_databases"]:
        for table_name in db["tables"]:
            all_tables.append(
                {
                    "name": f"{db['alias']}.{table_name}",
                    "schema": db["alias"],
                    "type": "external",
                    "source_db": db["db_path"],
                    "attached_at": db["attached_at"],
                }
            )

    all_tables = _dedupe_table_entries(all_tables)

    # Filter out system tables if not requested
    if not include_system:
        all_tables = [
            t
            for t in all_tables
            if not t["name"].startswith("_")
            and t["name"] not in ("_external_sources", "_csv_import_history")
        ]

    return {"tables": all_tables, "count": len(all_tables)}
