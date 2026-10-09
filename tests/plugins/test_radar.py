from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from conftest import plugin_context

from plugins.radar.plugin import _QUOTE_AI_PROMPT, _REPLY_AI_PROMPT, RadarPlugin
from twipsybot.plugin import TimelineNoteEvent, UserRef


async def test_radar_reacts_through_public_misskey_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("plugins.radar.plugin._DELAY_RANGE", (0.0, 0.0))
    create_reaction = AsyncMock(return_value={})
    misskey = SimpleNamespace(
        create_reaction=create_reaction,
        get_note=AsyncMock(side_effect=lambda note_id: {"id": note_id}),
    )

    @asynccontextmanager
    async def actor_lock(user_id: str | None, username: str | None):
        yield

    bot = SimpleNamespace(user_id="bot-id", username="bot", actor_lock=actor_lock)
    plugin = RadarPlugin(
        plugin_context(
            {"enabled": True, "reaction": "heart"},
            misskey=misskey,
            bot=bot,
        )
    )
    raw = {
        "id": "note-1",
        "text": "hello",
        "user": {"id": "user-1", "username": "alice"},
        "streamingChannel": "antenna",
    }
    event = TimelineNoteEvent(
        id="note-1",
        text="hello",
        cw=None,
        user=UserRef(id="user-1", username="alice", host=None),
        channel="antenna",
        files=(),
        raw=raw,
    )

    await plugin.on_timeline_note(event)
    await plugin.on_timeline_note(event)
    await asyncio.gather(*plugin._pending.values())

    create_reaction.assert_awaited_once_with("note-1", "heart")
    assert not plugin._pending

    monkeypatch.setattr("plugins.radar.plugin._DELAY_RANGE", (60.0, 60.0))
    await plugin.on_timeline_note(
        TimelineNoteEvent(
            id="note-4",
            text="hello",
            cw=None,
            user=UserRef(id="user-1", username="alice", host=None),
            channel="antenna",
            files=(),
            raw=raw,
        )
    )
    await plugin.on_shutdown()
    assert not plugin._pending

    remote_same_name = TimelineNoteEvent(
        id="note-2",
        text="hello",
        cw=None,
        user=UserRef(id="remote-id", username="bot", host="remote.example"),
        channel="antenna",
        files=(),
        raw={},
    )
    assert plugin._should_skip_self(remote_same_name) is False


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ("  ", None),
        ("1m", timedelta(minutes=1)),
        ("2H", timedelta(hours=2)),
        ("1h30m", timedelta(minutes=90)),
        ("1d", timedelta(days=1)),
        (timedelta(hours=3), timedelta(hours=3)),
    ],
)
def test_radar_delay_parsing(value: Any, expected: timedelta | None) -> None:
    plugin = RadarPlugin(plugin_context({"enabled": True, "delay": value}))

    assert plugin.settings.delay == expected


@pytest.mark.parametrize(
    "value", ("30s", "0m", "1d1m", "2d", "5", 5, True, "1x", timedelta(seconds=59))
)
def test_radar_rejects_invalid_delay(value: Any) -> None:
    context = plugin_context({"enabled": True, "delay": value})
    with pytest.raises(ValueError, match="delay must be between 1m and 1d"):
        RadarPlugin(context)


def _radar_event(note_id: str = "note-1") -> TimelineNoteEvent:
    return TimelineNoteEvent(
        id=note_id,
        text="hello",
        cw=None,
        user=UserRef(id="user-1", username="alice", host=None),
        channel="antenna",
        files=(),
        raw={"id": note_id, "text": "hello"},
    )


def _radar_plugin(config: dict[str, Any], **misskey: Any) -> RadarPlugin:
    @asynccontextmanager
    async def actor_lock(user_id: str | None, username: str | None):
        yield

    return RadarPlugin(
        plugin_context(
            {"enabled": True, "reaction": "heart", **config},
            misskey=SimpleNamespace(**misskey),
            bot=SimpleNamespace(
                user_id="bot-id", username="bot", actor_lock=actor_lock
            ),
        )
    )


async def test_radar_uses_exact_configured_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr("plugins.radar.plugin.asyncio.sleep", sleep)
    plugin = _radar_plugin({"delay": "2h"}, get_note=AsyncMock(return_value={}))

    await plugin._delayed_act(_radar_event())

    sleep.assert_awaited_once_with(7200.0)


async def test_radar_refetches_note_before_acting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("plugins.radar.plugin._DELAY_RANGE", (0.0, 0.0))
    get_note = AsyncMock(
        side_effect=[
            RuntimeError("NO_SUCH_NOTE"),
            {"id": "note-2", "myReaction": "👍"},
            {"id": "note-3"},
        ]
    )
    create_reaction = AsyncMock(return_value={})
    plugin = _radar_plugin({}, get_note=get_note, create_reaction=create_reaction)

    for note_id in ("note-1", "note-2", "note-3"):
        await plugin._delayed_act(_radar_event(note_id))

    assert [c.args for c in get_note.await_args_list] == [
        ("note-1",),
        ("note-2",),
        ("note-3",),
    ]
    create_reaction.assert_awaited_once_with("note-3", "heart")


