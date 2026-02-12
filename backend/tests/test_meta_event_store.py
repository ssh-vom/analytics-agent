import asyncio
import tempfile
import unittest
from pathlib import Path

import meta
import api.threads as threads
import api.worldlines as worldlines


class MetaEventStoreCharacterizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        temp_root = Path(self.temp_dir.name)

        meta.DB_DIR = temp_root / "data"
        meta.DB_PATH = meta.DB_DIR / "meta.db"
        meta.init_meta_db()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _run(self, coro):
        return asyncio.run(coro)

    def _create_worldline(self) -> str:
        thread_id = self._run(
            threads.create_thread(
                threads.CreateThreadRequest(title="meta-event-store-test")
            )
        )["thread_id"]
        worldline_id = self._run(
            worldlines.create_worldline(
                worldlines.CreateWorldlineRequest(thread_id=thread_id, name="main")
            )
        )["worldline_id"]
        return worldline_id

    def test_stale_parent_snapshot_is_rejected(self) -> None:
        """Reject stale-head appends to prevent divergent event chains."""
        worldline_id = self._create_worldline()

        with meta.get_conn() as conn:
            anchor_id = meta.append_event_and_advance_head(
                conn,
                worldline_id=worldline_id,
                expected_head_event_id=None,
                event_type="assistant_message",
                payload={"text": "anchor"},
            )
            conn.commit()

        stale_head_id = anchor_id

        with meta.get_conn() as conn:
            first_child_id = meta.append_event_and_advance_head(
                conn,
                worldline_id=worldline_id,
                expected_head_event_id=stale_head_id,
                event_type="assistant_message",
                payload={"text": "first"},
            )
            conn.commit()

        with meta.get_conn() as conn:
            with self.assertRaises(meta.EventStoreConflictError):
                meta.append_event_and_advance_head(
                    conn,
                    worldline_id=worldline_id,
                    expected_head_event_id=stale_head_id,
                    event_type="assistant_message",
                    payload={"text": "second"},
                )
            conn.rollback()

        with meta.get_conn() as conn:
            children = conn.execute(
                """
                SELECT id
                FROM events
                WHERE worldline_id = ? AND parent_event_id = ?
                ORDER BY rowid ASC
                """,
                (worldline_id, stale_head_id),
            ).fetchall()
            head_event_id = conn.execute(
                "SELECT head_event_id FROM worldlines WHERE id = ?",
                (worldline_id,),
            ).fetchone()["head_event_id"]
            reachable = {
                row["id"]
                for row in conn.execute(
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
                    SELECT id FROM chain
                    """,
                    (head_event_id,),
                ).fetchall()
            }

        self.assertEqual(len(children), 1)
        self.assertEqual(children[0]["id"], first_child_id)
        self.assertEqual(head_event_id, first_child_id)
        self.assertIn(first_child_id, reachable)


if __name__ == "__main__":
    unittest.main()
