import json
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

try:
    from backend.meta import get_conn, new_id
    from backend.worldline_service import BranchOptions, WorldlineService
except ModuleNotFoundError:
    from meta import get_conn, new_id
    from worldline_service import BranchOptions, WorldlineService

router = APIRouter(prefix="/api", tags=["worldlines"])
_worldline_service = WorldlineService()


class CreateWorldlineRequest(BaseModel):
    thread_id: str
    name: str | None = None


class BranchWorldlineRequest(BaseModel):
    from_event_id: str
    name: str | None = None


@router.post("/worldlines")
async def create_worldline(body: CreateWorldlineRequest):
    worldline_id = new_id("worldline")
    thread_id = body.thread_id
    parent_worldline_id = None
    forked_from_event_id = None
    head_event_id = None
    name = body.name if body and body.name else "main"
    with get_conn() as conn:
        thread_row = conn.execute(
            "SELECT id FROM threads WHERE id = ?",
            (thread_id,),
        ).fetchone()
        if thread_row is None:
            raise HTTPException(status_code=404, detail="thread not found")

        _ = conn.execute(
            "INSERT INTO worldlines (id, thread_id, parent_worldline_id, forked_from_event_id, head_event_id, name) VALUES (?, ?, ?, ?, ?, ?)",
            (
                worldline_id,
                thread_id,
                parent_worldline_id,
                forked_from_event_id,
                head_event_id,
                name,
            ),
        )
        conn.commit()

    return {"worldline_id": worldline_id}


@router.post("/worldlines/{worldline_id}/branch")
async def branch_worldline(worldline_id: str, body: BranchWorldlineRequest):
    result = _worldline_service.branch_from_event(
        BranchOptions(
            source_worldline_id=worldline_id,
            from_event_id=body.from_event_id,
            name=body.name,
            append_events=False,
            carried_user_message=None,
        )
    )

    return {"new_worldline_id": result.new_worldline_id}


@router.get("/worldlines/{worldline_id}/events")
async def get_worldline_events(
    worldline_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    cursor: str | None = None,
):
    with get_conn() as conn:
        worldline = conn.execute(
            "SELECT head_event_id FROM worldlines WHERE id = ?",
            (worldline_id,),
        ).fetchone()
        if worldline is None:
            raise HTTPException(status_code=404, detail="worldline not found")

        head_event_id = worldline["head_event_id"]
        if head_event_id is None:
            return {"events": [], "next_cursor": None}

        rows = conn.execute(
            """
            WITH RECURSIVE chain AS (
                SELECT id, parent_event_id, type, payload_json, created_at, 0 AS depth
                FROM events
                WHERE id = ?
                UNION ALL
                SELECT e.id, e.parent_event_id, e.type, e.payload_json, e.created_at, chain.depth + 1
                FROM events e
                JOIN chain ON chain.parent_event_id = e.id
            )
            SELECT id, parent_event_id, type, payload_json, created_at, depth
            FROM chain
            ORDER BY depth DESC
            """,
            (head_event_id,),
        ).fetchall()

    events = []
    for row in rows:
        events.append(
            {
                "id": row["id"],
                "parent_event_id": row["parent_event_id"],
                "type": row["type"],
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
            }
        )

    if cursor:
        idx = next((i for i, e in enumerate(events) if e["id"] == cursor), None)
        if idx is None:
            raise HTTPException(status_code=400, detail="invalid cursor")
        events = events[idx + 1 :]

    page = events[:limit]
    next_cursor = page[-1]["id"] if len(events) > limit else None
    return {"events": page, "next_cursor": next_cursor}
