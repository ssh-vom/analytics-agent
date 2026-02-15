"""Tests for artifact merging from child worldlines to parent."""

import tempfile
import unittest
from pathlib import Path

import meta
from chat.artifact_merger import _normalize_label, copy_artifacts_to_parent
from chat.subagents import _artifacts_from_child_events


class ArtifactMergerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        temp_root = Path(self.temp_dir.name)
        meta.DB_DIR = temp_root / "data"
        meta.DB_PATH = meta.DB_DIR / "meta.db"
        meta.init_meta_db()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_normalize_label_basic(self) -> None:
        self.assertEqual(_normalize_label("region-west"), "region-west")
        self.assertEqual(_normalize_label("Region West"), "region-west")
        self.assertEqual(_normalize_label("  Task 1  "), "task-1")
        self.assertEqual(_normalize_label(""), "")

    def test_normalize_label_special_chars(self) -> None:
        self.assertEqual(_normalize_label("task/with/slashes"), "task-with-slashes")
        self.assertEqual(_normalize_label("task...dots"), "task-dots")
        self.assertEqual(_normalize_label("task@#$special"), "task-special")

    def test_normalize_label_truncation(self) -> None:
        long_label = "a" * 50
        result = _normalize_label(long_label)
        self.assertEqual(len(result), 30)

    def test_artifacts_from_child_events_empty(self) -> None:
        events: list[dict] = []
        result = _artifacts_from_child_events(events)
        self.assertEqual(result, [])

    def test_artifacts_from_child_events_no_python_results(self) -> None:
        events = [
            {"type": "user_message", "payload": {"text": "hello"}},
            {"type": "assistant_message", "payload": {"text": "hi"}},
        ]
        result = _artifacts_from_child_events(events)
        self.assertEqual(result, [])

    def test_artifacts_from_child_events_extracts_artifacts(self) -> None:
        events = [
            {"type": "tool_call_python", "payload": {"code": "print('hi')"}},
            {
                "type": "tool_result_python",
                "payload": {
                    "stdout": "hi\n",
                    "artifacts": [
                        {
                            "artifact_id": "art_123",
                            "name": "chart.png",
                            "type": "image",
                            "path": "/workspace/chart.png",
                        },
                        {
                            "artifact_id": "art_456",
                            "name": "data.csv",
                            "type": "csv",
                            "path": "/workspace/data.csv",
                        },
                    ],
                },
            },
        ]
        result = _artifacts_from_child_events(events)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["name"], "chart.png")
        self.assertEqual(result[0]["type"], "image")
        self.assertEqual(result[1]["name"], "data.csv")

    def test_artifacts_from_child_events_skips_empty_names(self) -> None:
        events = [
            {
                "type": "tool_result_python",
                "payload": {
                    "artifacts": [
                        {"artifact_id": "art_1", "name": "", "type": "file"},
                        {"artifact_id": "art_2", "name": "valid.txt", "type": "file"},
                    ],
                },
            },
        ]
        result = _artifacts_from_child_events(events)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "valid.txt")

    def _create_worldline_and_event(self, worldline_id: str) -> str:
        """Helper to create a worldline and a dummy event for FK constraints."""
        thread_id = meta.new_id("thread")
        event_id = meta.new_id("event")
        with meta.get_conn() as conn:
            conn.execute(
                "INSERT INTO threads (id, title) VALUES (?, ?)",
                (thread_id, "test-thread"),
            )
            conn.execute(
                "INSERT INTO worldlines (id, thread_id, name) VALUES (?, ?, ?)",
                (worldline_id, thread_id, "test-worldline"),
            )
            conn.execute(
                "INSERT INTO events (id, worldline_id, type, payload_json) VALUES (?, ?, ?, ?)",
                (event_id, worldline_id, "user_message", '{"text": "test"}'),
            )
            conn.commit()
        return event_id

    def test_copy_artifacts_to_parent_creates_prefixed_files(self) -> None:
        # Setup: create source and target worldlines
        source_wid = "child_worldline_123"
        target_wid = "parent_worldline_456"

        # Create worldlines and events for FK constraints
        self._create_worldline_and_event(source_wid)
        target_event_id = self._create_worldline_and_event(target_wid)

        source_workspace = meta.DB_DIR / "worldlines" / source_wid / "workspace"
        source_workspace.mkdir(parents=True, exist_ok=True)

        # Create a source artifact file
        source_file = source_workspace / "chart.png"
        source_file.write_bytes(b"fake png content")

        artifacts = [
            {
                "artifact_id": "art_original",
                "name": "chart.png",
                "type": "image",
                "path": str(source_file),
            }
        ]

        result = copy_artifacts_to_parent(
            source_worldline_id=source_wid,
            target_worldline_id=target_wid,
            artifacts=artifacts,
            task_label="Region West",
            task_index=0,
            target_event_id=target_event_id,
        )

        # Verify file was copied with prefix
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "region-west_chart.png")
        self.assertEqual(result[0]["type"], "image")
        self.assertEqual(result[0]["source_name"], "chart.png")
        self.assertEqual(result[0]["task_label"], "Region West")

        # Verify file exists in target workspace
        target_workspace = meta.DB_DIR / "worldlines" / target_wid / "workspace"
        target_file = target_workspace / "region-west_chart.png"
        self.assertTrue(target_file.exists())
        self.assertEqual(target_file.read_bytes(), b"fake png content")

        # Verify artifact was registered in DB
        with meta.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM artifacts WHERE id = ?", (result[0]["artifact_id"],)
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["worldline_id"], target_wid)
            self.assertEqual(row["name"], "region-west_chart.png")

    def test_copy_artifacts_uses_task_index_when_no_label(self) -> None:
        source_wid = "child_worldline_nolabel"
        target_wid = "parent_worldline_nolabel"

        # Create worldlines and events for FK constraints
        self._create_worldline_and_event(source_wid)
        target_event_id = self._create_worldline_and_event(target_wid)

        source_workspace = meta.DB_DIR / "worldlines" / source_wid / "workspace"
        source_workspace.mkdir(parents=True, exist_ok=True)

        source_file = source_workspace / "output.csv"
        source_file.write_text("col1,col2\n1,2")

        artifacts = [{"name": "output.csv", "type": "csv", "path": str(source_file)}]

        result = copy_artifacts_to_parent(
            source_worldline_id=source_wid,
            target_worldline_id=target_wid,
            artifacts=artifacts,
            task_label="",  # Empty label
            task_index=3,
            target_event_id=target_event_id,
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "task-3_output.csv")

    def test_copy_artifacts_skips_missing_files(self) -> None:
        source_wid = "child_missing"
        target_wid = "parent_missing"

        artifacts = [
            {
                "name": "nonexistent.png",
                "type": "image",
                "path": "/does/not/exist/file.png",
            }
        ]

        result = copy_artifacts_to_parent(
            source_worldline_id=source_wid,
            target_worldline_id=target_wid,
            artifacts=artifacts,
            task_label="test",
            task_index=0,
            target_event_id="evt_789",
        )

        self.assertEqual(len(result), 0)

    def test_copy_artifacts_empty_list(self) -> None:
        result = copy_artifacts_to_parent(
            source_worldline_id="src",
            target_worldline_id="tgt",
            artifacts=[],
            task_label="test",
            task_index=0,
            target_event_id="evt_000",
        )
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
