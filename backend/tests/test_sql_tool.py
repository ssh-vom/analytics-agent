import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import duckdb
from fastapi import HTTPException

import duckdb_manager
import meta
import seed_data
import threads
import tools
import worldlines


class SqlToolTests(unittest.TestCase):
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

    def _create_thread(self, title: str = "sql-tool-test-thread") -> str:
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

    def test_sql_tool_select_returns_shape_and_appends_events(self) -> None:
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)

        result = self._run(
            tools.run_sql(
                tools.SqlToolRequest(
                    worldline_id=worldline_id,
                    sql="SELECT 1 AS x",
                    limit=100,
                )
            )
        )

        self.assertEqual([column["name"] for column in result["columns"]], ["x"])
        self.assertEqual(result["rows"], [[1]])
        self.assertEqual(result["row_count"], 1)
        self.assertEqual(result["preview_count"], 1)
        self.assertIn("execution_ms", result)

        with meta.get_conn() as conn:
            event_rows = conn.execute(
                """
                SELECT id, type, payload_json
                FROM events
                WHERE worldline_id = ?
                ORDER BY rowid
                """,
                (worldline_id,),
            ).fetchall()
            worldline_row = conn.execute(
                "SELECT head_event_id FROM worldlines WHERE id = ?",
                (worldline_id,),
            ).fetchone()

        self.assertEqual(
            [row["type"] for row in event_rows], ["tool_call_sql", "tool_result_sql"]
        )
        self.assertEqual(worldline_row["head_event_id"], event_rows[-1]["id"])
        self.assertEqual(
            json.loads(event_rows[0]["payload_json"])["sql"],
            "SELECT 1 AS x",
        )

    def test_sql_tool_blocks_mutating_statements(self) -> None:
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)

        with self.assertRaises(HTTPException) as ctx:
            self._run(
                tools.run_sql(
                    tools.SqlToolRequest(
                        worldline_id=worldline_id,
                        sql="DELETE FROM anything",
                        limit=100,
                    )
                )
            )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("read-only", str(ctx.exception.detail).lower())

        with meta.get_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS count FROM events WHERE worldline_id = ?",
                (worldline_id,),
            ).fetchone()["count"]
        self.assertEqual(count, 0)

    def test_worldline_duckdb_files_are_isolated(self) -> None:
        thread_id = self._create_thread()
        worldline_1 = self._create_worldline(thread_id, "main")
        worldline_2 = self._create_worldline(thread_id, "alt")

        path_1 = duckdb_manager.ensure_worldline_db(worldline_1)
        path_2 = duckdb_manager.ensure_worldline_db(worldline_2)

        # Seed each worldline database with different data.
        conn1 = duckdb.connect(str(path_1))
        conn1.execute("CREATE TABLE items (value INTEGER)")
        conn1.execute("INSERT INTO items VALUES (10)")
        conn1.close()

        conn2 = duckdb.connect(str(path_2))
        conn2.execute("CREATE TABLE items (value INTEGER)")
        conn2.execute("INSERT INTO items VALUES (99)")
        conn2.close()

        result_1 = self._run(
            tools.run_sql(
                tools.SqlToolRequest(
                    worldline_id=worldline_1, sql="SELECT value FROM items", limit=10
                )
            )
        )
        result_2 = self._run(
            tools.run_sql(
                tools.SqlToolRequest(
                    worldline_id=worldline_2, sql="SELECT value FROM items", limit=10
                )
            )
        )

        self.assertEqual(result_1["rows"], [[10]])
        self.assertEqual(result_2["rows"], [[99]])
        self.assertTrue(str(path_1).startswith(str(meta.DB_DIR)))
        self.assertTrue(str(path_2).startswith(str(meta.DB_DIR)))
        self.assertNotEqual(path_1, path_2)

    def test_sql_tool_queries_attached_duckdb_across_requests(self) -> None:
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)
        external_db_path = Path(self.temp_dir.name) / "external_finance.duckdb"

        conn = duckdb.connect(str(external_db_path))
        try:
            conn.execute(
                """
                CREATE TABLE finance_daily (
                    trading_date DATE,
                    revenue DOUBLE
                )
                """
            )
            conn.execute(
                """
                INSERT INTO finance_daily VALUES
                    ('2026-01-01', 1000.0),
                    ('2026-01-02', 1200.5)
                """
            )
        finally:
            conn.close()

        attached = seed_data.attach_external_duckdb(
            worldline_id,
            db_path=str(external_db_path),
            alias="warehouse",
        )
        self.assertEqual(attached.alias, "warehouse")

        with self.assertRaises(HTTPException) as blocked_ctx:
            self._run(
                tools.run_sql(
                    tools.SqlToolRequest(
                        worldline_id=worldline_id,
                        sql="SELECT revenue FROM warehouse.finance_daily ORDER BY trading_date",
                        limit=10,
                        allowed_external_aliases=[],
                    )
                )
            )
        self.assertEqual(blocked_ctx.exception.status_code, 400)
        self.assertIn("warehouse", str(blocked_ctx.exception.detail))

        first_result = self._run(
            tools.run_sql(
                tools.SqlToolRequest(
                    worldline_id=worldline_id,
                    sql="SELECT revenue FROM warehouse.finance_daily ORDER BY trading_date",
                    limit=10,
                    allowed_external_aliases=["warehouse"],
                )
            )
        )
        second_result = self._run(
            tools.run_sql(
                tools.SqlToolRequest(
                    worldline_id=worldline_id,
                    sql="SELECT SUM(revenue) AS total_revenue FROM warehouse.finance_daily",
                    limit=10,
                )
            )
        )

        self.assertEqual(first_result["rows"], [[1000.0], [1200.5]])
        self.assertEqual(second_result["rows"], [[2200.5]])

        detached = seed_data.detach_external_duckdb(worldline_id, "warehouse")
        self.assertEqual(detached["status"], "detached")

        with self.assertRaises(HTTPException) as ctx:
            self._run(
                tools.run_sql(
                    tools.SqlToolRequest(
                        worldline_id=worldline_id,
                        sql="SELECT COUNT(*) FROM warehouse.finance_daily",
                        limit=10,
                    )
                )
            )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("warehouse", str(ctx.exception.detail))


if __name__ == "__main__":
    unittest.main()
