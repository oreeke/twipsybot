from __future__ import annotations

from typing import Any

import pytest
from support.events import mention, message, notification, timeline_note
from support.types import MakeBot, MakePluginDir, WriteConfig

from twipsybot.plugin import (
    MentionEvent,
    MessageEvent,
)
from twipsybot.plugin.manager import PluginManager, _valid_auto_post, valid_handled


@pytest.mark.parametrize(
    ("result", "valid"),
    [
        ({"handled": True, "response": "ok"}, True),
        ({"handled": True, "response": "ok", "file_id": "f1"}, True),
        ({"handled": True, "response": "ok", "file_id": ""}, False),
        ({"handled": True, "response": "ok", "file_id": ["f1"]}, False),
        ({"handled": True, "response": "ok", "followup": lambda: None}, True),
        ({"handled": True, "response": "ok", "followup": "later"}, False),
        ({"handled": True, "response": "ok", "extra": 1}, False),
        ({"handled": True}, False),
    ],
)
def test_handled_result_validation(result: dict[str, Any], valid: bool) -> None:
    assert valid_handled(result) is valid


@pytest.mark.parametrize(
    ("contents", "valid"),
    [
        (["text"], True),
        ([{"text": "text"}], True),
        (["a", {"text": "b", "file_ids": ["f1", "f2"]}], True),
        ([{"text": "b", "file_ids": [f"f{i}" for i in range(17)]}], False),
        ([{"text": "b", "file_ids": ["f1", "f1"]}], False),
        ([{"text": "b", "file_ids": [""]}], False),
        ([{"text": "b", "file_ids": "f1"}], False),
        ([{"text": " "}], False),
        ([{"file_ids": ["f1"]}], False),
        ([{"text": "b", "extra": 1}], False),
        ([1], False),
    ],
)
def test_auto_post_contents_validation(contents: list[Any], valid: bool) -> None:
    assert _valid_auto_post({"contents": contents}) is valid


