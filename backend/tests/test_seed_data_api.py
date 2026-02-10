import asyncio
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException, UploadFile

import meta
import seed_data_api
import threads
import worldlines


class SeedDataApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        temp_root = Path(self.temp_dir.name)

        meta.DB_DIR = temp_root / "data"
        meta.DB_PATH = meta.DB_DIR / "meta.db"
        meta.init_meta_db()

        self.temp_upload_dir = meta.DB_DIR / "temp_uploads"
        self.temp_upload_patch = patch.object(
            seed_data_api,
            "TEMP_UPLOAD_DIR",
            self.temp_upload_dir,
        )
        self.temp_upload_patch.start()

    def tearDown(self) -> None:
        self.temp_upload_patch.stop()
        self.temp_dir.cleanup()

    def _run(self, coro):
        return asyncio.run(coro)

    def _create_worldline(self) -> str:
        thread_id = self._run(
            threads.create_thread(
                threads.CreateThreadRequest(title="seed-data-api-test-thread")
            )
        )["thread_id"]
        worldline_id = self._run(
            worldlines.create_worldline(
                worldlines.CreateWorldlineRequest(thread_id=thread_id, name="main")
            )
        )["worldline_id"]
        return worldline_id

    def test_import_csv_rejects_oversized_upload_with_413(self) -> None:
        worldline_id = self._create_worldline()
        upload = UploadFile(
            io.BytesIO(b"a,b\n1,2\n3,4\n5,6\n"),
            filename="too_big.csv",
        )

        with patch.object(seed_data_api, "MAX_CSV_FILE_SIZE", 12):
            with self.assertRaises(HTTPException) as ctx:
                self._run(
                    seed_data_api.import_csv_endpoint(
                        worldline_id=worldline_id, file=upload
                    )
                )

        self.assertEqual(ctx.exception.status_code, 413)
        self.assertIn("File too large", str(ctx.exception.detail))

        with meta.get_conn() as conn:
            event_count = conn.execute(
                "SELECT COUNT(*) AS count FROM events WHERE worldline_id = ?",
                (worldline_id,),
            ).fetchone()["count"]
        self.assertEqual(event_count, 0)

        temp_files = list(self.temp_upload_dir.glob("*"))
        self.assertEqual(temp_files, [])

    def test_import_csv_appends_event_for_small_upload(self) -> None:
        worldline_id = self._create_worldline()
        upload = UploadFile(
            io.BytesIO(b"name,score\nAva,10\nLiam,12\n"),
            filename="scores.csv",
        )

        with patch.object(seed_data_api, "MAX_CSV_FILE_SIZE", 1024):
            result = self._run(
                seed_data_api.import_csv_endpoint(
                    worldline_id=worldline_id,
                    file=upload,
                    table_name="scores_table",
                    if_exists="fail",
                )
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["row_count"], 2)
        self.assertEqual(result["table_name"], "scores_table")

        with meta.get_conn() as conn:
            event_row = conn.execute(
                """
                SELECT id, type, payload_json
                FROM events
                WHERE worldline_id = ?
                ORDER BY rowid DESC
                LIMIT 1
                """,
                (worldline_id,),
            ).fetchone()
            worldline_row = conn.execute(
                "SELECT head_event_id FROM worldlines WHERE id = ?",
                (worldline_id,),
            ).fetchone()

        self.assertEqual(event_row["id"], result["event_id"])
        self.assertEqual(event_row["type"], "csv_import")
        self.assertEqual(worldline_row["head_event_id"], result["event_id"])

        temp_files = list(self.temp_upload_dir.glob("*"))
        self.assertEqual(temp_files, [])


if __name__ == "__main__":
    unittest.main()
