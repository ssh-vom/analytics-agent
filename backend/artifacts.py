import importlib
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

try:
    meta = importlib.import_module("backend.meta")
except ModuleNotFoundError:
    meta = importlib.import_module("meta")


router = APIRouter(prefix="/api", tags=["artifacts"])


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


@router.get("/artifacts/{artifact_id}")
async def get_artifact(artifact_id: str):
    with meta.get_conn() as conn:
        row = conn.execute(
            "SELECT id, worldline_id, type, name, path FROM artifacts WHERE id = ?",
            (artifact_id,),
        ).fetchone()

    if row is None:
        raise HTTPException(404, "artifact not found")

    candidate_path = _resolve_artifact_candidate_path(
        worldline_id=row["worldline_id"], raw_path=row["path"]
    )
    try:
        file_path = candidate_path.resolve(strict=True)
    except OSError:
        raise HTTPException(404, "artifact file not found")

    if not file_path.is_file():
        raise HTTPException(404, "artifact file not found")

    if not _is_allowed_artifact_path(
        worldline_id=row["worldline_id"], resolved_file_path=file_path
    ):
        raise HTTPException(403, "artifact path not allowed")

    return FileResponse(path=str(file_path), filename=row["name"])
