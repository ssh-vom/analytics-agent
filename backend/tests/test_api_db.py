import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

import meta
import api.threads as threads
import api.worldlines as worldlines


class Stage1ApiDbTests(unittest.TestCase):
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

    def _create_thread(self, title: str = "thread-for-test") -> str:
        response = self._run(
            threads.create_thread(threads.CreateThreadRequest(title=title))
        )
        return response["thread_id"]

    def _create_worldline(self, thread_id: str, name: str = "main") -> str:
        response = self._run(
            worldlines.create_worldline(
                worldlines.CreateWorldlineRequest(thread_id=thread_id, name=name)
            )
        )
        return response["worldline_id"]

    def test_meta_schema_tables_created(self) -> None:
        with meta.get_conn() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        table_names = [row["name"] for row in rows]
        self.assertEqual(
            table_names,
            [
                "artifacts",
                "chat_turn_jobs",
                "events",
                "snapshots",
                "threads",
                "worldlines",
            ],
        )

    def test_create_thread_persists(self) -> None:
        thread_id = self._create_thread("My test thread")

        self.assertTrue(thread_id.startswith("thread_"))
        with meta.get_conn() as conn:
            row = conn.execute(
                "SELECT id, title FROM threads WHERE id = ?",
                (thread_id,),
            ).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row["id"], thread_id)
        self.assertEqual(row["title"], "My test thread")

    def test_create_worldline_persists(self) -> None:
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id, "main")

        self.assertTrue(worldline_id.startswith("worldline_"))
        with meta.get_conn() as conn:
            row = conn.execute(
                """
                SELECT id, thread_id, parent_worldline_id, forked_from_event_id, head_event_id, name
                FROM worldlines
                WHERE id = ?
                """,
                (worldline_id,),
            ).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row["id"], worldline_id)
        self.assertEqual(row["thread_id"], thread_id)
        self.assertIsNone(row["parent_worldline_id"])
        self.assertIsNone(row["forked_from_event_id"])
        self.assertIsNone(row["head_event_id"])
        self.assertEqual(row["name"], "main")

    def test_branch_worldline_creates_child(self) -> None:
        thread_id = self._create_thread()
        source_worldline_id = self._create_worldline(thread_id, "main")

        with meta.get_conn() as conn:
            conn.execute(
                """
                INSERT INTO events (id, worldline_id, parent_event_id, type, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "event_seed_1",
                    source_worldline_id,
                    None,
                    "user_message",
                    '{"text":"hi"}',
                ),
            )
            conn.execute(
                "UPDATE worldlines SET head_event_id = ? WHERE id = ?",
                ("event_seed_1", source_worldline_id),
            )
            conn.commit()

        response = self._run(
            worldlines.branch_worldline(
                source_worldline_id,
                worldlines.BranchWorldlineRequest(
                    from_event_id="event_seed_1", name="branch-a"
                ),
            )
        )
        new_worldline_id = response["new_worldline_id"]

        with meta.get_conn() as conn:
            row = conn.execute(
                """
                SELECT id, thread_id, parent_worldline_id, forked_from_event_id, head_event_id, name
                FROM worldlines
                WHERE id = ?
                """,
                (new_worldline_id,),
            ).fetchone()
            head_event = conn.execute(
                """
                SELECT id, worldline_id, parent_event_id, type, payload_json
                FROM events
                WHERE id = ?
                """,
                (row["head_event_id"],),
            ).fetchone()

        self.assertEqual(row["thread_id"], thread_id)
        self.assertEqual(row["parent_worldline_id"], source_worldline_id)
        self.assertEqual(row["forked_from_event_id"], "event_seed_1")
        self.assertNotEqual(row["head_event_id"], "event_seed_1")
        self.assertEqual(head_event["worldline_id"], new_worldline_id)
        self.assertEqual(head_event["parent_event_id"], "event_seed_1")
        self.assertEqual(head_event["type"], "worldline_created")
        self.assertEqual(
            json.loads(head_event["payload_json"])["new_worldline_id"],
            new_worldline_id,
        )
        self.assertEqual(row["name"], "branch-a")

    def test_branch_worldline_from_ancestor_event_in_history(self) -> None:
        thread_id = self._create_thread()
        source_worldline_id = self._create_worldline(thread_id, "main")

        with meta.get_conn() as conn:
            conn.execute(
                """
                INSERT INTO events (id, worldline_id, parent_event_id, type, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "event_seed_ancestor",
                    source_worldline_id,
                    None,
                    "user_message",
                    '{"text":"ancestor"}',
                ),
            )
            conn.execute(
                "UPDATE worldlines SET head_event_id = ? WHERE id = ?",
                ("event_seed_ancestor", source_worldline_id),
            )
            conn.commit()

        legacy_branch = self._run(
            worldlines.branch_worldline(
                source_worldline_id,
                worldlines.BranchWorldlineRequest(
                    from_event_id="event_seed_ancestor", name="legacy-branch"
                ),
            )
        )["new_worldline_id"]

        # Simulate legacy data where the branch head points to the ancestor event
        # from a different worldline.
        with meta.get_conn() as conn:
            conn.execute(
                "UPDATE worldlines SET head_event_id = ? WHERE id = ?",
                ("event_seed_ancestor", legacy_branch),
            )
            conn.commit()

        response = self._run(
            worldlines.branch_worldline(
                legacy_branch,
                worldlines.BranchWorldlineRequest(
                    from_event_id="event_seed_ancestor", name="branch-from-branch"
                ),
            )
        )
        new_worldline_id = response["new_worldline_id"]

        with meta.get_conn() as conn:
            row = conn.execute(
                """
                SELECT id, thread_id, parent_worldline_id, forked_from_event_id, head_event_id, name
                FROM worldlines
                WHERE id = ?
                """,
                (new_worldline_id,),
            ).fetchone()
            head_event = conn.execute(
                """
                SELECT id, worldline_id, parent_event_id, type
                FROM events
                WHERE id = ?
                """,
                (row["head_event_id"],),
            ).fetchone()

        self.assertEqual(row["thread_id"], thread_id)
        self.assertEqual(row["parent_worldline_id"], legacy_branch)
        self.assertEqual(row["forked_from_event_id"], "event_seed_ancestor")
        self.assertEqual(row["name"], "branch-from-branch")
        self.assertIsNotNone(head_event)
        self.assertEqual(head_event["worldline_id"], new_worldline_id)
        self.assertEqual(head_event["parent_event_id"], "event_seed_ancestor")
        self.assertEqual(head_event["type"], "worldline_created")

    def test_get_worldline_events_returns_chain(self) -> None:
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id, "main")

        with meta.get_conn() as conn:
            conn.execute(
                """
                INSERT INTO events (id, worldline_id, parent_event_id, type, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("event_1", worldline_id, None, "user_message", '{"text":"q1"}'),
            )
            conn.execute(
                """
                INSERT INTO events (id, worldline_id, parent_event_id, type, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "event_2",
                    worldline_id,
                    "event_1",
                    "assistant_message",
                    '{"text":"a1"}',
                ),
            )
            conn.execute(
                "UPDATE worldlines SET head_event_id = ? WHERE id = ?",
                ("event_2", worldline_id),
            )
            conn.commit()

        response = self._run(
            worldlines.get_worldline_events(
                worldline_id=worldline_id, limit=10, cursor=None
            )
        )

        self.assertEqual(
            [event["id"] for event in response["events"]], ["event_1", "event_2"]
        )
        self.assertIsNone(response["next_cursor"])
        self.assertEqual(response["events"][0]["payload"], json.loads('{"text":"q1"}'))

    def test_get_thread_worldlines_pagination(self) -> None:
        thread_id = self._create_thread()
        worldline_a = self._create_worldline(thread_id, "main")
        worldline_b = self._create_worldline(thread_id, "branch-a")

        first_page = self._run(
            threads.get_thread_worldlines(thread_id=thread_id, limit=1, cursor=None)
        )
        self.assertEqual(len(first_page["worldlines"]), 1)
        self.assertIsNotNone(first_page["next_cursor"])

        second_page = self._run(
            threads.get_thread_worldlines(
                thread_id=thread_id, limit=10, cursor=first_page["next_cursor"]
            )
        )
        self.assertEqual(len(second_page["worldlines"]), 1)
        self.assertIsNone(second_page["next_cursor"])

        fetched_ids = {
            first_page["worldlines"][0]["id"],
            second_page["worldlines"][0]["id"],
        }
        self.assertEqual(fetched_ids, {worldline_a, worldline_b})

    def test_get_thread_worldlines_not_found(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            self._run(
                threads.get_thread_worldlines(
                    thread_id="thread_missing", limit=50, cursor=None
                )
            )
        self.assertEqual(ctx.exception.status_code, 404)

    def test_get_thread_worldline_summaries_includes_message_and_job_stats(
        self,
    ) -> None:
        thread_id = self._create_thread()
        worldline_a = self._create_worldline(thread_id, "main")
        worldline_b = self._create_worldline(thread_id, "branch-a")

        with meta.get_conn() as conn:
            conn.execute(
                """
                INSERT INTO events (id, worldline_id, parent_event_id, type, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "event_a_1",
                    worldline_a,
                    None,
                    "user_message",
                    '{"text":"a1"}',
                    "2026-01-01 10:00:00",
                ),
            )
            conn.execute(
                """
                INSERT INTO events (id, worldline_id, parent_event_id, type, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "event_a_2",
                    worldline_a,
                    "event_a_1",
                    "assistant_message",
                    '{"text":"a2"}',
                    "2026-01-01 10:01:00",
                ),
            )
            conn.execute(
                """
                INSERT INTO events (id, worldline_id, parent_event_id, type, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "event_b_1",
                    worldline_b,
                    None,
                    "assistant_message",
                    '{"text":"b1"}',
                    "2026-01-01 09:00:00",
                ),
            )

            conn.execute(
                """
                INSERT INTO chat_turn_jobs (
                    id,
                    thread_id,
                    worldline_id,
                    request_json,
                    status,
                    created_at,
                    started_at,
                    finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "job_a_1",
                    thread_id,
                    worldline_a,
                    '{"message":"older"}',
                    "completed",
                    "2026-01-01 10:00:00",
                    "2026-01-01 10:00:05",
                    "2026-01-01 10:00:20",
                ),
            )
            conn.execute(
                """
                INSERT INTO chat_turn_jobs (
                    id,
                    thread_id,
                    worldline_id,
                    request_json,
                    status,
                    created_at,
                    started_at,
                    finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "job_a_2",
                    thread_id,
                    worldline_a,
                    '{"message":"newer"}',
                    "running",
                    "2026-01-01 10:02:00",
                    "2026-01-01 10:02:10",
                    None,
                ),
            )
            conn.execute(
                """
                INSERT INTO chat_turn_jobs (
                    id,
                    thread_id,
                    worldline_id,
                    request_json,
                    status,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "job_b_1",
                    thread_id,
                    worldline_b,
                    '{"message":"queued"}',
                    "queued",
                    "2026-01-01 09:30:00",
                ),
            )
            conn.commit()

        response = self._run(
            threads.get_thread_worldline_summaries(
                thread_id=thread_id,
                limit=10,
                cursor=None,
            )
        )

        self.assertEqual(len(response["worldlines"]), 2)
        self.assertIsNone(response["next_cursor"])

        by_id = {row["id"]: row for row in response["worldlines"]}
        summary_a = by_id[worldline_a]
        summary_b = by_id[worldline_b]

        self.assertEqual(summary_a["message_count"], 2)
        self.assertEqual(summary_a["jobs"]["running"], 1)
        self.assertEqual(summary_a["jobs"]["completed"], 1)
        self.assertEqual(summary_a["jobs"]["queued"], 0)
        self.assertEqual(summary_a["jobs"]["latest_status"], "running")
        self.assertEqual(summary_a["last_activity"], "2026-01-01 10:02:10")

        self.assertEqual(summary_b["message_count"], 1)
        self.assertEqual(summary_b["jobs"]["queued"], 1)
        self.assertEqual(summary_b["jobs"]["running"], 0)
        self.assertEqual(summary_b["jobs"]["latest_status"], "queued")
        self.assertEqual(summary_b["last_activity"], "2026-01-01 09:30:00")

    def test_get_thread_worldline_summaries_not_found(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            self._run(
                threads.get_thread_worldline_summaries(
                    thread_id="thread_missing",
                    limit=50,
                    cursor=None,
                )
            )
        self.assertEqual(ctx.exception.status_code, 404)

    def test_list_threads_returns_last_activity_desc(self) -> None:
        older_thread_id = self._create_thread("older")
        newer_thread_id = self._create_thread("newer")
        older_worldline_id = self._create_worldline(older_thread_id, "main")
        newer_worldline_id = self._create_worldline(newer_thread_id, "main")

        with meta.get_conn() as conn:
            conn.execute(
                """
                INSERT INTO events (id, worldline_id, parent_event_id, type, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "event_old_1",
                    older_worldline_id,
                    None,
                    "user_message",
                    '{"text":"older"}',
                    "2024-01-01 00:00:00",
                ),
            )
            conn.execute(
                """
                INSERT INTO events (id, worldline_id, parent_event_id, type, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "event_new_1",
                    newer_worldline_id,
                    None,
                    "assistant_message",
                    '{"text":"newer"}',
                    "2025-01-01 00:00:00",
                ),
            )
            conn.commit()

        response = self._run(threads.list_threads(limit=10, cursor=None))
        self.assertEqual(response["threads"][0]["id"], newer_thread_id)
        self.assertEqual(response["threads"][1]["id"], older_thread_id)
        self.assertEqual(response["threads"][0]["message_count"], 1)
        self.assertEqual(response["threads"][1]["message_count"], 1)


if __name__ == "__main__":
    unittest.main()
