from __future__ import annotations

import asyncio
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
        image: str = "python:3.11-slim",
        limits: SandboxLimits | None = None,
    ) -> None:
        self.image = image
        self.limits = limits or SandboxLimits()

    async def execute(
        self,
        worldline_id: str,
        code: str,
        timeout_s: int,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._execute_sync,
            worldline_id,
            code,
            timeout_s,
        )

    def _workspace_dir(self, worldline_id: str) -> Path:
        return meta.DB_DIR / "worldlines" / worldline_id / "workspace"

    def _build_command(self, workspace: Path, timeout_s: int) -> list[str]:
        del timeout_s  # timeout is enforced by subprocess.run timeout.
        return [
            "docker",
            "run",
            "--rm",
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
            "PYTHONDONTWRITEBYTECODE=1",
            "-v",
            f"{workspace}:/workspace",
            "-w",
            "/workspace",
            self.image,
            "python",
            "/workspace/.runner_input.py",
        ]

    def _scan_artifacts(self, workspace: Path) -> list[dict[str, str]]:
        artifacts_dir = workspace / "artifacts"
        artifacts: list[dict[str, str]] = []
        if not artifacts_dir.exists():
            return artifacts

        for file_path in sorted(artifacts_dir.rglob("*")):
            if not file_path.is_file():
                continue
            suffix = file_path.suffix.lower()
            if suffix in {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}:
                artifact_type = "image"
            elif suffix == ".csv":
                artifact_type = "csv"
            elif suffix == ".md":
                artifact_type = "md"
            elif suffix == ".pdf":
                artifact_type = "pdf"
            else:
                artifact_type = "file"

            artifacts.append(
                {
                    "type": artifact_type,
                    "name": file_path.name,
                    "path": str(file_path),
                }
            )
        return artifacts

    def _execute_sync(
        self,
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

        script_path = workspace / ".runner_input.py"
        script_path.write_text(code, encoding="utf-8")

        cmd = self._build_command(workspace=workspace, timeout_s=timeout_s)
        try:
            if shutil.which("docker") is None:
                return {
                    "stdout": "",
                    "stderr": "",
                    "error": "docker CLI not found on PATH",
                    "artifacts": self._scan_artifacts(workspace),
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
                return {
                    "stdout": _to_text(exc.stdout),
                    "stderr": _to_text(exc.stderr),
                    "error": f"Python execution timed out after {timeout_s} seconds",
                    "artifacts": self._scan_artifacts(workspace),
                    "previews": {"dataframes": []},
                }
            except Exception as exc:
                return {
                    "stdout": "",
                    "stderr": "",
                    "error": str(exc),
                    "artifacts": self._scan_artifacts(workspace),
                    "previews": {"dataframes": []},
                }

            error = None
            if proc.returncode != 0:
                error = (proc.stderr or "").strip() or f"Process exited with code {proc.returncode}"

            return {
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "error": error,
                "artifacts": self._scan_artifacts(workspace),
                "previews": {"dataframes": []},
            }
        finally:
            script_path.unlink(missing_ok=True)
