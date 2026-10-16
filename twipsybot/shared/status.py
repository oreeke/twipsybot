import json
import os
import time
from pathlib import Path

HEARTBEAT_SECONDS = 3.0
_STALE_SECONDS = 10.0
_STOPPED = {"bot": "stopped", "stream": "disconnected"}


def write_status(path: Path, bot: str, stream: str) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"bot": bot, "stream": stream}), encoding="utf-8")
    os.replace(tmp, path)


def read_status(path: Path) -> dict[str, str]:
    try:
        if time.time() - path.stat().st_mtime > _STALE_SECONDS:
            return _STOPPED
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _STOPPED
    return data if isinstance(data, dict) else _STOPPED
