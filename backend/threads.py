from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

if (__package__ or "").startswith("backend"):
    from backend.meta import get_conn, new_id, paginate_by_cursor
else:
    from meta import get_conn, new_id, paginate_by_cursor


router = APIRouter(prefix="/api", tags=["threads"])


class CreateThreadRequest(BaseModel):
    title: str | None = None


@router.post("/threads")
async def create_thread(body: CreateThreadRequest | None = None):
    thread_id = new_id("thread")
    title = body.title if body and body.title else "New Base Thread"
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO threads (id, title) VALUES (?, ?)", (thread_id, title)
        )
        conn.commit()

    return {"thread_id": thread_id}


@router.get("/threads")
async def list_threads(
    limit: int = Query(default=100, ge=1, le=500),
    cursor: str | None = None,
):
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                t.id,
                t.title,
                t.created_at,
                COUNT(CASE WHEN e.type IN ('user_message', 'assistant_message') THEN 1 END) AS message_count,
                COALESCE(MAX(e.created_at), t.created_at) AS last_activity
            FROM threads t
            LEFT JOIN worldlines w ON w.thread_id = t.id
            LEFT JOIN events e ON e.worldline_id = w.id
            GROUP BY t.id, t.title, t.created_at
            ORDER BY datetime(last_activity) DESC, datetime(t.created_at) DESC, t.id DESC
            """
        ).fetchall()

    threads = []
    for row in rows:
        threads.append(
            {
                "id": row["id"],
                "title": row["title"],
                "created_at": row["created_at"],
                "message_count": row["message_count"],
                "last_activity": row["last_activity"],
            }
        )

    try:
        page, next_cursor = paginate_by_cursor(threads, cursor=cursor, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"threads": page, "next_cursor": next_cursor}


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

    try:
        page, next_cursor = paginate_by_cursor(worldlines, cursor=cursor, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"worldlines": page, "next_cursor": next_cursor}
