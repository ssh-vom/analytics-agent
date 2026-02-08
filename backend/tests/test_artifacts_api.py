import asyncio
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

import artifacts
import meta
import threads
import worldlines


class ArtifactsApiTests(unittest.TestCase):
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

    def _create_worldline(self) -> tuple[str, str]:
        thread_id = self._run(
            threads.create_thread(threads.CreateThreadRequest(title="artifacts-test-thread"))
        )["thread_id"]
        worldline_id = self._run(
            worldlines.create_worldline(
                worldlines.CreateWorldlineRequest(thread_id=thread_id, name="main")
            )
        )["worldline_id"]

        with meta.get_conn() as conn:
            event_id = meta.append_event(
                conn=conn,
                worldline_id=worldline_id,
                parent_event_id=None,
                event_type="tool_result_python",
                payload={"ok": True},
            )
            meta.set_worldline_head(conn, worldline_id, event_id)
            conn.commit()

        return worldline_id, event_id

    def test_get_artifact_returns_file_response(self) -> None:
        worldline_id, event_id = self._create_worldline()
        file_path = meta.DB_DIR / "worldlines" / worldline_id / "workspace" / "artifacts" / "note.md"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("hello", encoding="utf-8")
        artifact_id = meta.new_id("artifact")

        with meta.get_conn() as conn:
            conn.execute(
                """
                INSERT INTO artifacts (id, worldline_id, event_id, type, name, path)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (artifact_id, worldline_id, event_id, "md", "note.md", str(file_path)),
            )
            conn.commit()

        response = self._run(artifacts.get_artifact(artifact_id))
        self.assertIsInstance(response, FileResponse)
        self.assertEqual(response.path, str(file_path))

    def test_get_artifact_missing_id_returns_404(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            self._run(artifacts.get_artifact("artifact_missing"))

        self.assertEqual(ctx.exception.status_code, 404)
        self.assertIn("artifact not found", str(ctx.exception.detail))

    def test_get_artifact_missing_file_returns_404(self) -> None:
        worldline_id, event_id = self._create_worldline()
        missing_path = meta.DB_DIR / "worldlines" / worldline_id / "workspace" / "artifacts" / "gone.md"
        artifact_id = meta.new_id("artifact")

        with meta.get_conn() as conn:
            conn.execute(
                """
                INSERT INTO artifacts (id, worldline_id, event_id, type, name, path)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (artifact_id, worldline_id, event_id, "md", "gone.md", str(missing_path)),
            )
            conn.commit()

        with self.assertRaises(HTTPException) as ctx:
            self._run(artifacts.get_artifact(artifact_id))

        self.assertEqual(ctx.exception.status_code, 404)
        self.assertIn("artifact file not found", str(ctx.exception.detail))


if __name__ == "__main__":
    unittest.main()
