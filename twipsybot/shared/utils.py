import json
from typing import Any

from loguru import logger

__all__ = (
    "format_log_text",
    "maybe_log_event_dump",
    "normalize_tokens",
)


def format_log_text(text: str, max_length: int = 50) -> str:
    if not text:
        return "None"
    suffix = "..." if len(text) > max_length else ""
    return f"{text[:max_length]}{suffix}"


def normalize_tokens(value: Any, *, lower: bool = False) -> list[str]:
    if value is None or isinstance(value, bool):
        return []
    if isinstance(value, str):
        tokens = [t.strip() for t in value.replace(",", " ").split() if t.strip()]
    elif isinstance(value, list):
        tokens = [str(v).strip() for v in value if v is not None and str(v).strip()]
    else:
        s = str(value).strip()
        tokens = [s] if s else []
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        k = t.lower() if lower else t
        if k in seen:
            continue
        seen.add(k)
        out.append(k if lower else t)
    return out


def maybe_log_event_dump(enabled: bool, *, kind: str, payload: Any) -> None:
    if not enabled:
        return
    logger.opt(lazy=True).debug(
        "{} data: {}",
        lambda: kind,
        lambda: json.dumps(payload, ensure_ascii=False, indent=2),
    )
