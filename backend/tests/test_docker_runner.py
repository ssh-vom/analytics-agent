import asyncio
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import meta
from sandbox.docker_runner import DockerSandboxRunner


class DockerRunnerTests(unittest.TestCase):
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

    def test_build_start_command_has_security_flags(self) -> None:
        runner = DockerSandboxRunner()
        workspace = Path("/tmp/workspace")
        cmd = runner._build_start_command("sb_1", workspace)

        self.assertIn("--network", cmd)
        self.assertEqual(cmd[cmd.index("--network") + 1], "none")
        self.assertIn("--cap-drop", cmd)
        self.assertEqual(cmd[cmd.index("--cap-drop") + 1], "ALL")
        self.assertIn("--security-opt", cmd)
        self.assertEqual(cmd[cmd.index("--security-opt") + 1], "no-new-privileges")
        self.assertIn("--read-only", cmd)
        self.assertIn("--pids-limit", cmd)
        self.assertIn("--memory", cmd)
        self.assertIn("--cpus", cmd)
        self.assertIn("--user", cmd)
        self.assertEqual(cmd[cmd.index("--user") + 1], "1000:1000")
        self.assertIn("-w", cmd)
        self.assertEqual(cmd[cmd.index("-w") + 1], "/workspace")
        self.assertIn("--name", cmd)
        self.assertEqual(cmd[cmd.index("--name") + 1], "sb_1")
        self.assertIn("-d", cmd)
        self.assertIn("MPLCONFIGDIR=/tmp/matplotlib", cmd)

    def test_default_image_is_textql_sandbox(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            runner = DockerSandboxRunner()
        self.assertEqual(runner.image, "textql-sandbox:py311")

    def test_image_can_be_overridden_by_env(self) -> None:
        with patch.dict(
            os.environ, {"SANDBOX_IMAGE": "custom/sandbox:latest"}, clear=True
        ):
            runner = DockerSandboxRunner()
        self.assertEqual(runner.image, "custom/sandbox:latest")

    def test_execute_success_returns_stdout_and_artifacts(self) -> None:
        runner = DockerSandboxRunner()
        workspace = runner._workspace_dir("w_success")
        artifacts_dir = workspace / "artifacts"

        def fake_run(*args, **kwargs):
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            (artifacts_dir / "result.md").write_text("ok", encoding="utf-8")
            return subprocess.CompletedProcess(
                args=args[0], returncode=0, stdout="ok\n", stderr=""
            )

        with (
            patch("sandbox.docker_runner.shutil.which", return_value="/usr/bin/docker"),
            patch("sandbox.docker_runner.subprocess.run", side_effect=fake_run),
        ):
            out = self._run(
                runner.execute(
                    sandbox_id="sb_w_success",
                    worldline_id="w_success",
                    code="print('ok')",
                    timeout_s=5,
                )
            )

        self.assertEqual(out["stdout"], "ok\n")
        self.assertEqual(out["error"], None)
        self.assertIn("result.md", [artifact["name"] for artifact in out["artifacts"]])
        self.assertIn(
            "artifacts",
            [Path(artifact["path"]).parent.name for artifact in out["artifacts"]],
        )
        self.assertFalse((workspace / ".runner_input.py").exists())

    def test_execute_discovers_artifacts_saved_in_workspace_root(self) -> None:
        runner = DockerSandboxRunner()
        workspace = runner._workspace_dir("w_root_artifact")

        def fake_run(*args, **kwargs):
            workspace.mkdir(parents=True, exist_ok=True)
            (workspace / "line_2x.png").write_bytes(b"png")
            return subprocess.CompletedProcess(
                args=args[0], returncode=0, stdout="", stderr=""
            )

        with (
            patch("sandbox.docker_runner.shutil.which", return_value="/usr/bin/docker"),
            patch("sandbox.docker_runner.subprocess.run", side_effect=fake_run),
        ):
            out = self._run(
                runner.execute(
                    sandbox_id="sb_w_root_artifact",
                    worldline_id="w_root_artifact",
                    code="print('ok')",
                    timeout_s=5,
                )
            )

        names = [artifact["name"] for artifact in out["artifacts"]]
        self.assertIn("line_2x.png", names)
        found = next(
            artifact
            for artifact in out["artifacts"]
            if artifact["name"] == "line_2x.png"
        )
        self.assertEqual(found["type"], "image")

    def test_execute_nonzero_sets_error(self) -> None:
        runner = DockerSandboxRunner()
        with (
            patch("sandbox.docker_runner.shutil.which", return_value="/usr/bin/docker"),
            patch(
                "sandbox.docker_runner.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=["docker", "run"],
                    returncode=1,
                    stdout="",
                    stderr="boom",
                ),
            ),
        ):
            out = self._run(
                runner.execute(
                    sandbox_id="sb_w_nonzero",
                    worldline_id="w_nonzero",
                    code="print('x')",
                    timeout_s=5,
                )
            )

        self.assertEqual(out["stdout"], "")
        self.assertEqual(out["stderr"], "boom")
        self.assertIn("boom", out["error"])

    def test_execute_timeout_returns_timeout_error(self) -> None:
        runner = DockerSandboxRunner()
        timeout_exc = subprocess.TimeoutExpired(
            cmd=["docker", "run"],
            timeout=5,
            output="partial out",
            stderr="partial err",
        )
        call_count = 0

        def mock_run(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            # First call is the main execution (times out)
            # Subsequent calls are cleanup (pkill commands) - return success
            if call_count == 1:
                raise timeout_exc
            return subprocess.CompletedProcess(
                args=args[0] if args else [], returncode=0, stdout="", stderr=""
            )

        with (
            patch("sandbox.docker_runner.shutil.which", return_value="/usr/bin/docker"),
            patch("sandbox.docker_runner.subprocess.run", side_effect=mock_run),
        ):
            out = self._run(
                runner.execute(
                    sandbox_id="sb_w_timeout",
                    worldline_id="w_timeout",
                    code="print('x')",
                    timeout_s=5,
                )
            )

        self.assertIn("timed out", out["error"])
        self.assertEqual(out["stdout"], "partial out")
        self.assertEqual(out["stderr"], "partial err")
        # Verify cleanup was attempted (main exec + 2 pkill calls)
        self.assertGreaterEqual(call_count, 3)

    def test_execute_without_docker_cli_returns_error(self) -> None:
        runner = DockerSandboxRunner()
        with patch("sandbox.docker_runner.shutil.which", return_value=None):
            out = self._run(
                runner.execute(
                    sandbox_id="sb_w_no_docker",
                    worldline_id="w_no_docker",
                    code="print('x')",
                    timeout_s=5,
                )
            )

        self.assertEqual(out["stdout"], "")
        self.assertEqual(out["stderr"], "")
        self.assertIn("docker CLI not found", out["error"])

    def test_start_without_docker_cli_raises(self) -> None:
        runner = DockerSandboxRunner()
        with patch("sandbox.docker_runner.shutil.which", return_value=None):
            with self.assertRaises(RuntimeError):
                self._run(runner.start("w_no_docker"))

    def test_execute_only_returns_new_or_modified_artifacts(self) -> None:
        """Artifacts from previous executions should not be re-reported."""
        runner = DockerSandboxRunner()
        workspace = runner._workspace_dir("w_dedup")
        artifacts_dir = workspace / "artifacts"
        workspace.mkdir(parents=True, exist_ok=True)
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        # Pre-existing file from a previous execution
        preexisting = artifacts_dir / "old_chart.png"
        preexisting.write_bytes(b"old png")

        import time

        time.sleep(0.01)  # Ensure mtime difference

        def fake_run(*args, **kwargs):
            # Only create a new file during this execution
            (artifacts_dir / "new_chart.png").write_bytes(b"new png")
            return subprocess.CompletedProcess(
                args=args[0], returncode=0, stdout="ok", stderr=""
            )

        with (
            patch("sandbox.docker_runner.shutil.which", return_value="/usr/bin/docker"),
            patch("sandbox.docker_runner.subprocess.run", side_effect=fake_run),
        ):
            out = self._run(
                runner.execute(
                    sandbox_id="sb_w_dedup",
                    worldline_id="w_dedup",
                    code="print('ok')",
                    timeout_s=5,
                )
            )

        artifact_names = [a["name"] for a in out["artifacts"]]
        # New artifact should be present
        self.assertIn("new_chart.png", artifact_names)
        # Pre-existing artifact should NOT be present
        self.assertNotIn("old_chart.png", artifact_names)

    def test_execute_returns_modified_artifacts(self) -> None:
        """If a pre-existing file is modified, it should be returned."""
        runner = DockerSandboxRunner()
        workspace = runner._workspace_dir("w_modified")
        artifacts_dir = workspace / "artifacts"
        workspace.mkdir(parents=True, exist_ok=True)
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        # Pre-existing file
        chart_file = artifacts_dir / "chart.png"
        chart_file.write_bytes(b"v1")

        import time

        time.sleep(0.01)  # Ensure mtime difference

        def fake_run(*args, **kwargs):
            # Modify the existing file
            time.sleep(0.01)
            chart_file.write_bytes(b"v2 modified")
            return subprocess.CompletedProcess(
                args=args[0], returncode=0, stdout="ok", stderr=""
            )

        with (
            patch("sandbox.docker_runner.shutil.which", return_value="/usr/bin/docker"),
            patch("sandbox.docker_runner.subprocess.run", side_effect=fake_run),
        ):
            out = self._run(
                runner.execute(
                    sandbox_id="sb_w_modified",
                    worldline_id="w_modified",
                    code="print('ok')",
                    timeout_s=5,
                )
            )

        artifact_names = [a["name"] for a in out["artifacts"]]
        # Modified file should be returned
        self.assertIn("chart.png", artifact_names)


if __name__ == "__main__":
    unittest.main()