async def test_radar_ai_uses_configured_prompt() -> None:
    generate_text = AsyncMock(return_value="generated reply")
    create_note = AsyncMock(return_value={})
    plugin = RadarPlugin(
        plugin_context(
            {
                "enabled": True,
                "reply": True,
                "reply_ai": True,
                "reply_ai_prompt": "Reply:\n{content}",
            },
            misskey=SimpleNamespace(create_note=create_note),
            openai=SimpleNamespace(
                generate_text=generate_text,
                system_prompt="system",
                max_tokens=100,
                temperature=0.5,
            ),
        )
    )

    await plugin._maybe_reply({"text": "hello"}, "note-1", "antenna")

    generate_text.assert_awaited_once_with(
        "Reply:\nhello",
        "system",
        max_tokens=100,
        temperature=0.5,
    )
    create_note.assert_awaited_once_with(
        text="generated reply", reply_id="note-1", local_only=False
    )


async def test_radar_blank_ai_prompts_use_defaults() -> None:
    generate_text = AsyncMock(return_value="generated")
    plugin = RadarPlugin(
        plugin_context(
            {
                "enabled": True,
                "reply": True,
                "reply_ai": True,
                "reply_ai_prompt": " ",
                "repeat": "quote",
                "quote_ai": True,
                "quote_ai_prompt": "",
            },
            misskey=SimpleNamespace(
                create_note=AsyncMock(return_value={}),
                create_renote=AsyncMock(return_value={}),
            ),
            openai=SimpleNamespace(
                generate_text=generate_text,
                system_prompt="",
                max_tokens=1,
                temperature=0,
            ),
        )
    )

    await plugin._maybe_reply({"text": "hello"}, "note-1", "antenna")
    await plugin._maybe_quote({"text": "hello"}, "note-1", "antenna")

    assert [c.args[0] for c in generate_text.await_args_list] == [
        _REPLY_AI_PROMPT.format(content="hello"),
        _QUOTE_AI_PROMPT.format(content="hello"),
    ]


async def test_radar_preserves_specified_reply_visibility() -> None:
    create_note = AsyncMock(return_value={})
    plugin = RadarPlugin(
        plugin_context(
            {"enabled": True, "reply": True, "reply_text": "reply"},
            misskey=SimpleNamespace(create_note=create_note),
        )
    )

    await plugin._maybe_reply(
        {"text": "hello", "visibility": "specified"}, "note-1", "antenna"
    )

    create_note.assert_awaited_once_with(
        text="reply",
        visibility="specified",
        reply_id="note-1",
        local_only=False,
    )


async def test_radar_applies_reply_visibility() -> None:
    create_note = AsyncMock(return_value={})
    plugin = RadarPlugin(
        plugin_context(
            {
                "enabled": True,
                "reply": True,
                "reply_text": "reply",
                "reply_visibility": "home",
            },
            misskey=SimpleNamespace(create_note=create_note),
        )
    )

    await plugin._maybe_reply({"text": "hello"}, "note-1", "antenna")

    create_note.assert_awaited_once_with(
        text="reply", visibility="home", reply_id="note-1", local_only=False
    )


async def test_radar_repeat_quote_falls_back_to_renote() -> None:
    misskey = SimpleNamespace(create_renote=AsyncMock(return_value={}))
    plugin = RadarPlugin(
        plugin_context(
            {
                "enabled": True,
                "repeat": "quote",
                "quote_visibility": "home",
                "quote_local_only": True,
            },
            misskey=misskey,
        )
    )

    await plugin._act({"text": "hello"}, "note-1", "antenna")

    misskey.create_renote.assert_awaited_once_with(
        "note-1", visibility="home", local_only=True
    )


async def test_radar_repeat_renotes_without_quote() -> None:
    misskey = SimpleNamespace(create_renote=AsyncMock(return_value={}))
    plugin = RadarPlugin(
        plugin_context(
            {"enabled": True, "repeat": "renote", "quote_text": "quote"},
            misskey=misskey,
        )
    )

    await plugin._act({}, "note-1", "antenna")

    misskey.create_renote.assert_awaited_once_with(
        "note-1", visibility=None, local_only=False
    )


async def test_radar_repeat_quote_does_not_renote() -> None:
    misskey = SimpleNamespace(
        create_reaction=AsyncMock(return_value={}),
        create_note=AsyncMock(return_value={}),
        create_renote=AsyncMock(return_value={}),
    )
    plugin = RadarPlugin(
        plugin_context(
            {
                "enabled": True,
                "reply": True,
                "reply_text": "hello {username}",
                "repeat": "quote",
                "quote_text": "quote",
            },
            misskey=misskey,
        )
    )

    await plugin._act({"user": {"username": "alice"}}, "note-1", "antenna")

    misskey.create_note.assert_awaited_once_with(
        text="hello alice", reply_id="note-1", local_only=False
    )
    misskey.create_renote.assert_awaited_once_with(
        "note-1", visibility=None, text="quote", local_only=False
    )


def test_radar_parses_boolean_strings() -> None:
    plugin = RadarPlugin(
        plugin_context({"enabled": "true", "reply": "false", "repeat": "quote"})
    )

    assert plugin._enabled is True
    assert plugin.settings.reply_enabled is False
    assert plugin.settings.repeat == "quote"
