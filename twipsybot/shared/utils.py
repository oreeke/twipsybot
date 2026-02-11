import json
import os
import platform
import re
from typing import Any

import psutil
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

__all__ = (
    "extract_user_handle",
    "extract_user_id",
    "extract_username",
    "extract_chat_text",
    "extract_first_text",
    "extract_note_text",
    "get_memory_usage",
    "get_system_info",
    "maybe_log_event_dump",
    "normalize_tokens",
    "normalize_payload",
    "redact_misskey_access_token",
    "resolve_history_limit",
    "retry_async",
)

_MISSKEY_I_PARAM_RE = re.compile(r"([?&]i=)[^&#\s]+")
_MISSKEY_I_JSON_RE = re.compile(r'("i"\s*:\s*")[^"]+(")')


def redact_misskey_access_token(text: str) -> str:
    if not text:
        return text
    text = _MISSKEY_I_PARAM_RE.sub(r"\1***", text)
    return _MISSKEY_I_JSON_RE.sub(r"\1***\2", text)


def retry_async(max_retries=3, retryable_exceptions=None):
    kwargs = {
        "stop": stop_after_attempt(max_retries),
        "wait": wait_random_exponential(multiplier=1, max=30),
        "before_sleep": lambda retry_state: logger.info(
            f"Retry attempt #{retry_state.attempt_number}..."
        ),
    }
    if retryable_exceptions:
        kwargs["retry"] = retry_if_exception_type(retryable_exceptions)
    return retry(**kwargs)


def get_system_info() -> dict[str, Any]:
    return {
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "cpu_count": psutil.cpu_count(),
        "memory_total_gb": round(psutil.virtual_memory().total / (1024**3), 2),
        "process_id": os.getpid(),
    }


def get_memory_usage() -> dict[str, Any]:
    process = psutil.Process()
    memory_info = process.memory_info()
    mb_factor = 1024 * 1024
    return {
        "rss_mb": round(memory_info.rss / mb_factor, 2),
        "vms_mb": round(memory_info.vms / mb_factor, 2),
        "percent": process.memory_percent(),
    }


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


def extract_first_text(data: Any, *keys: str) -> str:
    if not isinstance(data, dict):
        return ""
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and (s := value.strip()):
            return s
    return ""


def extract_chat_text(data: Any) -> str:
    return extract_first_text(data, "text", "content", "body")


def extract_note_text(
    data: Any, *, include_cw: bool = True, allow_body_fallback: bool = False
) -> str:
    if not isinstance(data, dict):
        return ""
    parts: list[str] = []
    if include_cw:
        if isinstance((cw := data.get("cw")), str) and (s := cw.strip()):
            parts.append(s)
    if isinstance((text := data.get("text")), str) and (s := text.strip()):
        parts.append(s)
    elif allow_body_fallback:
        if isinstance((body := data.get("body")), str) and (s := body.strip()):
            parts.append(s)
    return "\n\n".join(parts).strip()


def normalize_payload(data: Any, *, kind: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    if kind != "chat" and isinstance(data.get("note"), dict):
        return data["note"]
    return data


def maybe_log_event_dump(enabled: bool, *, kind: str, payload: Any) -> None:
    if not enabled:
        return
    logger.opt(lazy=True).debug(
        "{} data: {}",
        lambda: kind,
        lambda: json.dumps(payload, ensure_ascii=False, indent=2),
    )


def resolve_history_limit(config_value: int | None, limit: int | None) -> int:
    if isinstance(limit, int):
        return limit
    if isinstance(config_value, int):
        return config_value
    return 0


def extract_user_id(message: dict[str, Any]) -> str | None:
    user_info = message.get("fromUser") or message.get("user")
    if isinstance(user_info, dict):
        return user_info.get("id")
    return message.get("userId") or message.get("fromUserId")


def extract_username(message: dict[str, Any]) -> str:
    user_info = message.get("fromUser") or message.get("user", {})
    if isinstance(user_info, dict):
        return user_info.get("username", "unknown")
    return "unknown"


def extract_user_handle(message: dict[str, Any]) -> str | None:
    user_info = message.get("fromUser") or message.get("user")
    if not isinstance(user_info, dict):
        return None
    username = user_info.get("username")
    if not isinstance(username, str) or not (u := username.strip()):
        return None
    host = user_info.get("host")
    if isinstance(host, str) and (h := host.strip()):
        return f"{u}@{h}"
    return u
