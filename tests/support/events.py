from __future__ import annotations

from typing import Any

from twipsybot.clients.misskey.models import (
    MentionEvent,
    MentionKind,
    MessageEvent,
    NotificationEvent,
    TimelineNoteEvent,
    parse_mention,
    parse_message,
    parse_notification,
    parse_timeline_note,
)


def message(raw: dict[str, Any]) -> MessageEvent:
    event = parse_message(raw)
    assert event is not None, raw
    return event


def mention(raw: dict[str, Any], kind: MentionKind = "mention") -> MentionEvent:
    event = parse_mention(raw, kind)
    assert event is not None, raw
    return event


def notification(raw: dict[str, Any]) -> NotificationEvent:
    event = parse_notification(raw)
    assert event is not None, raw
    return event


def timeline_note(raw: dict[str, Any]) -> TimelineNoteEvent:
    event = parse_timeline_note(raw)
    assert event is not None, raw
    return event
