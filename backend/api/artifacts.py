import csv
import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response

import meta


router = APIRouter(prefix="/api", tags=["artifacts"])

MAX_PREVIEW_ROWS = 500


def _workspace_root_for_worldline(worldline_id: str) -> Path:
    return meta.DB_DIR / "worldlines" / worldline_id / "workspace"


def _resolve_artifact_candidate_path(*, worldline_id: str, raw_path: str) -> Path:
    workspace_root = _workspace_root_for_worldline(worldline_id).resolve()
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        return workspace_root / candidate
    return candidate


def _is_allowed_artifact_path(*, worldline_id: str, resolved_file_path: Path) -> bool:
    try:
        workspace_root = _workspace_root_for_worldline(worldline_id).resolve()
        resolved_file_path.relative_to(workspace_root)
        return True
    except ValueError:
        return False


def _load_artifact_row(artifact_id: str):
    with meta.get_conn() as conn:
        row = conn.execute(
            "SELECT id, worldline_id, type, name, path FROM artifacts WHERE id = ?",
            (artifact_id,),
        ).fetchone()

    if row is None:
        raise HTTPException(404, "artifact not found")
    return row


def _resolve_artifact_file_path(*, worldline_id: str, raw_path: str) -> Path:
    candidate_path = _resolve_artifact_candidate_path(
        worldline_id=worldline_id, raw_path=raw_path
    )
    try:
        file_path = candidate_path.resolve(strict=True)
    except OSError:
        raise HTTPException(404, "artifact file not found")

    if not file_path.is_file():
        raise HTTPException(404, "artifact file not found")

    if not _is_allowed_artifact_path(
        worldline_id=worldline_id, resolved_file_path=file_path
    ):
        raise HTTPException(403, "artifact path not allowed")

    return file_path


def _coerce_header_cell(value: str, index: int) -> str:
    label = value.strip()
    if label:
        return label
    return f"column_{index + 1}"


def _read_csv_preview(
    file_path: Path, *, limit: int
) -> tuple[list[str], list[list[str]], int]:
    preview_rows: list[list[str]] = []

    try:
        with open(file_path, "r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
            if header is None:
                return [], [], 0

            columns = [
                _coerce_header_cell(str(cell), idx) for idx, cell in enumerate(header)
            ]
            row_count = 0

            for raw_row in reader:
                row_count += 1
                normalized = [str(cell) for cell in raw_row]

                if len(normalized) > len(columns):
                    missing = len(normalized) - len(columns)
                    for idx in range(missing):
                        columns.append(f"column_{len(columns) + idx + 1}")
                    for existing in preview_rows:
                        existing.extend([""] * missing)

                if len(normalized) < len(columns):
                    normalized.extend([""] * (len(columns) - len(normalized)))

                if row_count <= limit:
                    preview_rows.append(normalized)
    except UnicodeDecodeError:
        raise HTTPException(400, "CSV preview requires UTF-8 encoded text")
    except csv.Error as exc:
        raise HTTPException(400, f"Unable to parse CSV preview: {exc}")

    return columns, preview_rows, row_count


_INLINE_SAFE_MEDIA_TYPES = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "image/svg+xml",
        "text/plain",
        "text/html",
        "text/csv",
    }
)


def _guess_media_type(file_path: Path, artifact_type: str) -> str:
    guessed, _ = mimetypes.guess_type(str(file_path))
    if guessed:
        return guessed
    type_map = {
        "pdf": "application/pdf",
        "image": "image/png",
        "csv": "text/csv",
    }
    return type_map.get(artifact_type.lower(), "application/octet-stream")


@router.get("/artifacts/{artifact_id}")
async def get_artifact(
    artifact_id: str,
    inline: bool = Query(default=False),
):
    row = _load_artifact_row(artifact_id)
    file_path = _resolve_artifact_file_path(
        worldline_id=row["worldline_id"],
        raw_path=row["path"],
    )

    media_type = _guess_media_type(file_path, str(row["type"]))

    if inline and media_type in _INLINE_SAFE_MEDIA_TYPES:
        content = file_path.read_bytes()
        return Response(
            content=content,
            media_type=media_type,
            headers={"Content-Disposition": f'inline; filename="{row["name"]}"'},
        )

    return FileResponse(
        path=str(file_path), filename=row["name"], media_type=media_type
    )


@router.get("/artifacts/{artifact_id}/preview")
async def preview_artifact(
    artifact_id: str,
    limit: int = Query(default=100, ge=1, le=MAX_PREVIEW_ROWS),
):
    row = _load_artifact_row(artifact_id)
    file_path = _resolve_artifact_file_path(
        worldline_id=row["worldline_id"],
        raw_path=row["path"],
    )

    artifact_type = str(row["type"]).lower()
    if artifact_type != "csv" and file_path.suffix.lower() != ".csv":
        raise HTTPException(400, "Table preview is only available for CSV artifacts")

    columns, rows, row_count = _read_csv_preview(file_path, limit=limit)
    preview_count = len(rows)

    return {
        "artifact_id": row["id"],
        "name": row["name"],
        "type": row["type"],
        "preview": {
            "format": "table",
            "columns": columns,
            "rows": rows,
            "row_count": row_count,
            "preview_count": preview_count,
            "truncated": row_count > preview_count,
        },
    }
