import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

import meta
import threads
import tools
import worldlines


class FakeSandboxManager:
    def __init__(self, *, result: dict | None = None, error: str | None = None) -> None:
        self._result = result or {
            "stdout": "",
            "stderr": "",
            "error": None,
            "artifacts": [],
            "previews": {"dataframes": []},
        }
        self._error = error
        self.calls: list[tuple[str, str, int]] = []

    async def execute(self, worldline_id: str, code: str, timeout_s: int) -> dict:
        self.calls.append((worldline_id, code, timeout_s))
        if self._error is not None:
            raise RuntimeError(self._error)
        return dict(self._result)


class PythonToolTests(unittest.TestCase):
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

    def _create_thread(self, title: str = "python-tool-test-thread") -> str:
        response = self._run(threads.create_thread(threads.CreateThreadRequest(title=title)))
        return response["thread_id"]

    def _create_worldline(self, thread_id: str, name: str = "main") -> str:
        response = self._run(
            worldlines.create_worldline(
                worldlines.CreateWorldlineRequest(thread_id=thread_id, name=name)
            )
        )
        return response["worldline_id"]

    def test_python_tool_success_returns_result_and_appends_events(self) -> None:
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)
        artifact_path = (
            meta.DB_DIR
            / "worldlines"
            / worldline_id
            / "workspace"
            / "artifacts"
            / "result.md"
        )
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text("ok", encoding="utf-8")
        fake_manager = FakeSandboxManager(
            result={
                "stdout": "hello\n",
                "stderr": "",
                "error": None,
                "artifacts": [
                    {
                        "type": "md",
                        "name": "result.md",
                        "path": str(artifact_path),
                    }
                ],
                "previews": {"dataframes": []},
            }
        )

        with patch.object(tools, "_sandbox_manager", fake_manager):
            result = self._run(
                tools.run_python(
                    tools.PythonToolRequest(
                        worldline_id=worldline_id,
                        code="print('hello')",
                        timeout=15,
                    )
                )
            )

        self.assertEqual(fake_manager.calls, [(worldline_id, "print('hello')", 15)])
        self.assertEqual(result["stdout"], "hello\n")
        self.assertEqual(result["error"], None)
        self.assertIn("execution_ms", result)
        self.assertEqual(len(result["artifacts"]), 1)
        self.assertEqual(result["artifacts"][0]["type"], "md")
        self.assertEqual(result["artifacts"][0]["name"], "result.md")
        self.assertIn("artifact_id", result["artifacts"][0])
        self.assertNotIn("path", result["artifacts"][0])

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
            artifact_rows = conn.execute(
                """
                SELECT id, worldline_id, event_id, type, name, path
                FROM artifacts
                WHERE worldline_id = ?
                ORDER BY rowid
                """,
                (worldline_id,),
            ).fetchall()

        self.assertEqual(
            [row["type"] for row in event_rows],
            ["tool_call_python", "tool_result_python"],
        )
        self.assertEqual(
            json.loads(event_rows[0]["payload_json"]),
            {"code": "print('hello')", "timeout": 15},
        )
        self.assertEqual(worldline_row["head_event_id"], event_rows[-1]["id"])
        self.assertEqual(len(artifact_rows), 1)
        self.assertEqual(artifact_rows[0]["id"], result["artifacts"][0]["artifact_id"])
        self.assertEqual(artifact_rows[0]["worldline_id"], worldline_id)
        self.assertEqual(artifact_rows[0]["event_id"], event_rows[-1]["id"])
        self.assertEqual(artifact_rows[0]["type"], "md")
        self.assertEqual(artifact_rows[0]["name"], "result.md")
        self.assertEqual(artifact_rows[0]["path"], str(artifact_path))

    def test_python_tool_worldline_not_found(self) -> None:
        fake_manager = FakeSandboxManager()
        with patch.object(tools, "_sandbox_manager", fake_manager):
            with self.assertRaises(HTTPException) as ctx:
                self._run(
                    tools.run_python(
                        tools.PythonToolRequest(
                            worldline_id="worldline_missing",
                            code="print('x')",
                            timeout=10,
                        )
                    )
                )

        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(fake_manager.calls, [])

    def test_python_tool_runner_error_sets_result_event(self) -> None:
        thread_id = self._create_thread()
        worldline_id = self._create_worldline(thread_id)
        fake_manager = FakeSandboxManager(error="sandbox crashed")

        with patch.object(tools, "_sandbox_manager", fake_manager):
            with self.assertRaises(HTTPException) as ctx:
                self._run(
                    tools.run_python(
                        tools.PythonToolRequest(
                            worldline_id=worldline_id,
                            code="print('x')",
                            timeout=10,
                        )
                    )
                )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("sandbox crashed", str(ctx.exception.detail))

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
            [row["type"] for row in event_rows],
            ["tool_call_python", "tool_result_python"],
        )
        self.assertEqual(
            json.loads(event_rows[1]["payload_json"]),
            {"error": "sandbox crashed"},
        )
        self.assertEqual(worldline_row["head_event_id"], event_rows[-1]["id"])


if __name__ == "__main__":
    unittest.main()
