from pydantic.main import BaseModel
from meta import new_id, get_conn
from fastapi import APIRouter


router = APIRouter(prefix="/api", tags=["worldlines"])


class CreateWorldlineRequest(BaseModel):
    thread_id: str
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
