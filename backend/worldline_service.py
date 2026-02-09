from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException

try:
    from backend.meta import append_event, get_conn, new_id, set_worldline_head
except ModuleNotFoundError:
    from meta import append_event, get_conn, new_id, set_worldline_head


@dataclass(frozen=True)
class BranchOptions:
    source_worldline_id: str
    from_event_id: str
    name: str | None = None
    append_events: bool = False
    carried_user_message: str | None = None


@dataclass(frozen=True)
class BranchResult:
    new_worldline_id: str
    thread_id: str
    source_worldline_id: str
    from_event_id: str
    name: str
    created_event_ids: tuple[str, ...] = ()
    switched: bool = False


class WorldlineService:
    def branch_from_event(self, options: BranchOptions) -> BranchResult:
        created_event_ids: list[str] = []

        with get_conn() as conn:
            source_worldline = conn.execute(
                "SELECT id, thread_id FROM worldlines WHERE id = ?",
                (options.source_worldline_id,),
            ).fetchone()
            if source_worldline is None:
                raise HTTPException(
                    status_code=404, detail="source worldline not found"
                )

            source_event = conn.execute(
                "SELECT id, worldline_id FROM events WHERE id = ?",
                (options.from_event_id,),
            ).fetchone()
            if source_event is None:
                raise HTTPException(status_code=404, detail="from_event_id not found")

            if source_event["worldline_id"] != options.source_worldline_id:
                raise HTTPException(
                    status_code=400,
                    detail="from_event_id does not belong to source worldline",
                )

            new_worldline_id = new_id("worldline")
            branch_name = options.name or f"branch-{options.from_event_id[-6:]}"

            conn.execute(
                """
                INSERT INTO worldlines
                (id, thread_id, parent_worldline_id, forked_from_event_id, head_event_id, name)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    new_worldline_id,
                    source_worldline["thread_id"],
                    options.source_worldline_id,
                    options.from_event_id,
                    options.from_event_id,
                    branch_name,
                ),
            )

            if options.append_events:
                worldline_created_event_id = append_event(
                    conn=conn,
                    worldline_id=new_worldline_id,
                    parent_event_id=options.from_event_id,
                    event_type="worldline_created",
                    payload={
                        "new_worldline_id": new_worldline_id,
                        "parent_worldline_id": options.source_worldline_id,
                        "forked_from_event_id": options.from_event_id,
                        "name": branch_name,
                    },
                )
                created_event_ids.append(worldline_created_event_id)

                time_travel_event_id = append_event(
                    conn=conn,
                    worldline_id=new_worldline_id,
                    parent_event_id=worldline_created_event_id,
                    event_type="time_travel",
                    payload={
                        "from_worldline_id": options.source_worldline_id,
                        "from_event_id": options.from_event_id,
                        "new_worldline_id": new_worldline_id,
                        "name": branch_name,
                    },
                )
                created_event_ids.append(time_travel_event_id)

                if options.carried_user_message:
                    carried_user_event_id = append_event(
                        conn=conn,
                        worldline_id=new_worldline_id,
                        parent_event_id=time_travel_event_id,
                        event_type="user_message",
                        payload={
                            "text": options.carried_user_message,
                            "carried_from_worldline_id": options.source_worldline_id,
                        },
                    )
                    created_event_ids.append(carried_user_event_id)
                    set_worldline_head(conn, new_worldline_id, carried_user_event_id)
                else:
                    set_worldline_head(conn, new_worldline_id, time_travel_event_id)

            conn.commit()

        return BranchResult(
            new_worldline_id=new_worldline_id,
            thread_id=source_worldline["thread_id"],
            source_worldline_id=options.source_worldline_id,
            from_event_id=options.from_event_id,
            name=branch_name,
            created_event_ids=tuple(created_event_ids),
            switched=options.append_events,
        )
