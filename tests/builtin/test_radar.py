from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, call

import pytest
from support.factories import plugin_context

from plugins.radar.plugin import RadarPlugin
from twipsybot.plugin import TimelineNoteEvent, UserRef

ALICE = UserRef(id="user-1", username="alice", host=None)


def make_event(
    note_id: str = "note-1", user: UserRef = ALICE, channel: str | None = "antenna"
) -> TimelineNoteEvent:
    return TimelineNoteEvent(
        id=note_id,
        text="hello",
        cw=None,
        user=user,
        channel=channel,
        files=(),
        raw={"id": note_id},
    )


def plugin_tasks() -> list[asyncio.Task[Any]]:
    return [t for t in asyncio.all_tasks() if t.get_name().startswith("plugin-Test-")]


async def settle() -> None:
    await asyncio.gather(*plugin_tasks(), return_exceptions=True)


class Radar:
    def __init__(
        self,
        config: dict[str, Any],
        note: dict[str, Any] | Exception | None = None,
        *,
        bot_id: str | None = "bot-id",
        username: str | None = "bot",
    ) -> None:
        if not isinstance(note, Exception):
            note = {"id": "note-1", **(note or {})}
        self.misskey = SimpleNamespace(
            get_note=AsyncMock(
                side_effect=note if isinstance(note, Exception) else None,
                return_value=note,
            ),
            create_reaction=AsyncMock(),
            create_note=AsyncMock(),
            create_renote=AsyncMock(),
        )
        self.openai = SimpleNamespace(
            generate_text=AsyncMock(return_value="generated"),
            system_prompt="system",
            max_tokens=100,
            temperature=0.5,
        )

        @asynccontextmanager
        async def actor_lock(user_id: str | None, handle: str | None):
            yield

        self.plugin = RadarPlugin(
            plugin_context(
                {"enabled": True, **config},
                misskey=self.misskey,
                openai=self.openai,
                bot=SimpleNamespace(
                    user_id=bot_id, username=username, actor_lock=actor_lock
                ),
            )
        )

    async def run(self, event: TimelineNoteEvent | None = None) -> None:
        await self.plugin.on_timeline_note(event or make_event())
        await settle()


