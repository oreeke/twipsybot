from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import nullcontext
from datetime import datetime
from itertools import count
from typing import Any
from unittest.mock import AsyncMock

import pytest
from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.interval import IntervalTrigger
from support.fakes import DEFAULT_AI_REPLY, FakeMisskeyServer, FakeOpenAIServer
from support.types import MakeBot, MakePluginDir, WriteConfig

from twipsybot.shared.settings import read_settings, write_settings


async def test_auto_post_publishes_note_and_counts_it(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
    today: str,
) -> None:
    openai_server.set_reply("今天天气不错")
    bot = await make_bot(write_config())
    bot.runtime.running = True

    await bot.auto_post.run()

    [note] = misskey_server.calls["notes/create"]
    assert note["text"] == "今天天气不错"
    assert note["visibility"] == "public"
    assert bot.auto_post.posts_today == 1
    assert await bot.db.get_auto_post_state() == (today, 1)


async def test_auto_post_restores_counter_and_resets_stale_date(
    make_bot: MakeBot, write_config: WriteConfig, today: str
) -> None:
    bot = await make_bot(write_config())
    await bot.db.set_auto_post_state(today, 3)

    await bot.auto_post.start()
    assert bot.auto_post.posts_today == 3

    await bot.db.set_auto_post_state("2000-01-01", 7)
    await bot.auto_post.start()
    assert bot.auto_post.posts_today == 0
    assert await bot.db.get_auto_post_state() == (today, 0)


async def test_auto_post_counter_resets_after_date_change(
    make_bot: MakeBot, write_config: WriteConfig, next_day: Callable[[], str]
) -> None:
    bot = await make_bot(write_config())
    await bot.auto_post.post_count()
    assert bot.auto_post.daily_post_count == 1

    tomorrow = next_day()

    assert bot.auto_post.daily_post_count == 0
    assert await bot.auto_post.check_post_counter(1) is True
    assert bot.auto_post.posts_today == 0
    assert await bot.db.get_auto_post_state() == (tomorrow, 0)


