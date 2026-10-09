import json
import re
import sys
from pathlib import Path
from typing import Any

from loguru import logger

__all__ = (
    "LOG_LINE",
    "format_log_text",
    "maybe_log_event_dump",
    "set_log_level",
    "setup_logging",
)

_FORMAT = "{time:YYYY-MM-DD HH:mm:ss.SSS} | <level>{level: <8}</level> | <level>{message}</level>"
LOG_LINE = re.compile(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}) \| (\w+)\s* \| ")
_threshold = [logger.level("INFO").no]


def _filter(record: Any) -> bool:
    return record["level"].no >= _threshold[0]


def set_log_level(level: str) -> None:
    _threshold[0] = logger.level(level).no


def setup_logging(path: Path, level: str) -> None:
    set_log_level(level)
    logger.remove()
    logger.add(sys.stderr, level=0, format=_FORMAT, filter=_filter)
    logger.add(
        path,
        level=0,
        format=_FORMAT,
        filter=_filter,
        rotation="10 MB",
        retention=5,
        compression="zip",
        enqueue=True,
    )


def format_log_text(text: str, max_length: int = 50) -> str:
    if not text:
        return "None"
    suffix = "..." if len(text) > max_length else ""
    return f"{text[:max_length]}{suffix}"


def maybe_log_event_dump(enabled: bool, *, kind: str, payload: Any) -> None:
    if not enabled:
        return
    logger.opt(lazy=True).debug(
        "{} data: {}",
        lambda: kind,
        lambda: json.dumps(payload, ensure_ascii=False, indent=2),
    )
