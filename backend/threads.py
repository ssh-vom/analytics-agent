from pydantic.main import BaseModel
from meta import new_id, get_conn
from fastapi import APIRouter


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
