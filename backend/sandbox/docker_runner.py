from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

try:
    from backend import meta
except ModuleNotFoundError:
    import meta


@dataclass(frozen=True)
class SandboxLimits:
    pids_limit: int = 128
    memory: str = "512m"
    cpus: str = "1.0"


class DockerSandboxRunner:
    def __init__(
        self,
        image: str | None = None,
        limits: SandboxLimits | None = None,
    ) -> None:
        self.image = image or os.getenv("SANDBOX_IMAGE", "textql-sandbox:py311")
        self.limits = limits or SandboxLimits()

    async def execute(
        self,
        sandbox_id: str,
        worldline_id: str,
        code: str,
        timeout_s: int,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._execute_sync,
            sandbox_id,
            worldline_id,
            code,
            timeout_s,
        )

    async def start(self, worldline_id: str) -> str:
        return await asyncio.to_thread(self._start_sync, worldline_id)

    async def stop(self, sandbox_id: str) -> None:
        await asyncio.to_thread(self._stop_sync, sandbox_id)

    def _workspace_dir(self, worldline_id: str) -> Path:
        return meta.DB_DIR / "worldlines" / worldline_id / "workspace"

    def _build_start_command(self, sandbox_id: str, workspace: Path) -> list[str]:
        return [
            "docker",
            "run",
            "-d",
            "--name",
            sandbox_id,
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--read-only",
            "--pids-limit",
            str(self.limits.pids_limit),
            "--memory",
            self.limits.memory,
            "--cpus",
            self.limits.cpus,
            "--user",
            "1000:1000",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=64m",
            "-e",
            "MPLBACKEND=Agg",
            "-e",
            "MPLCONFIGDIR=/tmp/matplotlib",
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
            "-v",
            f"{workspace}:/workspace",
            "-w",
            "/workspace",
            self.image,
            "sleep",
            "infinity",
        ]

    def _build_exec_command(self, sandbox_id: str) -> list[str]:
        return [
            "docker",
            "exec",
            sandbox_id,
            "python",
            "/workspace/.runner_input.py",
        ]

    def _classify_artifact_type(self, file_path: Path) -> str:
        suffix = file_path.suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}:
            return "image"
        if suffix == ".csv":
            return "csv"
        if suffix == ".md":
            return "md"
        if suffix == ".pdf":
            return "pdf"
        return "file"

    def _is_artifact_candidate(self, workspace: Path, file_path: Path) -> bool:
        if not file_path.is_file():
            return False

        if file_path.name.startswith("."):
            return False
        if file_path.name == ".runner_input.py":
            return False

        # Exclude hidden paths under workspace.
        for part in file_path.relative_to(workspace).parts:
            if part.startswith("."):
                return False
        return True

    def _snapshot_workspace(self, workspace: Path) -> dict[str, float]:
        """Capture file paths and modification times before execution."""
        snapshot: dict[str, float] = {}
        for file_path in workspace.rglob("*"):
            if self._is_artifact_candidate(workspace, file_path):
                snapshot[str(file_path.resolve())] = file_path.stat().st_mtime
        return snapshot

    def _scan_artifacts(
        self,
        workspace: Path,
        before_snapshot: dict[str, float] | None = None,
    ) -> list[dict[str, str]]:
        """Scan for artifacts, optionally filtering to only new/modified files."""
        artifacts: list[dict[str, str]] = []
        seen_paths: set[str] = set()

        for file_path in sorted(workspace.rglob("*")):
            if not self._is_artifact_candidate(workspace, file_path):
                continue

            normalized_path = str(file_path.resolve())
            if normalized_path in seen_paths:
                continue
            seen_paths.add(normalized_path)

            # If we have a before snapshot, only include new or modified files
            if before_snapshot is not None:
                current_mtime = file_path.stat().st_mtime
                prev_mtime = before_snapshot.get(normalized_path)
                # Skip if file existed before and wasn't modified
                if prev_mtime is not None and current_mtime <= prev_mtime:
                    continue

            artifacts.append(
                {
                    "type": self._classify_artifact_type(file_path),
                    "name": file_path.name,
                    "path": str(file_path),
                }
            )
        return artifacts

    def _start_sync(self, worldline_id: str) -> str:
        if shutil.which("docker") is None:
            raise RuntimeError("docker CLI not found on PATH")

        workspace = self._workspace_dir(worldline_id)
        artifacts_dir = workspace / "artifacts"
        workspace.mkdir(parents=True, exist_ok=True)
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        safe_worldline = (
            "".join(ch for ch in worldline_id if ch.isalnum()) or "worldline"
        )
        sandbox_id = f"textql_{safe_worldline[-20:]}_{uuid4().hex[:8]}"

        start_cmd = self._build_start_command(
            sandbox_id=sandbox_id, workspace=workspace
        )
        proc = subprocess.run(
            start_cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(detail or "failed to start sandbox container")
        return sandbox_id

    def _stop_sync(self, sandbox_id: str) -> None:
        if shutil.which("docker") is None:
            return
        subprocess.run(
            ["docker", "rm", "-f", sandbox_id],
            capture_output=True,
            text=True,
            check=False,
        )

    def _kill_python_processes_sync(self, sandbox_id: str) -> None:
        """Kill any running Python processes inside the sandbox container."""
        if shutil.which("docker") is None:
            return
        # Try to kill Python processes gracefully first, then forcefully
        subprocess.run(
            ["docker", "exec", sandbox_id, "pkill", "-f", "python"],
            capture_output=True,
            text=True,
            check=False,
        )
        # Give it a moment, then force kill if still running
        subprocess.run(
            ["docker", "exec", sandbox_id, "pkill", "-9", "-f", "python"],
            capture_output=True,
            text=True,
            check=False,
        )

    def _execute_sync(
        self,
        sandbox_id: str,
        worldline_id: str,
        code: str,
        timeout_s: int,
    ) -> dict[str, Any]:
        def _to_text(value: Any) -> str:
            if value is None:
                return ""
            if isinstance(value, bytes):
                return value.decode("utf-8", errors="replace")
            return str(value)

        workspace = self._workspace_dir(worldline_id)
        artifacts_dir = workspace / "artifacts"
        workspace.mkdir(parents=True, exist_ok=True)
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        # Snapshot files before execution to detect new/modified artifacts
        before_snapshot = self._snapshot_workspace(workspace)

        script_path = workspace / ".runner_input.py"
        script_path.write_text(code, encoding="utf-8")

        cmd = self._build_exec_command(sandbox_id=sandbox_id)
        try:
            if shutil.which("docker") is None:
                return {
                    "stdout": "",
                    "stderr": "",
                    "error": "docker CLI not found on PATH",
                    "artifacts": self._scan_artifacts(workspace, before_snapshot),
                    "previews": {"dataframes": []},
                }

            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout_s,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                # Kill any lingering Python processes in the container to prevent ghosts
                self._kill_python_processes_sync(sandbox_id)
                return {
                    "stdout": _to_text(exc.stdout),
                    "stderr": _to_text(exc.stderr),
                    "error": f"Python execution timed out after {timeout_s} seconds",
                    "artifacts": self._scan_artifacts(workspace, before_snapshot),
                    "previews": {"dataframes": []},
                }
            except Exception as exc:
                return {
                    "stdout": "",
                    "stderr": "",
                    "error": str(exc),
                    "artifacts": self._scan_artifacts(workspace, before_snapshot),
                    "previews": {"dataframes": []},
                }

            error = None
            if proc.returncode != 0:
                error = (
                    proc.stderr or ""
                ).strip() or f"Process exited with code {proc.returncode}"

            return {
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "error": error,
                "artifacts": self._scan_artifacts(workspace, before_snapshot),
                "previews": {"dataframes": []},
            }
        finally:
            script_path.unlink(missing_ok=True)