async def test_all_hooks_receive_stable_events(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir(
        "events",
        "from twipsybot.plugin import (\n"
        "    AutoPostEvent, MentionEvent, MessageEvent, NotificationEvent,\n"
        "    PLUGIN_API_VERSION, PluginBase, TimelineNoteEvent,\n"
        ")\n\n"
        "class EventsPlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n\n"
        "    def __init__(self, context):\n"
        "        super().__init__(context)\n"
        "        self.received = []\n\n"
        "    async def on_message(self, event: MessageEvent):\n"
        "        self.received.append((type(event), event.id, event.room_id))\n\n"
        "    async def on_mention(self, event: MentionEvent):\n"
        "        self.received.append((type(event), event.id, event.text))\n\n"
        "    async def on_notification(self, event: NotificationEvent):\n"
        "        self.received.append((type(event), event.id, event.type))\n\n"
        "    async def on_timeline_note(self, event: TimelineNoteEvent):\n"
        "        self.received.append((type(event), event.id, event.channel))\n\n"
        "    async def on_auto_post(self, event: AutoPostEvent):\n"
        "        self.received.append((type(event), bool(event.triggered_at), None))\n\n"
        "plugin = EventsPlugin\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
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

    plugin = manager.plugins.get("events")
    assert plugin is not None
    assert [(item[0].__name__, item[1], item[2]) for item in plugin.received] == [
        ("MessageEvent", "message-1", "room-1"),
        ("MentionEvent", "mention-1", "hi"),
        ("NotificationEvent", "notification-1", "reaction"),
        ("TimelineNoteEvent", "note-1", "antenna"),
        ("AutoPostEvent", True, None),
    ]


async def test_invalid_hook_results_are_rejected(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir(
        "invalid",
        body="async def on_message(self, event):\n"
        "    return {'handled': True, 'response': 42}\n"
        "async def on_auto_post(self, event):\n"
        "    return {'contents': []}\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    assert (
        await bot.plugin_manager.call_plugin_hook(
            "on_message", message({"id": "message-1", "user": {"username": "a"}})
        )
        == []
    )
    assert await bot.plugin_manager.call_plugin_hook("on_auto_post") == []
    plugin = bot.plugin_manager.plugins.get("invalid")
    assert plugin is not None

    async def invalid_visibility(event):
        return {"contents": ["x"], "visibility": []}

    plugin.on_auto_post = invalid_visibility
    assert await bot.plugin_manager.call_plugin_hook("on_auto_post") == []


async def test_event_raw_is_isolated_between_plugins(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir(
        "mutator",
        body="async def on_message(self, event):\n"
        "    event.raw['user']['username'] = 'changed'\n",
    )
    make_plugin_dir(
        "observer",
        body="async def on_message(self, event):\n"
        "    return self.handled(event.raw['user']['username'])\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
    payload = {"id": "message-1", "user": {"username": "original"}}

    results = await bot.plugin_manager.call_plugin_hook("on_message", message(payload))

    assert results[0][1]["response"] == "original"
    assert payload["user"]["username"] == "original"


async def test_reused_result_is_not_mutated(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir(
        "reuse",
        body="def __init__(self, context):\n"
        "    super().__init__(context)\n"
        "    self.result = self.handled('ok')\n"
        "async def on_message(self, event):\n"
        "    return self.result\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
    event = message({"id": "message-1", "user": {"username": "alice"}})

    first = await bot.plugin_manager.call_plugin_hook("on_message", event)
    second = await bot.plugin_manager.call_plugin_hook("on_message", event)

    assert first == second
    plugin = bot.plugin_manager.plugins.get("reuse")
    assert plugin is not None
    assert plugin.result == {"handled": True, "response": "ok"}


def test_message_hook_event_preserves_chat_file() -> None:
    raw = {
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

    event = message(raw)

    assert isinstance(event, MessageEvent)
    assert len(event.files) == 1
    assert event.files[0].id == "file-1"
    assert event.files[0].mime_type == "image/png"
    assert event.files[0].url == "https://example.com/image.png"
    assert event.files[0].thumbnail_url == "https://example.com/thumbnail.webp"


def test_mention_hook_event_preserves_note_files() -> None:
    raw = {
        "id": "note-1",
        "text": "@testbot describe",
        "user": {"id": "user-1", "username": "alice"},
        "fileIds": ["file-1"],
        "files": [
            {
                "id": "file-1",
                "type": "image/jpeg",
                "url": "https://example.com/image.jpg",
            }
        ],
    }

    event = mention(raw)

    assert isinstance(event, MentionEvent)
    assert len(event.files) == 1
    assert event.files[0].id == "file-1"
    assert event.files[0].mime_type == "image/jpeg"
    assert event.files[0].url == "https://example.com/image.jpg"
    assert event.reply is None
    assert event.renote is None
    assert event.attachments == event.files


def test_mention_hook_event_exposes_related_notes() -> None:
    def note(note_id: str, file_id: str) -> dict[str, Any]:
        return {
            "id": note_id,
            "text": f"text-{note_id}",
            "cw": "warn",
            "user": {"id": "user-2", "username": "bob", "host": "example.com"},
            "files": [{"id": file_id, "type": "application/pdf", "url": "u"}],
        }

    raw = {
        **note("note-1", "file-1"),
        "replyId": "note-2",
        "renoteId": "note-3",
        "reply": note("note-2", "file-2"),
        "renote": note("note-3", "file-3"),
    }

    event = mention(raw, "reply")

    assert isinstance(event, MentionEvent)
    assert event.kind == "reply"
    assert (event.reply_id, event.renote_id) == ("note-2", "note-3")
    assert event.reply is not None
    assert event.reply.text == "text-note-2"
    assert event.reply.cw == "warn"
    assert event.reply.user.handle == "bob@example.com"
    assert event.renote is not None
    assert event.renote.id == "note-3"
    assert [f.id for f in event.attachments] == ["file-1", "file-3", "file-2"]


def test_mention_hook_event_keeps_ids_when_related_notes_are_unavailable() -> None:
    raw = {
        "id": "note-1",
        "user": {"id": "user-1", "username": "alice"},
        "replyId": "note-2",
        "reply": None,
    }

    event = mention(raw)

    assert isinstance(event, MentionEvent)
    assert event.reply_id == "note-2"
    assert event.reply is None
    assert event.attachments == ()


@pytest.mark.parametrize(
    ("result", "valid"),
    [
        ({"context": "c"}, True),
        ({"text": "t"}, True),
        ({"context": "c", "text": "t"}, True),
        ({"media": [{"type": "input_image", "image_url": "u"}]}, True),
        ({"context": "c", "media": [{"type": "input_text"}]}, True),
        ({"media": []}, False),
        ({"media": [{"image_url": "u"}]}, False),
        ({"media": "image"}, False),
        ({}, False),
        ({"context": " "}, False),
        ({"context": "c", "extra": "x"}, False),
        ({"context": 1}, False),
    ],
)
def test_context_hook_result_validation(result: dict[str, Any], valid: bool) -> None:
    assert PluginManager._validate_hook_result("on_context", result) is valid