async def test_auto_post_keeps_memory_count_when_persistence_fails(
    make_bot: MakeBot, write_config: WriteConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = await make_bot(write_config())
    failing = AsyncMock(side_effect=RuntimeError("database failed"))
    monkeypatch.setattr(bot.db, "set_auto_post_state", failing)

    with pytest.raises(RuntimeError, match="database failed"):
        await bot.auto_post.post_count()

    assert bot.auto_post.posts_today == 1


async def test_auto_post_uses_first_plugin_prompt(
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    for name in ("first", "second"):
        plugins_dir = make_plugin_dir(
            name,
            body=(
                "async def on_auto_post(self, event):\n"
                f"    return {{'prompt': '{name} '}}\n"
            ),
        )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
    bot.runtime.running = True

    await bot.auto_post.run()

    assert openai_server.calls[0]["messages"][-1]["content"] == "first 写一条随笔"
    assert misskey_server.calls["notes/create"][0]["text"] == DEFAULT_AI_REPLY


async def test_auto_post_without_prompt_is_skipped(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(write_config(autopost={"prompt": ""}))
    bot.runtime.running = True

    with pytest.raises(ValueError, match="Missing prompt"):
        await bot.auto_post.generate()
    await bot.auto_post.run()

    assert not openai_server.calls
    assert "notes/create" not in misskey_server.calls
    assert bot.auto_post.posts_today == 0


@pytest.mark.usefixtures("no_post_interval")
async def test_plugin_auto_post_attaches_files_and_confirms_publication(
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
) -> None:
    plugins_dir = make_plugin_dir(
        "album",
        body=(
            "async def on_auto_post(self, event):\n"
            "    return {'contents': ['plain', {'text': 'pic', 'file_ids': ['f1', 'f2']}]}\n\n"
            "async def on_auto_post_published(self, content):\n"
            "    await self.context.storage.set(content, 'done')\n"
        ),
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
    bot.runtime.running = True

    await bot.auto_post.run()

    first, second = misskey_server.calls["notes/create"]
    assert "fileIds" not in first
    assert (second["text"], second["fileIds"]) == ("pic", ["f1", "f2"])
    assert await bot.db.get_plugin_data("album", "plain") == "done"
    assert await bot.db.get_plugin_data("album", "pic") == "done"


@pytest.mark.usefixtures("no_post_interval")
async def test_plugin_auto_post_confirms_only_successful_publish(
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
) -> None:
    created = count(1)

    def create(payload: dict[str, Any]) -> dict[str, Any]:
        if next(created) == 2:
            raise RuntimeError("failed")
        return {"createdNote": {"id": "note-1"}}

    misskey_server.set_response("notes/create", create)
    plugins_dir = make_plugin_dir(
        "album",
        body=(
            "async def on_auto_post(self, event):\n"
            "    return {'contents': ['published', 'failed']}\n\n"
            "async def on_auto_post_published(self, content):\n"
            "    await self.context.storage.set(content, 'done')\n"
        ),
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
    bot.runtime.running = True

    await bot.auto_post.run()

    assert await bot.db.get_plugin_data("album", "published") == "done"
    assert await bot.db.get_plugin_data("album", "failed") is None
    assert bot.auto_post.posts_today == 1


async def test_auto_post_stops_at_daily_limit(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(write_config(autopost={"daily_max": 1}))
    bot.runtime.running = True
    await bot.auto_post.post_count()

    await bot.auto_post.run()

    assert openai_server.calls == []
    assert "notes/create" not in misskey_server.calls


async def test_scheduled_auto_post_ignores_daily_limit(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
) -> None:
    bot = await make_bot(
        write_config(autopost={"mode": "schedule", "times": ["09:00"], "daily_max": 0})
    )
    bot.runtime.running = True

    await bot.auto_post.run()
    await bot.auto_post.run()

    assert len(misskey_server.calls["notes/create"]) == 2
    assert bot.auto_post.posts_today == 0


@pytest.mark.parametrize("shutting_down", [True, False])
async def test_auto_post_cancellation_is_suppressed_only_during_shutdown(
    shutting_down: bool,
    make_bot: MakeBot,
    write_config: WriteConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = await make_bot(write_config())
    bot.runtime.running = True

    async def cancel(*args: Any) -> None:
        bot.runtime.running = not shutting_down
        raise asyncio.CancelledError

    monkeypatch.setattr(bot.plugin_manager, "call_plugin_hook", cancel)

    with nullcontext() if shutting_down else pytest.raises(asyncio.CancelledError):
        await bot.auto_post.run()


async def test_auto_post_schedule_follows_settings(
    make_bot: MakeBot, write_config: WriteConfig
) -> None:
    bot = await make_bot(write_config())
    bot.auto_post.apply_schedule(initial=True)
    job = bot.scheduler.get_job("auto_post")
    assert isinstance(job.trigger, IntervalTrigger)
    assert job.trigger.interval.total_seconds() == 10800

    settings = read_settings(bot.config.settings_path)
    settings["autopost"] = {"mode": "schedule", "times": ["21:30", "08:00"]}
    write_settings(bot.config.settings_path, settings)
    await bot.settings.reload()

    trigger = bot.scheduler.get_job("auto_post").trigger
    assert isinstance(trigger, OrTrigger)
    now = datetime(2026, 10, 3, 9, 0, tzinfo=trigger.triggers[0].timezone)
    fire = trigger.get_next_fire_time(None, now)
    assert fire is not None
    assert (fire.hour, fire.minute) == (21, 30)
    assert bot.auto_post.mode == "schedule"

    settings["autopost"] = {"mode": "off"}
    write_settings(bot.config.settings_path, settings)
    await bot.settings.reload()

    assert bot.scheduler.get_job("auto_post") is None
    assert bot.auto_post.mode == "off"
