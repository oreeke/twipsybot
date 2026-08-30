from typing import Any


def extract_first_text(data: Any, *keys: str) -> str:
    if not isinstance(data, dict):
        return ""
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and (text := value.strip()):
            return text
    return ""


def extract_chat_text(data: Any) -> str:
    return extract_first_text(data, "text", "content", "body")


def extract_note_text(
    data: Any, *, include_cw: bool = True, allow_body_fallback: bool = False
) -> str:
    if not isinstance(data, dict):
        return ""
    parts: list[str] = []
    if include_cw and isinstance((cw := data.get("cw")), str) and (text := cw.strip()):
        parts.append(text)
    value = data.get("text")
    if allow_body_fallback and not (isinstance(value, str) and value.strip()):
        value = data.get("body")
    if isinstance(value, str) and (text := value.strip()):
        parts.append(text)
    return "\n\n".join(parts).strip()


def normalize_payload(data: Any, *, kind: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    if kind != "chat" and isinstance(data.get("note"), dict):
        return data["note"]
    return data


def extract_user_id(message: dict[str, Any]) -> str | None:
    user = message.get("fromUser") or message.get("user")
    if isinstance(user, dict):
        return user.get("id")
    return message.get("userId") or message.get("fromUserId")


def extract_username(message: dict[str, Any]) -> str:
    user = message.get("fromUser") or message.get("user", {})
    if isinstance(user, dict):
        return user.get("username", "unknown")
    return "unknown"


def extract_user_handle(message: dict[str, Any]) -> str | None:
    user = message.get("fromUser") or message.get("user")
    if not isinstance(user, dict):
        return None
    username = user.get("username")
    if not isinstance(username, str) or not (username := username.strip()):
        return None
    host = user.get("host")
    if isinstance(host, str) and (host := host.strip()):
        return f"{username}@{host}"
    return username
