from __future__ import annotations

import os
from pathlib import Path

_ENV_LOADED = False


def _parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None

    key, raw_value = stripped.split("=", 1)
    key = key.strip()
    value = raw_value.strip()

    if not key:
        return None

    if value and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]

    return key, value


def _load_env_file(path: Path) -> None:
    if not path.exists() or not path.is_file():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_env_line(line)
        if not parsed:
            continue
        key, value = parsed
        os.environ.setdefault(key, value)


def load_env_once() -> None:
    global _ENV_LOADED
    if _ENV_LOADED:
        return

    backend_dir = Path(__file__).resolve().parent
    project_root = backend_dir.parent

    _load_env_file(project_root / ".env")
    _load_env_file(backend_dir / ".env")

    _ENV_LOADED = True