@pytest.fixture(autouse=True)
def sleep(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock = AsyncMock()
    monkeypatch.setattr("plugins.radar.plugin.asyncio.sleep", mock)
    return mock


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


async def test_radar_waits_configured_delay_before_acting(sleep: AsyncMock) -> None:
    radar = Radar({"delay": "2h", "reaction": "heart"})

    await radar.run()

    sleep.assert_awaited_once_with(7200.0)
    radar.misskey.create_reaction.assert_awaited_once_with("note-1", "heart")


async def test_radar_waits_random_delay_without_configured_delay(
    sleep: AsyncMock,
) -> None:
    await Radar({}).run()

    assert 180 <= sleep.await_args_list[0].args[0] <= 300


@pytest.mark.parametrize(
    ("bot_id", "username", "event", "acted"),
    [
        pytest.param("bot-id", "bot", make_event(), True, id="other-user"),
        pytest.param(
            "bot-id",
            "bot",
            make_event(user=UserRef("bot-id", "bot", None)),
            False,
            id="same-id",
        ),
        pytest.param(
            None,
            "Bot",
            make_event(user=UserRef(None, "bot", None)),
            False,
            id="same-local-username-without-id",
        ),
        pytest.param(
            None,
            "bot",
            make_event(user=UserRef("remote-id", "bot", "remote.example")),
            True,
            id="same-username-on-remote-host",
        ),
        pytest.param(
            None,
            None,
            make_event(user=UserRef(None, "bot", None)),
            True,
            id="no-bot-name",
        ),
        pytest.param("bot-id", "bot", make_event(channel="home"), False, id="channel"),
        pytest.param("bot-id", "bot", make_event(""), False, id="missing-note-id"),
    ],
)
async def test_radar_reacts_only_to_foreign_antenna_notes(
    bot_id: str | None, username: str | None, event: TimelineNoteEvent, acted: bool
) -> None:
    radar = Radar({"reaction": "heart"}, bot_id=bot_id, username=username)

    await radar.run(event)

    assert radar.misskey.create_reaction.await_count == acted


async def test_radar_ignores_duplicate_pending_note_until_it_is_handled() -> None:
    radar = Radar({"reaction": "heart"})

    await radar.plugin.on_timeline_note(make_event())
    await radar.plugin.on_timeline_note(make_event())
    await settle()
    assert radar.misskey.create_reaction.await_count == 1

    await radar.run()
    assert radar.misskey.create_reaction.await_count == 2


async def test_radar_drops_notes_when_pending_queue_is_full(sleep: AsyncMock) -> None:
    gate = asyncio.Event()

    async def wait(_: float) -> None:
        await gate.wait()

    sleep.side_effect = wait
    radar = Radar({"reaction": "heart"})

    for index in range(101):
        await radar.plugin.on_timeline_note(make_event(f"note-{index}"))
    gate.set()
    await settle()

    assert radar.misskey.create_reaction.await_count == 100


async def test_radar_shutdown_cancels_pending_notes(sleep: AsyncMock) -> None:
    sleep.side_effect = lambda _: asyncio.Event().wait()
    radar = Radar({"reaction": "heart"})
    await radar.plugin.on_timeline_note(make_event())
    assert plugin_tasks()

    await radar.plugin.on_shutdown()

    assert not plugin_tasks()
    radar.misskey.get_note.assert_not_awaited()


@pytest.mark.parametrize(
    ("note", "reacted"),
    [
        pytest.param({}, True, id="fresh"),
        pytest.param({"myReaction": "👍"}, False, id="already-reacted"),
        pytest.param(RuntimeError("NO_SUCH_NOTE"), False, id="unavailable"),
    ],
)
async def test_radar_refetches_note_before_reacting(
    note: dict[str, Any] | Exception, reacted: bool
) -> None:
    radar = Radar({"reaction": "heart"}, note)

    await radar.run()

    radar.misskey.get_note.assert_awaited_once_with("note-1")
    assert radar.misskey.create_reaction.await_count == reacted


async def test_radar_later_actions_run_after_earlier_ones_fail() -> None:
    radar = Radar({"reaction": "heart", "reply_text": "hi", "repeat": "renote"})
    radar.misskey.create_reaction.side_effect = RuntimeError("boom")
    radar.misskey.create_note.side_effect = RuntimeError("boom")

    await radar.run()

    radar.misskey.create_renote.assert_awaited_once()


@pytest.mark.parametrize(
    ("config", "note", "expected"),
    [
        pytest.param(
            {"reply_text": "hi {username}"},
            {"user": {"username": "alice"}},
            {"text": "hi alice", "reply_id": "note-1", "local_only": False},
            id="username-template",
        ),
        pytest.param(
            {"reply_text": "hi {username}"},
            {},
            {"text": "hi unknown", "reply_id": "note-1", "local_only": False},
            id="unknown-username",
        ),
        pytest.param(
            {
                "reply_text": "hi",
                "reply_visibility": "home",
                "reply_local_only": True,
            },
            {},
            {
                "text": "hi",
                "visibility": "home",
                "reply_id": "note-1",
                "local_only": True,
            },
            id="configured-visibility",
        ),
        pytest.param(
            {"reply_text": "hi", "reply_visibility": "home"},
            {"visibility": "specified"},
            {
                "text": "hi",
                "visibility": "specified",
                "reply_id": "note-1",
                "local_only": False,
            },
            id="specified-note-keeps-visibility",
        ),
        pytest.param({"reply": False, "reply_text": "hi"}, {}, None, id="disabled"),
        pytest.param({"reply_text": None}, {}, None, id="no-text"),
        pytest.param({"reply_text": "  "}, {}, None, id="blank-text"),
    ],
)
async def test_radar_reply_text_and_visibility(
    config: dict[str, Any], note: dict[str, Any], expected: dict[str, Any] | None
) -> None:
    radar = Radar({"reply": True, **config}, note)

    await radar.run()

    if expected is None:
        radar.misskey.create_note.assert_not_awaited()
    else:
        radar.misskey.create_note.assert_awaited_once_with(**expected)


async def test_radar_reply_ai_uses_configured_prompt_and_model_settings() -> None:
    radar = Radar(
        {"reply": True, "reply_ai": True, "reply_ai_prompt": "Reply:\n{content}"},
        {"text": "hello"},
    )

    await radar.run()

    radar.openai.generate_text.assert_awaited_once_with(
        "Reply:\nhello", "system", max_tokens=100, temperature=0.5
    )
    radar.misskey.create_note.assert_awaited_once_with(
        text="generated", reply_id="note-1", local_only=False
    )


@pytest.mark.parametrize("prompt", ("", " "))
async def test_radar_ai_with_blank_prompt_still_receives_note_text(
    prompt: str,
) -> None:
    radar = Radar(
        {
            "reply": True,
            "reply_ai": True,
            "reply_ai_prompt": prompt,
            "repeat": "quote",
            "quote_ai": True,
            "quote_ai_prompt": prompt,
        },
        {"text": "hello"},
    )

    await radar.run()

    prompts = [c.args[0] for c in radar.openai.generate_text.await_args_list]
    assert len(prompts) == 2
    assert all(p != "hello" and "hello" in p for p in prompts)


async def test_radar_ai_content_includes_cw_text_and_nested_renotes() -> None:
    radar = Radar(
        {"reply": True, "reply_ai": True, "reply_ai_prompt": "{content}"},
        {
            "cw": "warn",
            "text": " body ",
            "renote": {"text": "quoted", "renote": {"cw": "deep"}},
        },
    )

    await radar.run()

    assert radar.openai.generate_text.await_args.args[0] == "warn\nbody\nquoted\ndeep"


async def test_radar_static_text_takes_precedence_over_ai() -> None:
    radar = Radar(
        {
            "reply": True,
            "reply_text": "reply",
            "reply_ai": True,
            "repeat": "quote",
            "quote_text": "quote",
            "quote_ai": True,
        },
        {"text": "hello"},
    )

    await radar.run()

    radar.openai.generate_text.assert_not_awaited()


@pytest.mark.parametrize(
    ("note", "reply", "failure"),
    [
        pytest.param({"text": "hello"}, "  ", None, id="blank-reply"),
        pytest.param({"text": "hello"}, None, RuntimeError("ai down"), id="ai-error"),
        pytest.param({"text": "  "}, None, None, id="note-without-text"),
    ],
)
async def test_radar_ai_without_text_skips_reply_and_quotes_as_plain_renote(
    note: dict[str, Any], reply: str | None, failure: Exception | None
) -> None:
    radar = Radar(
        {"reply": True, "reply_ai": True, "repeat": "quote", "quote_ai": True}, note
    )
    radar.openai.generate_text.side_effect = failure
    radar.openai.generate_text.return_value = reply

    await radar.run()

    radar.misskey.create_note.assert_not_awaited()
    radar.misskey.create_renote.assert_awaited_once_with(
        "note-1", visibility=None, local_only=False
    )


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        pytest.param(
            {
                "repeat": "renote",
                "renote_visibility": "home",
                "renote_local_only": True,
                "quote_text": "quote",
            },
            [call("note-1", visibility="home", local_only=True)],
            id="renote",
        ),
        pytest.param(
            {"repeat": "quote", "quote_text": "quote", "quote_visibility": "followers"},
            [call("note-1", visibility="followers", text="quote", local_only=False)],
            id="quote",
        ),
        pytest.param(
            {"repeat": "quote", "quote_ai": True},
            [call("note-1", visibility=None, text="generated", local_only=False)],
            id="quote-with-ai",
        ),
        pytest.param(
            {
                "repeat": "quote",
                "quote_visibility": "home",
                "quote_local_only": True,
            },
            [call("note-1", visibility="home", local_only=True)],
            id="quote-without-text-falls-back-to-renote",
        ),
        pytest.param({"repeat": "off", "quote_text": "quote"}, [], id="off"),
    ],
)
async def test_radar_repeat_mode(config: dict[str, Any], expected: list[Any]) -> None:
    radar = Radar(config, {"text": "hello"})

    await radar.run()

    assert radar.misskey.create_renote.await_args_list == expected


async def test_radar_quote_failure_falls_back_to_plain_renote() -> None:
    radar = Radar({"repeat": "quote", "quote_text": "quote"})
    radar.misskey.create_renote.side_effect = [RuntimeError("boom"), {}]

    await radar.run()

    assert radar.misskey.create_renote.await_args_list == [
        call("note-1", visibility=None, text="quote", local_only=False),
        call("note-1", visibility=None, local_only=False),
    ]
