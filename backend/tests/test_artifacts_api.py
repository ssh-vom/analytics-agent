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
            threads.create_thread(
                threads.CreateThreadRequest(title="artifacts-test-thread")
            )
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

    def _insert_artifact(
        self,
        *,
        worldline_id: str,
        event_id: str,
        artifact_type: str,
        name: str,
        path: Path,
    ) -> str:
        artifact_id = meta.new_id("artifact")
        with meta.get_conn() as conn:
            conn.execute(
                """
                INSERT INTO artifacts (id, worldline_id, event_id, type, name, path)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (artifact_id, worldline_id, event_id, artifact_type, name, str(path)),
            )
            conn.commit()
        return artifact_id

    def test_get_artifact_returns_file_response(self) -> None:
        worldline_id, event_id = self._create_worldline()
        file_path = (
            meta.DB_DIR
            / "worldlines"
            / worldline_id
            / "workspace"
            / "artifacts"
            / "note.md"
        )
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("hello", encoding="utf-8")
        artifact_id = self._insert_artifact(
            worldline_id=worldline_id,
            event_id=event_id,
            artifact_type="md",
            name="note.md",
            path=file_path,
        )

        response = self._run(artifacts.get_artifact(artifact_id))
        self.assertIsInstance(response, FileResponse)
        self.assertEqual(Path(response.path).resolve(), file_path.resolve())

    def test_get_artifact_allows_workspace_root_file(self) -> None:
        worldline_id, event_id = self._create_worldline()
        file_path = (
            meta.DB_DIR / "worldlines" / worldline_id / "workspace" / "report.csv"
        )
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("x\n1\n", encoding="utf-8")
        artifact_id = self._insert_artifact(
            worldline_id=worldline_id,
            event_id=event_id,
            artifact_type="csv",
            name="report.csv",
            path=file_path,
        )

        response = self._run(artifacts.get_artifact(artifact_id))
        self.assertIsInstance(response, FileResponse)
        self.assertEqual(Path(response.path).resolve(), file_path.resolve())

    def test_get_artifact_missing_id_returns_404(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            self._run(artifacts.get_artifact("artifact_missing"))

        self.assertEqual(ctx.exception.status_code, 404)
        self.assertIn("artifact not found", str(ctx.exception.detail))

    def test_get_artifact_missing_file_returns_404(self) -> None:
        worldline_id, event_id = self._create_worldline()
        missing_path = (
            meta.DB_DIR
            / "worldlines"
            / worldline_id
            / "workspace"
            / "artifacts"
            / "gone.md"
        )
        artifact_id = self._insert_artifact(
            worldline_id=worldline_id,
            event_id=event_id,
            artifact_type="md",
            name="gone.md",
            path=missing_path,
        )

        with self.assertRaises(HTTPException) as ctx:
            self._run(artifacts.get_artifact(artifact_id))

        self.assertEqual(ctx.exception.status_code, 404)
        self.assertIn("artifact file not found", str(ctx.exception.detail))

    def test_get_artifact_rejects_path_outside_worldline_workspace(self) -> None:
        worldline_id, event_id = self._create_worldline()
        outside_file = Path(self.temp_dir.name) / "outside.txt"
        outside_file.write_text("sensitive", encoding="utf-8")
        artifact_id = self._insert_artifact(
            worldline_id=worldline_id,
            event_id=event_id,
            artifact_type="txt",
            name="outside.txt",
            path=outside_file,
        )

        with self.assertRaises(HTTPException) as ctx:
            self._run(artifacts.get_artifact(artifact_id))

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIn("artifact path not allowed", str(ctx.exception.detail))

    def test_preview_artifact_returns_table_preview_and_truncation(self) -> None:
        worldline_id, event_id = self._create_worldline()
        csv_path = (
            meta.DB_DIR
            / "worldlines"
            / worldline_id
            / "workspace"
            / "risk_analysis.csv"
        )
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        csv_path.write_text(
            "provider,risk,revenue\n"
            "Nguyen,CRITICAL,36830\n"
            "Labsolutions,CRITICAL,4619\n"
            "Inova,MEDIUM,1088\n",
            encoding="utf-8",
        )
        artifact_id = self._insert_artifact(
            worldline_id=worldline_id,
            event_id=event_id,
            artifact_type="csv",
            name="risk_analysis.csv",
            path=csv_path,
        )

        response = self._run(artifacts.preview_artifact(artifact_id, limit=2))

        self.assertEqual(response["artifact_id"], artifact_id)
        self.assertEqual(response["preview"]["format"], "table")
        self.assertEqual(
            response["preview"]["columns"], ["provider", "risk", "revenue"]
        )
        self.assertEqual(response["preview"]["row_count"], 3)
        self.assertEqual(response["preview"]["preview_count"], 2)
        self.assertTrue(response["preview"]["truncated"])
        self.assertEqual(
            response["preview"]["rows"][0], ["Nguyen", "CRITICAL", "36830"]
        )

    def test_preview_artifact_rejects_non_csv(self) -> None:
        worldline_id, event_id = self._create_worldline()
        file_path = (
            meta.DB_DIR
            / "worldlines"
            / worldline_id
            / "workspace"
            / "artifacts"
            / "note.md"
        )
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("not a csv", encoding="utf-8")
        artifact_id = self._insert_artifact(
            worldline_id=worldline_id,
            event_id=event_id,
            artifact_type="md",
            name="note.md",
            path=file_path,
        )

        with self.assertRaises(HTTPException) as ctx:
            self._run(artifacts.preview_artifact(artifact_id, limit=10))

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn(
            "Table preview is only available for CSV artifacts",
            str(ctx.exception.detail),
        )


if __name__ == "__main__":
    unittest.main()
