from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pathlib import Path
from meta import get_conn


router = APIRouter(prefix="/api", tags=["artifacts"])


@router.get("/artifacts/{artifact_id}")
async def get_artifact(artifact_id: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, type, name, path FROM ARTIFACTS WHERE id = ?", (artifact_id,)
        ).fetchone()

    if row is None:
        raise HTTPException(404, "artifact not found")

    file_path = Path(row["path"])
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(404, "artifact file not found")

    return FileResponse(path=str(file_path), filename=row["name"])
