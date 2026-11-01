from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from support.events import mention, message, notification, timeline_note

from twipsybot.plugin import MentionEvent, MessageEvent

_EVENT = message({"id": "message-1", "user": {"username": "a"}})
_PROBE = (
    "result = None\n"
    "async def on_message(self, event):\n"
    "    return self.result\n"
    "async def on_context(self, event):\n"
    "    return self.result\n"
    "async def on_auto_post(self, event):\n"
    "    return self.result\n"
)

type CallHook = Callable[[str, Any], Awaitable[bool]]


@pytest.fixture
async def accepted(boot) -> CallHook:
    bot = await boot(probe=_PROBE)
    probe = bot.plugin_manager.plugins["probe"]

    async def call(hook: str, result: Any) -> bool:
        probe.result = result
        return bool(await bot.plugin_manager.call_plugin_hook(hook, _EVENT))

    return call


def _ids(count: int) -> list[str]:
    return [f"f{i}" for i in range(count)]


@pytest.mark.parametrize(
    ("result", "valid"),
    [
        ({"handled": True, "response": "ok"}, True),
        ({"handled": True, "response": "ok", "file_ids": ["f1", "f2"]}, True),
        ({"handled": True, "response": "ok", "followup": lambda: None}, True),
        ({"handled": True, "response": "ok", "file_id": "f1"}, False),
        ({"handled": True, "response": "ok", "file_ids": ["f1", "f1"]}, False),
        ({"handled": True, "response": "ok", "file_ids": "f1"}, False),
        ({"handled": True, "response": "ok", "file_ids": _ids(17)}, False),
        ({"handled": True, "response": "ok", "followup": "later"}, False),
        ({"handled": True, "response": "ok", "links": ["https://a.test"]}, True),
        ({"handled": True, "response": "ok", "links": ["javascript:x"]}, False),
        ({"handled": True, "response": "ok", "extra": 1}, False),
        ({"handled": True, "response": 42}, False),
        ({"handled": True}, False),
        ("ok", False),
    ],
)
async def test_message_result_is_validated(
    accepted: CallHook, result: Any, valid: bool
) -> None:
    assert await accepted("on_message", result) is valid


@pytest.mark.parametrize(
    ("result", "valid"),
    [
        ({"prompt": "p"}, True),
        ({"contents": ["text"]}, True),
        ({"contents": [{"text": "text"}], "visibility": "home"}, True),
        ({"contents": ["a", {"text": "b", "file_ids": ["f1", "f2"]}]}, True),
        ({"prompt": " "}, False),
        ({"prompt": "p", "contents": ["text"]}, False),
        ({"contents": []}, False),
        ({"contents": ["x"], "visibility": []}, False),
        ({"contents": ["x"], "visibility": "everyone"}, False),
        ({"contents": [{"text": "b", "file_ids": _ids(17)}]}, False),
        ({"contents": [{"text": "b", "file_ids": ["f1", "f1"]}]}, False),
        ({"contents": [{"text": "b", "file_ids": [""]}]}, False),
        ({"contents": [{"text": "b", "file_ids": "f1"}]}, False),
        ({"contents": [{"text": " "}]}, False),
        ({"contents": [{"file_ids": ["f1"]}]}, False),
        ({"contents": [{"text": "b", "extra": 1}]}, False),
        ({"contents": [1]}, False),
    ],
)
async def test_auto_post_result_is_validated(
    accepted: CallHook, result: Any, valid: bool
) -> None:
    assert await accepted("on_auto_post", result) is valid


@pytest.mark.parametrize(
    ("result", "valid"),
    [
        ({"context": "c"}, True),
        ({"text": "t"}, True),
        ({"context": "c", "text": "t"}, True),
        ({"media": [{"type": "input_image", "image_url": "u"}]}, True),
        ({"context": "c", "media": [{"type": "input_text"}]}, True),
        ({}, False),
        ({"media": []}, False),
        ({"media": [{"image_url": "u"}]}, False),
        ({"media": "image"}, False),
        ({"context": " "}, False),
        ({"context": 1}, False),
        ({"context": "c", "extra": "x"}, False),
    ],
)
async def test_context_result_is_validated(
    accepted: CallHook, result: Any, valid: bool
) -> None:
    assert await accepted("on_context", result) is valid


