from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

ChannelSpec = str | tuple[str, dict[str, Any]]
MentionKind = Literal["mention", "reply"]
UNKNOWN_USER = "unknown"


class ChannelType(StrEnum):
    MAIN = "main"
    HOME_TIMELINE = "homeTimeline"
    LOCAL_TIMELINE = "localTimeline"
    HYBRID_TIMELINE = "hybridTimeline"
    GLOBAL_TIMELINE = "globalTimeline"
    ANTENNA = "antenna"
    CHAT_USER = "chatUser"
    CHAT_ROOM = "chatRoom"


TIMELINE_CHANNELS = frozenset(
    {
        ChannelType.HOME_TIMELINE.value,
        ChannelType.LOCAL_TIMELINE.value,
        ChannelType.HYBRID_TIMELINE.value,
        ChannelType.GLOBAL_TIMELINE.value,
    }
)
NOTE_CHANNELS = frozenset({*TIMELINE_CHANNELS, ChannelType.ANTENNA.value})
CHAT_CHANNELS = frozenset({ChannelType.CHAT_USER.value, ChannelType.CHAT_ROOM.value})


@dataclass(frozen=True, slots=True)
class UserRef:
    id: str | None
    username: str
    host: str | None
    is_bot: bool = False

    @property
    def handle(self) -> str:
        return f"{self.username}@{self.host}" if self.host else self.username

    @property
    def known_handle(self) -> str | None:
        return None if self.username == UNKNOWN_USER else self.handle


@dataclass(frozen=True, slots=True)
class FileRef:
    id: str
    mime_type: str | None
    url: str | None
    thumbnail_url: str | None
    raw: Mapping[str, Any]
    name: str | None = None


@dataclass(frozen=True, slots=True)
class NoteRef:
    id: str
    text: str
    cw: str | None
    user: UserRef
    files: tuple[FileRef, ...]
    raw: Mapping[str, Any]
    hidden: bool = False

    @property
    def body(self) -> str:
        return _join_cw(self.cw, self.text)


@dataclass(frozen=True, slots=True)
class MessageEvent:
    id: str
    text: str
    user: UserRef
    room_id: str | None
    files: tuple[FileRef, ...]
    raw: Mapping[str, Any]
    room_name: str | None = None


@dataclass(frozen=True, slots=True)
class MentionEvent:
    id: str
    text: str
    cw: str | None
    user: UserRef
    files: tuple[FileRef, ...]
    raw: Mapping[str, Any]
    reply_id: str | None = None
    renote_id: str | None = None
    reply: NoteRef | None = None
    renote: NoteRef | None = None
    kind: MentionKind = "mention"
    mentions: tuple[str, ...] = ()
    visibility: str | None = None

    @property
    def body(self) -> str:
        return _join_cw(self.cw, self.text)

    @property
    def attachments(self) -> tuple[FileRef, ...]:
        refs = (self, self.renote, self.reply)
        return tuple({f.id: f for ref in refs if ref for f in ref.files}.values())


@dataclass(frozen=True, slots=True)
class NotificationEvent:
    id: str | None
    type: str
    user: UserRef | None
    raw: Mapping[str, Any]
    note: NoteRef | None = None


@dataclass(frozen=True, slots=True)
class TimelineNoteEvent:
    id: str
    text: str
    cw: str | None
    user: UserRef
    channel: str | None
    files: tuple[FileRef, ...]
    raw: Mapping[str, Any]


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _join_cw(cw: str | None, text: str) -> str:
    return "\n\n".join(part for part in (_text(cw), text) if part)


def parse_user(raw: Mapping[str, Any]) -> UserRef:
    value = raw.get("fromUser") or raw.get("user")
    data = value if isinstance(value, dict) else {}
    return UserRef(
        id=_string(data.get("id")) or _string(raw.get("fromUserId")),
        username=_string(data.get("username")) or UNKNOWN_USER,
        host=_string(data.get("host")),
        is_bot=data.get("isBot") is True,
    )


def _file_values(raw: Mapping[str, Any], *, chat: bool) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    if chat:
        if isinstance(raw.get("file"), dict):
            values.append(raw["file"])
        if file_id := _string(raw.get("fileId")):
            values.append({"id": file_id})
        return values
    if isinstance(files := raw.get("files"), list):
        values.extend(item for item in files if isinstance(item, dict))
    if isinstance(ids := raw.get("fileIds"), list):
        values.extend({"id": item} for item in ids if isinstance(item, str))
    return values


def _file_ref(file_id: str, value: dict[str, Any]) -> FileRef:
    return FileRef(
        id=file_id,
        mime_type=_string(value.get("type")),
        url=_string(value.get("url")),
        thumbnail_url=_string(value.get("thumbnailUrl")),
        raw=value,
        name=_string(value.get("name")),
    )


def _files(raw: Mapping[str, Any], *, chat: bool = False) -> tuple[FileRef, ...]:
    unique: dict[str, FileRef] = {}
    for value in _file_values(raw, chat=chat):
        if file_id := _string(value.get("id")):
            unique.setdefault(file_id, _file_ref(file_id, value))
    return tuple(unique.values())


def _note_fields(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "text": _text(raw.get("text")),
        "cw": _string(raw.get("cw")),
        "user": parse_user(raw),
        "files": _files(raw),
        "raw": raw,
    }


def parse_note(raw: Any) -> NoteRef | None:
    if not isinstance(raw, dict) or not (note_id := _string(raw.get("id"))):
        return None
    return NoteRef(id=note_id, hidden=bool(raw.get("isHidden")), **_note_fields(raw))


def parse_message(raw: Any) -> MessageEvent | None:
    if not isinstance(raw, dict) or not (message_id := _string(raw.get("id"))):
        return None
    room: Mapping[str, Any] = (
        raw["toRoom"] if isinstance(raw.get("toRoom"), dict) else {}
    )
    return MessageEvent(
        id=message_id,
        text=_text(raw.get("text")),
        user=parse_user(raw),
        room_id=_string(raw.get("toRoomId")) or _string(room.get("id")),
        files=_files(raw, chat=True),
        raw=raw,
        room_name=_string(room.get("name")),
    )


def parse_mention(raw: Any, kind: MentionKind = "mention") -> MentionEvent | None:
    if not isinstance(raw, dict) or not (note_id := _string(raw.get("id"))):
        return None
    mentions = raw.get("mentions")
    return MentionEvent(
        id=note_id,
        reply_id=_string(raw.get("replyId")),
        renote_id=_string(raw.get("renoteId")),
        reply=parse_note(raw.get("reply")),
        renote=parse_note(raw.get("renote")),
        kind=kind,
        mentions=tuple(m for m in mentions if isinstance(m, str))
        if isinstance(mentions, list)
        else (),
        visibility=_string(raw.get("visibility")),
        **_note_fields(raw),
    )


def parse_notification(raw: Any) -> NotificationEvent | None:
    if not isinstance(raw, dict) or not (kind := _string(raw.get("type"))):
        return None
    return NotificationEvent(
        id=_string(raw.get("id")),
        type=kind,
        user=parse_user(raw) if isinstance(raw.get("user"), dict) else None,
        raw=raw,
        note=parse_note(raw.get("note")),
    )


def parse_timeline_note(raw: Any) -> TimelineNoteEvent | None:
    if not isinstance(raw, dict) or not (note_id := _string(raw.get("id"))):
        return None
    return TimelineNoteEvent(
        id=note_id,
        channel=_string(raw.get("streamingChannel")),
        **_note_fields(raw),
    )
