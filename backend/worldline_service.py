from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException

from meta import (
    EventStoreConflictError,
    append_event_and_advance_head,
    get_conn,
    new_id,
)
from duckdb_manager import (
    clone_worldline_db_from_file,
    copy_external_sources_to_worldline,
    ensure_worldline_db,
    worldline_db_path,
)


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

    def to_tool_result(self) -> dict[str, object]:
        return {
            "new_worldline_id": self.new_worldline_id,
            "from_event_id": self.from_event_id,
            "name": self.name,
            "created_event_ids": list(self.created_event_ids),
            "switched": self.switched,
        }


class WorldlineService:
    def _resolve_branch_state_source_path(
        self,
        conn,
        *,
        source_worldline_id: str,
        source_head_event_id: str | None,
        from_event_id: str,
    ) -> Path | None:
        snapshot_row = conn.execute(
            """
            WITH RECURSIVE chain AS (
                SELECT id, parent_event_id, 0 AS depth
                FROM events
                WHERE id = ?
                UNION ALL
                SELECT e.id, e.parent_event_id, chain.depth + 1
                FROM events e
                JOIN chain ON chain.parent_event_id = e.id
            )
            SELECT s.duckdb_path
            FROM chain
            JOIN snapshots s ON s.event_id = chain.id
            ORDER BY chain.depth ASC
            LIMIT 1
            """,
            (from_event_id,),
        ).fetchone()

        if snapshot_row is not None:
            snapshot_path = Path(str(snapshot_row["duckdb_path"]))
            if snapshot_path.exists():
                return snapshot_path

        if source_head_event_id == from_event_id:
            source_path = worldline_db_path(source_worldline_id)
            if source_path.exists():
                return source_path

        return None

    def _event_in_history(
        self, conn, *, head_event_id: str | None, event_id: str
    ) -> bool:
        if head_event_id is None:
            return False

        row = conn.execute(
            """
            WITH RECURSIVE chain AS (
                SELECT id, parent_event_id
                FROM events
                WHERE id = ?
                UNION ALL
                SELECT e.id, e.parent_event_id
                FROM events e
                JOIN chain ON chain.parent_event_id = e.id
            )
            SELECT 1 AS found
            FROM chain
            WHERE id = ?
            LIMIT 1
            """,
            (head_event_id, event_id),
        ).fetchone()

        return row is not None

    def branch_from_event(self, options: BranchOptions) -> BranchResult:
        created_event_ids: list[str] = []

        with get_conn() as conn:
            source_worldline = conn.execute(
                "SELECT id, thread_id, head_event_id FROM worldlines WHERE id = ?",
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

            if source_event[
                "worldline_id"
            ] != options.source_worldline_id and not self._event_in_history(
                conn,
                head_event_id=source_worldline["head_event_id"],
                event_id=options.from_event_id,
            ):
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
                    None,
                    branch_name,
                ),
            )

            source_state_path = self._resolve_branch_state_source_path(
                conn,
                source_worldline_id=options.source_worldline_id,
                source_head_event_id=source_worldline["head_event_id"],
                from_event_id=options.from_event_id,
            )

            if source_state_path is None:
                _ = ensure_worldline_db(new_worldline_id)
            else:
                _ = clone_worldline_db_from_file(source_state_path, new_worldline_id)

            copy_external_sources_to_worldline(
                options.source_worldline_id, new_worldline_id
            )

            try:
                worldline_created_event_id = append_event_and_advance_head(
                    conn,
                    worldline_id=new_worldline_id,
                    expected_head_event_id=None,
                    event_type="worldline_created",
                    parent_event_id=options.from_event_id,
                    payload={
                        "new_worldline_id": new_worldline_id,
                        "parent_worldline_id": options.source_worldline_id,
                        "forked_from_event_id": options.from_event_id,
                        "name": branch_name,
                    },
                )
                created_event_ids.append(worldline_created_event_id)

                if options.append_events:
                    time_travel_event_id = append_event_and_advance_head(
                        conn,
                        worldline_id=new_worldline_id,
                        expected_head_event_id=worldline_created_event_id,
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
                        carried_user_event_id = append_event_and_advance_head(
                            conn,
                            worldline_id=new_worldline_id,
                            expected_head_event_id=time_travel_event_id,
                            event_type="user_message",
                            payload={
                                "text": options.carried_user_message,
                                "carried_from_worldline_id": options.source_worldline_id,
                            },
                        )
                        created_event_ids.append(carried_user_event_id)
            except EventStoreConflictError as exc:
                raise HTTPException(
                    status_code=409,
                    detail="worldline head moved during branch event creation",
                ) from exc

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