async def test_all_hooks_receive_stable_events(boot) -> None:
    bot = await boot(
        events="def __init__(self, context):\n"
        "    super().__init__(context)\n"
        "    self.received = []\n"
        "async def on_message(self, event):\n"
        "    self.received.append((type(event), event.id, event.room_id))\n"
        "async def on_mention(self, event):\n"
        "    self.received.append((type(event), event.id, event.text))\n"
        "async def on_notification(self, event):\n"
        "    self.received.append((type(event), event.id, event.type))\n"
        "async def on_timeline_note(self, event):\n"
        "    self.received.append((type(event), event.id, event.channel))\n"
        "async def on_auto_post(self, event):\n"
        "    self.received.append((type(event), bool(event.triggered_at), None))\n"
    )
    manager = bot.plugin_manager

    await manager.call_plugin_hook(
        "on_message",
        message(
            {
                "id": "message-1",
                "text": "hello",
                "user": {"username": "a"},
                "toRoom": {"id": "room-1"},
            }
        ),
    )
    await manager.call_plugin_hook(
        "on_mention",
        mention({"id": "mention-1", "text": "hi", "user": {"username": "b"}}),
    )
    await manager.call_plugin_hook(
        "on_notification",
        notification(
            {"id": "notification-1", "type": "reaction", "user": {"username": "c"}}
        ),
    )
    await manager.call_plugin_hook(
        "on_timeline_note",
        timeline_note(
            {
                "id": "note-1",
                "text": "post",
                "user": {"username": "d"},
                "streamingChannel": "antenna",
            }
        ),
    )
    await manager.call_plugin_hook("on_auto_post")

    assert [
        (item[0].__name__, item[1], item[2])
        for item in manager.plugins["events"].received
    ] == [
        ("MessageEvent", "message-1", "room-1"),
        ("MentionEvent", "mention-1", "hi"),
        ("NotificationEvent", "notification-1", "reaction"),
        ("TimelineNoteEvent", "note-1", "antenna"),
        ("AutoPostEvent", True, None),
    ]


async def test_event_raw_is_isolated_between_plugins(boot) -> None:
    bot = await boot(
        mutator="async def on_message(self, event):\n"
        "    event.raw['user']['username'] = 'changed'\n",
        observer="async def on_message(self, event):\n"
        "    return self.handled(event.raw['user']['username'])\n",
    )
    payload = {"id": "message-1", "user": {"username": "original"}}

    results = await bot.plugin_manager.call_plugin_hook("on_message", message(payload))

    assert results[0][1]["response"] == "original"
    assert payload["user"]["username"] == "original"


async def test_reused_result_is_not_mutated(boot) -> None:
    bot = await boot(
        reuse="def __init__(self, context):\n"
        "    super().__init__(context)\n"
        "    self.result = self.handled('ok')\n"
        "async def on_message(self, event):\n"
        "    return self.result\n"
    )

    first = await bot.plugin_manager.call_plugin_hook("on_message", _EVENT)
    second = await bot.plugin_manager.call_plugin_hook("on_message", _EVENT)

    assert first == second
    assert bot.plugin_manager.plugins["reuse"].result == {
        "handled": True,
        "response": "ok",
    }


def test_message_event_preserves_chat_file() -> None:
    event = message(
        {
            "id": "message-1",
            "text": "describe",
            "user": {"id": "user-1", "username": "alice"},
            "fileId": "file-1",
            "file": {
                "id": "file-1",
                "type": "image/png",
                "url": "https://example.com/image.png",
                "thumbnailUrl": "https://example.com/thumbnail.webp",
            },
        }
    )

    assert isinstance(event, MessageEvent)
    [file] = event.files
    assert (file.id, file.mime_type, file.url, file.thumbnail_url) == (
        "file-1",
        "image/png",
        "https://example.com/image.png",
        "https://example.com/thumbnail.webp",
    )


def test_mention_event_exposes_files_and_related_notes() -> None:
    def note(note_id: str, file_id: str) -> dict[str, Any]:
        return {
            "id": note_id,
            "text": f"text-{note_id}",
            "cw": "warn",
            "user": {"id": "user-2", "username": "bob", "host": "example.com"},
            "files": [{"id": file_id, "type": "application/pdf", "url": "u"}],
        }

    event = mention(
        {
            **note("note-1", "file-1"),
            "replyId": "note-2",
            "renoteId": "note-3",
            "reply": note("note-2", "file-2"),
            "renote": note("note-3", "file-3"),
        },
        "reply",
    )

    assert isinstance(event, MentionEvent)
    assert event.kind == "reply"
    assert [f.id for f in event.files] == ["file-1"]
    assert (event.reply_id, event.renote_id) == ("note-2", "note-3")
    assert event.reply is not None
    assert event.reply.text == "text-note-2"
    assert event.reply.cw == "warn"
    assert event.reply.user.handle == "bob@example.com"
    assert event.renote is not None
    assert event.renote.id == "note-3"
    assert [f.id for f in event.attachments] == ["file-1", "file-3", "file-2"]


def test_mention_event_keeps_ids_when_related_notes_are_unavailable() -> None:
    event = mention(
        {
            "id": "note-1",
            "user": {"id": "user-1", "username": "alice"},
            "replyId": "note-2",
            "reply": None,
        }
    )

    assert event.reply_id == "note-2"
    assert event.reply is None
    assert event.attachments == ()
