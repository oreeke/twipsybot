from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

from ..shared.utils import extract_chat_text, extract_note_text, normalize_payload

__all__ = (
    "AutoPostEvent",
    "FileRef",
    "MentionEvent",
    "MessageEvent",
    "NotificationEvent",
    "TimelineNoteEvent",
    "UserRef",
)


@dataclass(frozen=True, slots=True)
class UserRef:
    id: str | None
    username: str
    host: str | None

    @property
    def handle(self) -> str:
        return f"{self.username}@{self.host}" if self.host else self.username


@dataclass(frozen=True, slots=True)
class FileRef:
    id: str
    mime_type: str | None
    url: str | None
    thumbnail_url: str | None
    raw: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class MessageEvent:
    id: str
    text: str
    user: UserRef
    room_id: str | None
    files: tuple[FileRef, ...]
    raw: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class MentionEvent:
    id: str
    text: str
    cw: str | None
    user: UserRef
    files: tuple[FileRef, ...]
    raw: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class NotificationEvent:
    id: str | None
    type: str
    user: UserRef | None
    raw: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class TimelineNoteEvent:
    id: str
    text: str
    cw: str | None
    user: UserRef
    channel: str | None
    files: tuple[FileRef, ...]
    raw: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class AutoPostEvent:
    triggered_at: datetime


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _user(payload: Mapping[str, Any]) -> UserRef:
    value = payload.get("fromUser") or payload.get("user")
    data = value if isinstance(value, dict) else {}
    return UserRef(
        id=_string(data.get("id")),
        username=_string(data.get("username")) or "unknown",
        host=_string(data.get("host")),
    )


def _files(payload: Mapping[str, Any], *, chat: bool = False) -> tuple[FileRef, ...]:
    values: list[dict[str, Any]] = []
    if chat:
        if isinstance(payload.get("file"), dict):
            values.append(payload["file"])
        if file_id := _string(payload.get("fileId")):
            values.append({"id": file_id})
    else:
        raw_files = payload.get("files")
        if isinstance(raw_files, list):
            values.extend(item for item in raw_files if isinstance(item, dict))
        raw_ids = payload.get("fileIds")
        if isinstance(raw_ids, list):
            values.extend({"id": value} for value in raw_ids if isinstance(value, str))
    unique: dict[str, FileRef] = {}
    for value in values:
        if not (file_id := _string(value.get("id"))):
            continue
        unique.setdefault(
            file_id,
            FileRef(
                id=file_id,
                mime_type=_string(value.get("type")),
                url=_string(value.get("url")),
                thumbnail_url=_string(value.get("thumbnailUrl")),
                raw=MappingProxyType(value),
            ),
        )
    return tuple(unique.values())


def _message_event(raw: dict[str, Any]) -> MessageEvent:
    if not (event_id := _string(raw.get("id"))):
        raise ValueError("message event requires id")
    room = raw.get("toRoom")
    room_id = _string(raw.get("toRoomId"))
    if room_id is None and isinstance(room, dict):
        room_id = _string(room.get("id"))
    return MessageEvent(
        id=event_id,
        text=extract_chat_text(raw),
        user=_user(raw),
        room_id=room_id,
        files=_files(raw, chat=True),
        raw=MappingProxyType(raw),
    )


def _mention_event(raw: dict[str, Any]) -> MentionEvent:
    note = normalize_payload(raw, kind="mention")
    if not (event_id := _string(note.get("id"))):
        raise ValueError("mention event requires id")
    return MentionEvent(
        id=event_id,
        text=extract_note_text(note, include_cw=False, allow_body_fallback=True),
        cw=_string(note.get("cw")),
        user=_user(note),
        files=_files(note),
        raw=MappingProxyType(note),
    )


def _notification_event(raw: dict[str, Any]) -> NotificationEvent:
    nested = raw.get("notification")
    notification = nested if isinstance(nested, dict) else raw
    if not (event_type := _string(notification.get("type"))):
        raise ValueError("notification event requires type")
    user_value = notification.get("user")
    return NotificationEvent(
        id=_string(notification.get("id")),
        type=event_type,
        user=_user(notification) if isinstance(user_value, dict) else None,
        raw=MappingProxyType(notification),
    )


def _timeline_note_event(raw: dict[str, Any]) -> TimelineNoteEvent:
    if not (event_id := _string(raw.get("id"))):
        raise ValueError("timeline note event requires id")
    return TimelineNoteEvent(
        id=event_id,
        text=extract_note_text(raw, include_cw=False),
        cw=_string(raw.get("cw")),
        user=_user(raw),
        channel=_string(raw.get("streamingChannel")),
        files=_files(raw),
        raw=MappingProxyType(raw),
    )


def build_hook_event(hook_name: str, payload: Any = None) -> Any:
    raw = deepcopy(payload) if isinstance(payload, dict) else {}
    builders = {
        "on_message": _message_event,
        "on_mention": _mention_event,
        "on_notification": _notification_event,
        "on_timeline_note": _timeline_note_event,
    }
    if builder := builders.get(hook_name):
        return builder(raw)
    if hook_name == "on_auto_post":
        return AutoPostEvent(triggered_at=datetime.now(UTC))
    return payload
