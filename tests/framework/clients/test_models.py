from __future__ import annotations

from typing import Any

import pytest

from twipsybot.clients.misskey.models import (
    parse_mention,
    parse_message,
    parse_note,
    parse_notification,
    parse_timeline_note,
    parse_user,
)


@pytest.mark.parametrize("raw", [None, [], {}, {"text": "no id"}, {"id": ""}])
def test_id_events_reject_invalid_payloads(raw: Any) -> None:
    assert parse_message(raw) is None
    assert parse_mention(raw) is None
    assert parse_note(raw) is None
    assert parse_timeline_note(raw) is None


@pytest.mark.parametrize("raw", [None, {}, {"id": "n-1"}, {"type": ""}])
def test_notification_requires_type(raw: Any) -> None:
    assert parse_notification(raw) is None


def test_message_user_falls_back_to_sender_id_and_reads_room() -> None:
    event = parse_message(
        {
            "id": "m-1",
            "text": " hi ",
            "fromUserId": "u-1",
            "toRoom": {"id": "r-1", "name": "lounge"},
            "fileId": "f-1",
        }
    )

    assert event is not None
    assert (event.text, event.user.id, event.user.username) == ("hi", "u-1", "unknown")
    assert (event.room_id, event.room_name) == ("r-1", "lounge")
    assert [f.id for f in event.files] == ["f-1"]
    assert event.user.known_handle is None


def test_mention_exposes_kind_and_routing_fields() -> None:
    event = parse_mention(
        {
            "id": "n-1",
            "cw": " warn ",
            "text": " body ",
            "user": {"id": "u-1", "username": "alice", "host": "example.com"},
            "mentions": ["bot", 1, "other"],
            "visibility": "specified",
            "isHidden": True,
        },
        "reply",
    )

    assert event is not None
    assert event.kind == "reply"
    assert event.mentions == ("bot", "other")
    assert event.visibility == "specified"
    assert event.body == "warn\n\nbody"
    assert event.user.known_handle == "alice@example.com"


def test_files_are_deduplicated_across_inline_and_id_lists() -> None:
    event = parse_timeline_note(
        {
            "id": "n-1",
            "files": [
                {"id": "f-1", "type": "image/png", "name": "a.png"},
                {"name": "x"},
            ],
            "fileIds": ["f-1", "f-2"],
            "streamingChannel": "antenna",
        }
    )

    assert event is not None
    assert [f.id for f in event.files] == ["f-1", "f-2"]
    assert (event.files[0].mime_type, event.files[0].name) == ("image/png", "a.png")
    assert event.channel == "antenna"


def test_note_ref_marks_hidden_notes() -> None:
    note = parse_note({"id": "n-1", "text": "x", "isHidden": True})

    assert note is not None
    assert note.hidden


def test_user_bot_flag_requires_true() -> None:
    assert parse_user({"user": {"username": "bot", "isBot": True}}).is_bot
    assert not parse_user({"user": {"username": "alice", "isBot": "yes"}}).is_bot


def test_notification_exposes_note() -> None:
    event = parse_notification(
        {
            "id": "x-1",
            "type": "reaction",
            "user": {"id": "u-1", "username": "alice"},
            "note": {"id": "n-1", "text": " hi "},
        }
    )

    assert event is not None
    assert event.note is not None
    assert (event.note.id, event.note.text) == ("n-1", "hi")
    follow = parse_notification({"type": "follow", "user": {"id": "u-1"}})
    assert follow is not None
    assert follow.note is None
