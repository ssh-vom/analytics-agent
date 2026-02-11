import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import meta
import threads
import worldlines
import duckdb
import duckdb_manager
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
            threads.create_thread(
                threads.CreateThreadRequest(title="worldline-service-test")
            )
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

        self.assertEqual(
            [row["type"] for row in rows],
            ["worldline_created", "time_travel", "user_message"],
        )
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

    def test_branch_clones_duckdb_state_from_source_worldline(self) -> None:
        thread_id = self._create_thread()
        source_worldline_id = self._create_worldline(thread_id)

        source_db_path = duckdb_manager.ensure_worldline_db(source_worldline_id)
        conn = duckdb.connect(str(source_db_path))
        conn.execute("CREATE TABLE metrics (k VARCHAR, v INTEGER)")
        conn.execute("INSERT INTO metrics VALUES ('a', 1), ('b', 2)")
        conn.close()

        with meta.get_conn() as sqlite_conn:
            sqlite_conn.execute(
                """
                INSERT INTO events (id, worldline_id, parent_event_id, type, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "event_anchor_3",
                    source_worldline_id,
                    None,
                    "assistant_message",
                    json.dumps({"text": "anchor-3"}),
                ),
            )
            sqlite_conn.execute(
                "UPDATE worldlines SET head_event_id = ? WHERE id = ?",
                ("event_anchor_3", source_worldline_id),
            )
            sqlite_conn.commit()

        service = WorldlineService()
        result = service.branch_from_event(
            BranchOptions(
                source_worldline_id=source_worldline_id,
                from_event_id="event_anchor_3",
                name="duckdb-clone",
                append_events=False,
            )
        )

        target_db_path = duckdb_manager.worldline_db_path(result.new_worldline_id)
        self.assertTrue(target_db_path.exists())

        target_conn = duckdb.connect(str(target_db_path))
        rows = target_conn.execute("SELECT k, v FROM metrics ORDER BY k").fetchall()
        target_conn.close()

        self.assertEqual(rows, [("a", 1), ("b", 2)])

    def test_branch_from_historical_event_uses_snapshot_state(self) -> None:
        thread_id = self._create_thread()
        source_worldline_id = self._create_worldline(thread_id)

        source_db_path = duckdb_manager.ensure_worldline_db(source_worldline_id)
        conn = duckdb.connect(str(source_db_path))
        conn.execute("CREATE TABLE metrics (k VARCHAR, v INTEGER)")
        conn.execute("INSERT INTO metrics VALUES ('a', 1)")
        conn.close()

        with meta.get_conn() as sqlite_conn:
            anchor_event_id = meta.append_event_and_advance_head(
                sqlite_conn,
                worldline_id=source_worldline_id,
                expected_head_event_id=None,
                event_type="assistant_message",
                payload={"text": "anchor"},
            )
            sqlite_conn.commit()

        snapshot_path = duckdb_manager.capture_worldline_snapshot(
            source_worldline_id,
            anchor_event_id,
        )
        with meta.get_conn() as sqlite_conn:
            sqlite_conn.execute(
                """
                INSERT INTO snapshots (id, worldline_id, event_id, duckdb_path)
                VALUES (?, ?, ?, ?)
                """,
                (
                    meta.new_id("snapshot"),
                    source_worldline_id,
                    anchor_event_id,
                    str(snapshot_path),
                ),
            )
            sqlite_conn.commit()

        conn = duckdb.connect(str(source_db_path))
        conn.execute("INSERT INTO metrics VALUES ('b', 2)")
        conn.close()

        with meta.get_conn() as sqlite_conn:
            meta.append_event_and_advance_head(
                sqlite_conn,
                worldline_id=source_worldline_id,
                expected_head_event_id=anchor_event_id,
                event_type="assistant_message",
                payload={"text": "after-snapshot"},
            )
            sqlite_conn.commit()

        service = WorldlineService()
        result = service.branch_from_event(
            BranchOptions(
                source_worldline_id=source_worldline_id,
                from_event_id=anchor_event_id,
                name="snapshot-branch",
                append_events=False,
            )
        )

        target_db_path = duckdb_manager.worldline_db_path(result.new_worldline_id)
        target_conn = duckdb.connect(str(target_db_path))
        rows = target_conn.execute("SELECT k, v FROM metrics ORDER BY k").fetchall()
        target_conn.close()

        self.assertEqual(rows, [("a", 1)])

    def test_branch_from_deep_fork_history_uses_ancestor_snapshot(self) -> None:
        thread_id = self._create_thread()
        root_worldline_id = self._create_worldline(thread_id)

        root_db = duckdb_manager.ensure_worldline_db(root_worldline_id)
        conn = duckdb.connect(str(root_db))
        conn.execute("CREATE TABLE metrics (k VARCHAR, v INTEGER)")
        conn.execute("INSERT INTO metrics VALUES ('root', 1)")
        conn.close()

        with meta.get_conn() as sqlite_conn:
            root_anchor_event_id = meta.append_event_and_advance_head(
                sqlite_conn,
                worldline_id=root_worldline_id,
                expected_head_event_id=None,
                event_type="assistant_message",
                payload={"text": "root-anchor"},
            )
            sqlite_conn.commit()

        root_snapshot = duckdb_manager.capture_worldline_snapshot(
            root_worldline_id,
            root_anchor_event_id,
        )
        with meta.get_conn() as sqlite_conn:
            sqlite_conn.execute(
                """
                INSERT INTO snapshots (id, worldline_id, event_id, duckdb_path)
                VALUES (?, ?, ?, ?)
                """,
                (
                    meta.new_id("snapshot"),
                    root_worldline_id,
                    root_anchor_event_id,
                    str(root_snapshot),
                ),
            )
            sqlite_conn.commit()

        service = WorldlineService()
        branch_1 = service.branch_from_event(
            BranchOptions(
                source_worldline_id=root_worldline_id,
                from_event_id=root_anchor_event_id,
                name="branch-1",
                append_events=False,
            )
        )

        branch_1_db = duckdb_manager.worldline_db_path(branch_1.new_worldline_id)
        conn = duckdb.connect(str(branch_1_db))
        conn.execute("INSERT INTO metrics VALUES ('branch1', 2)")
        conn.close()

        with meta.get_conn() as sqlite_conn:
            branch_1_anchor_event_id = meta.append_event_and_advance_head(
                sqlite_conn,
                worldline_id=branch_1.new_worldline_id,
                expected_head_event_id=branch_1.created_event_ids[0],
                event_type="assistant_message",
                payload={"text": "branch-1-anchor"},
            )
            sqlite_conn.commit()

        branch_2 = service.branch_from_event(
            BranchOptions(
                source_worldline_id=branch_1.new_worldline_id,
                from_event_id=branch_1_anchor_event_id,
                name="branch-2",
                append_events=False,
            )
        )

        branch_2_db = duckdb_manager.worldline_db_path(branch_2.new_worldline_id)
        conn = duckdb.connect(str(branch_2_db))
        conn.execute("INSERT INTO metrics VALUES ('branch2', 3)")
        conn.close()

        with meta.get_conn() as sqlite_conn:
            branch_2_anchor_event_id = meta.append_event_and_advance_head(
                sqlite_conn,
                worldline_id=branch_2.new_worldline_id,
                expected_head_event_id=branch_2.created_event_ids[0],
                event_type="assistant_message",
                payload={"text": "branch-2-anchor"},
            )
            sqlite_conn.commit()

        branch_3 = service.branch_from_event(
            BranchOptions(
                source_worldline_id=branch_2.new_worldline_id,
                from_event_id=root_anchor_event_id,
                name="branch-3-from-root",
                append_events=False,
            )
        )

        branch_3_db = duckdb_manager.worldline_db_path(branch_3.new_worldline_id)
        conn = duckdb.connect(str(branch_3_db))
        rows = conn.execute("SELECT k, v FROM metrics ORDER BY k").fetchall()
        conn.close()

        self.assertEqual(rows, [("root", 1)])


if __name__ == "__main__":
    unittest.main()
