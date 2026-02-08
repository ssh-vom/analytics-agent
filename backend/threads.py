from fastapi import APIRouter
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
