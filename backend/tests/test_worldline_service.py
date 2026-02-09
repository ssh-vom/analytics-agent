import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import meta
import threads
import worldlines
from worldline_service import BranchOptions, WorldlineService


class WorldlineServiceTests(unittest.TestCase):
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

    def _create_thread(self) -> str:
        response = self._run(
            threads.create_thread(threads.CreateThreadRequest(title="worldline-service-test"))
        )
        return response["thread_id"]

    def _create_worldline(self, thread_id: str) -> str:
        response = self._run(
            worldlines.create_worldline(
                worldlines.CreateWorldlineRequest(thread_id=thread_id, name="main")
            )
        )
        return response["worldline_id"]

    def test_branch_from_event_with_append_events_creates_chain(self) -> None:
        thread_id = self._create_thread()
        source_worldline_id = self._create_worldline(thread_id)

        with meta.get_conn() as conn:
            conn.execute(
                """
                INSERT INTO events (id, worldline_id, parent_event_id, type, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "event_anchor",
                    source_worldline_id,
                    None,
                    "assistant_message",
                    json.dumps({"text": "anchor"}),
                ),
            )
            conn.execute(
                "UPDATE worldlines SET head_event_id = ? WHERE id = ?",
                ("event_anchor", source_worldline_id),
            )
            conn.commit()

        service = WorldlineService()
        result = service.branch_from_event(
            BranchOptions(
                source_worldline_id=source_worldline_id,
                from_event_id="event_anchor",
                name="branch-service",
                append_events=True,
                carried_user_message="carry this prompt",
            )
        )

        self.assertTrue(result.new_worldline_id.startswith("worldline_"))
        self.assertEqual(result.thread_id, thread_id)
        self.assertEqual(result.source_worldline_id, source_worldline_id)
        self.assertEqual(result.from_event_id, "event_anchor")
        self.assertEqual(result.name, "branch-service")
        self.assertEqual(len(result.created_event_ids), 3)
        self.assertTrue(result.switched)

        with meta.get_conn() as conn:
            branch_row = conn.execute(
                """
                SELECT id, thread_id, parent_worldline_id, forked_from_event_id, head_event_id, name
                FROM worldlines
                WHERE id = ?
                """,
                (result.new_worldline_id,),
            ).fetchone()

            rows = conn.execute(
                """
                SELECT id, parent_event_id, type, payload_json
                FROM events
                WHERE worldline_id = ? AND id IN (?, ?, ?)
                ORDER BY rowid ASC
                """,
                (
                    result.new_worldline_id,
                    result.created_event_ids[0],
                    result.created_event_ids[1],
                    result.created_event_ids[2],
                ),
            ).fetchall()

        self.assertEqual(branch_row["thread_id"], thread_id)
        self.assertEqual(branch_row["parent_worldline_id"], source_worldline_id)
        self.assertEqual(branch_row["forked_from_event_id"], "event_anchor")
        self.assertEqual(branch_row["head_event_id"], result.created_event_ids[-1])
        self.assertEqual(branch_row["name"], "branch-service")

        self.assertEqual([row["type"] for row in rows], ["worldline_created", "time_travel", "user_message"])
        self.assertEqual(rows[0]["parent_event_id"], "event_anchor")
        self.assertEqual(rows[1]["parent_event_id"], rows[0]["id"])
        self.assertEqual(rows[2]["parent_event_id"], rows[1]["id"])
        self.assertEqual(
            json.loads(rows[2]["payload_json"])["text"],
            "carry this prompt",
        )

    def test_branch_result_to_tool_result_shape(self) -> None:
        thread_id = self._create_thread()
        source_worldline_id = self._create_worldline(thread_id)

        with meta.get_conn() as conn:
            conn.execute(
                """
                INSERT INTO events (id, worldline_id, parent_event_id, type, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "event_anchor_2",
                    source_worldline_id,
                    None,
                    "assistant_message",
                    json.dumps({"text": "anchor-2"}),
                ),
            )
            conn.execute(
                "UPDATE worldlines SET head_event_id = ? WHERE id = ?",
                ("event_anchor_2", source_worldline_id),
            )
            conn.commit()

        service = WorldlineService()
        result = service.branch_from_event(
            BranchOptions(
                source_worldline_id=source_worldline_id,
                from_event_id="event_anchor_2",
                name="tool-shape",
                append_events=True,
                carried_user_message="carry me",
            )
        )
        payload = result.to_tool_result()

        self.assertEqual(payload["new_worldline_id"], result.new_worldline_id)
        self.assertEqual(payload["from_event_id"], "event_anchor_2")
        self.assertEqual(payload["name"], "tool-shape")
        self.assertEqual(payload["created_event_ids"], list(result.created_event_ids))
        self.assertTrue(payload["switched"])


if __name__ == "__main__":
    unittest.main()
