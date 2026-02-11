from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

DEBUG_LOG_PATH = Path("/Users/shivom/take_homes/textql/.cursor/debug.log")


def debug_log(
    *,
    run_id: str,
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict[str, Any],
    path: Path = DEBUG_LOG_PATH,
) -> None:
    try:
        payload = {
            "id": f"log_{time.time_ns()}",
            "timestamp": int(time.time() * 1000),
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as debug_file:
            debug_file.write(json.dumps(payload, ensure_ascii=True, default=str) + "\n")
    except Exception:
        pass
