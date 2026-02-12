from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

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


@router.get("/threads/{thread_id}/worldline-summaries")
async def get_thread_worldline_summaries(
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
            WITH event_stats AS (
                SELECT
                    worldline_id,
                    COUNT(
                        CASE
                            WHEN type IN ('user_message', 'assistant_message') THEN 1
                        END
                    ) AS message_count,
                    MAX(created_at) AS last_event_at
                FROM events
                GROUP BY worldline_id
            ),
            job_stats AS (
                SELECT
                    worldline_id,
                    SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END) AS queued_jobs,
                    SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS running_jobs,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed_jobs,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_jobs,
                    SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled_jobs,
                    MAX(COALESCE(finished_at, started_at, created_at)) AS last_job_activity
                FROM chat_turn_jobs
                GROUP BY worldline_id
            )
            SELECT
                w.id,
                w.parent_worldline_id,
                w.forked_from_event_id,
                w.head_event_id,
                w.name,
                w.created_at,
                COALESCE(es.message_count, 0) AS message_count,
                es.last_event_at,
                COALESCE(js.queued_jobs, 0) AS queued_jobs,
                COALESCE(js.running_jobs, 0) AS running_jobs,
                COALESCE(js.completed_jobs, 0) AS completed_jobs,
                COALESCE(js.failed_jobs, 0) AS failed_jobs,
                COALESCE(js.cancelled_jobs, 0) AS cancelled_jobs,
                (
                    SELECT j.status
                    FROM chat_turn_jobs j
                    WHERE j.worldline_id = w.id
                    ORDER BY datetime(COALESCE(j.finished_at, j.started_at, j.created_at)) DESC,
                             datetime(j.created_at) DESC,
                             j.id DESC
                    LIMIT 1
                ) AS latest_job_status,
                COALESCE(
                    CASE
                        WHEN js.last_job_activity IS NOT NULL AND es.last_event_at IS NOT NULL THEN
                            CASE
                                WHEN datetime(js.last_job_activity) >= datetime(es.last_event_at)
                                    THEN js.last_job_activity
                                ELSE es.last_event_at
                            END
                        WHEN js.last_job_activity IS NOT NULL THEN js.last_job_activity
                        WHEN es.last_event_at IS NOT NULL THEN es.last_event_at
                        ELSE w.created_at
                    END,
                    w.created_at
                ) AS last_activity
            FROM worldlines w
            LEFT JOIN event_stats es ON es.worldline_id = w.id
            LEFT JOIN job_stats js ON js.worldline_id = w.id
            WHERE w.thread_id = ?
            ORDER BY datetime(w.created_at) ASC, w.id ASC
            """,
            (thread_id,),
        ).fetchall()

    summaries: list[dict[str, object]] = []
    for row in rows:
        summaries.append(
            {
                "id": row["id"],
                "parent_worldline_id": row["parent_worldline_id"],
                "forked_from_event_id": row["forked_from_event_id"],
                "head_event_id": row["head_event_id"],
                "name": row["name"],
                "created_at": row["created_at"],
                "message_count": int(row["message_count"]),
                "last_event_at": row["last_event_at"],
                "last_activity": row["last_activity"],
                "jobs": {
                    "queued": int(row["queued_jobs"]),
                    "running": int(row["running_jobs"]),
                    "completed": int(row["completed_jobs"]),
                    "failed": int(row["failed_jobs"]),
                    "cancelled": int(row["cancelled_jobs"]),
                    "latest_status": row["latest_job_status"],
                },
            }
        )

    try:
        page, next_cursor = paginate_by_cursor(summaries, cursor=cursor, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"worldlines": page, "next_cursor": next_cursor}
