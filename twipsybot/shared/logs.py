import re
import sys
from pathlib import Path
from typing import Any

from loguru import logger

__all__ = ("LOG_LINE", "set_log_level", "setup_logging")

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
