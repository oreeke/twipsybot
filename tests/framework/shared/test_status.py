from __future__ import annotations

import os
import time
from pathlib import Path

from twipsybot.shared.status import read_status, write_status


def test_status_round_trip_and_expiry(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    assert read_status(path) == {"bot": "stopped", "stream": "disconnected"}
    write_status(path, "running", "connected")
    assert read_status(path) == {"bot": "running", "stream": "connected"}
    old = time.time() - 60
    os.utime(path, (old, old))
    assert read_status(path)["bot"] == "stopped"
    path.write_text("[]", encoding="utf-8")
    assert read_status(path)["bot"] == "stopped"
