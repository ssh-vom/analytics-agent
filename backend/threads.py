from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

try:
    from backend.meta import get_conn, new_id
except ModuleNotFoundError:
    from meta import get_conn, new_id


router = APIRouter(prefix="/api", tags=["threads"])


class CreateThreadRequest(BaseModel):
    title: str | None = None


@router.post("/threads")
async def create_thread(body: CreateThreadRequest | None = None):
    thread_id = new_id("thread")
    title = body.title if body and body.title else "New Base Thread"
    with get_conn() as conn:
        _ = conn.execute(
            "INSERT INTO threads (id, title) VALUES (?, ?)", (thread_id, title)
        )
        conn.commit()

    return {"thread_id": thread_id}


@router.get("/threads/{thread_id}/worldlines")
async def get_thread_worldlines(
    thread_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    cursor: str | None = None,
):
    with get_conn() as conn:
        thread = conn.execute(
            "SELECT id FROM threads WHERE id = ?",
            (thread_id,),
        ).fetchone()
        if thread is None:
            raise HTTPException(status_code=404, detail="thread not found")

        rows = conn.execute(
            """
            SELECT id, parent_worldline_id, forked_from_event_id, head_event_id, name, created_at
            FROM worldlines
            WHERE thread_id = ?
            ORDER BY created_at ASC
            """,
            (thread_id,),
        ).fetchall()

    worldlines = []
    for row in rows:
        worldlines.append(
            {
                "id": row["id"],
                "parent_worldline_id": row["parent_worldline_id"],
                "forked_from_event_id": row["forked_from_event_id"],
                "head_event_id": row["head_event_id"],
                "name": row["name"],
                "created_at": row["created_at"],
            }
        )

    if cursor:
        idx = next((i for i, w in enumerate(worldlines) if w["id"] == cursor), None)
        if idx is None:
            raise HTTPException(status_code=400, detail="invalid cursor")
        worldlines = worldlines[idx + 1 :]

    page = worldlines[:limit]
    next_cursor = page[-1]["id"] if len(worldlines) > limit else None
    return {"worldlines": page, "next_cursor": next_cursor}
