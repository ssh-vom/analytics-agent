import asyncio
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

    def test_build_command_has_security_flags(self) -> None:
        runner = DockerSandboxRunner()
        workspace = Path("/tmp/workspace")
        cmd = runner._build_command(workspace, timeout_s=30)

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

    def test_execute_success_returns_stdout_and_artifacts(self) -> None:
        runner = DockerSandboxRunner()
        workspace = runner._workspace_dir("w_success")
        artifacts_dir = workspace / "artifacts"

        def fake_run(*args, **kwargs):
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            (artifacts_dir / "result.md").write_text("ok", encoding="utf-8")
            return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="ok\n", stderr="")

        with patch("sandbox.docker_runner.shutil.which", return_value="/usr/bin/docker"), patch(
            "sandbox.docker_runner.subprocess.run", side_effect=fake_run
        ):
            out = self._run(runner.execute("w_success", "print('ok')", timeout_s=5))

        self.assertEqual(out["stdout"], "ok\n")
        self.assertEqual(out["error"], None)
        self.assertIn("result.md", [artifact["name"] for artifact in out["artifacts"]])
        self.assertFalse((workspace / ".runner_input.py").exists())

    def test_execute_nonzero_sets_error(self) -> None:
        runner = DockerSandboxRunner()
        with patch("sandbox.docker_runner.shutil.which", return_value="/usr/bin/docker"), patch(
            "sandbox.docker_runner.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=["docker", "run"],
                returncode=1,
                stdout="",
                stderr="boom",
            ),
        ):
            out = self._run(runner.execute("w_nonzero", "print('x')", timeout_s=5))

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
        with patch("sandbox.docker_runner.shutil.which", return_value="/usr/bin/docker"), patch(
            "sandbox.docker_runner.subprocess.run", side_effect=timeout_exc
        ):
            out = self._run(runner.execute("w_timeout", "print('x')", timeout_s=5))

        self.assertIn("timed out", out["error"])
        self.assertEqual(out["stdout"], "partial out")
        self.assertEqual(out["stderr"], "partial err")

    def test_execute_without_docker_cli_returns_error(self) -> None:
        runner = DockerSandboxRunner()
        with patch("sandbox.docker_runner.shutil.which", return_value=None):
            out = self._run(runner.execute("w_no_docker", "print('x')", timeout_s=5))

        self.assertEqual(out["stdout"], "")
        self.assertEqual(out["stderr"], "")
        self.assertIn("docker CLI not found", out["error"])


if __name__ == "__main__":
    unittest.main()
