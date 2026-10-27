from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.interval import IntervalTrigger
from support.fakes import DEFAULT_AI_REPLY, FakeMisskeyServer, FakeOpenAIServer
from support.types import MakeBot, MakePluginDir, WriteConfig

from twipsybot.bot.flows.post import AutoPostService
from twipsybot.shared.keys import ConfigKeys
from twipsybot.shared.settings import read_settings, write_settings


async def test_auto_post_publishes_note(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    openai_server.set_reply("今天天气不错")
    bot = await make_bot(write_config())
    bot.runtime.running = True

    await bot.auto_post.run()

    notes = misskey_server.calls["notes/create"]
    assert len(notes) == 1
    assert notes[0]["text"] == "今天天气不错"
    assert notes[0]["visibility"] == "public"
    assert bot.auto_post.posts_today == 1
    assert await bot.db.get_auto_post_state() == (bot.auto_post._today(), 1)


async def test_auto_post_restores_counter_and_resets_stale_date(
    make_bot: MakeBot, write_config: WriteConfig
) -> None:
    bot = await make_bot(write_config())
    await bot.db.set_auto_post_state(bot.auto_post._today(), 3)

    await bot.auto_post.start()
    assert bot.auto_post.posts_today == 3

    await bot.db.set_auto_post_state("2000-01-01", 7)
    await bot.auto_post.start()
    assert bot.auto_post.posts_today == 0
    assert await bot.db.get_auto_post_state() == (bot.auto_post._today(), 0)


async def test_auto_post_counter_self_corrects_after_date_change(
    make_bot: MakeBot, write_config: WriteConfig
) -> None:
    bot = await make_bot(write_config())
    bot.auto_post._post_date = "2000-01-01"
    bot.auto_post.posts_today = 7

    assert await bot.auto_post.check_post_counter(1) is True
    assert bot.auto_post.posts_today == 0
    assert await bot.db.get_auto_post_state() == (bot.auto_post._today(), 0)


async def test_auto_post_uses_first_plugin_prompt(
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
    openai_server: FakeOpenAIServer,
) -> None:
    plugins_dir = make_plugin_dir(
        "first",
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n"
        "class FirstPlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n"
        "    async def on_auto_post(self, event):\n"
        "        return {'prompt': 'first '}\n\n"
        "plugin = FirstPlugin\n",
    )
    make_plugin_dir(
        "second",
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n"
        "class SecondPlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n"
        "    async def on_auto_post(self, event):\n"
        "        return {'prompt': 'second '}\n\n"
        "plugin = SecondPlugin\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
    bot.runtime.running = True

    await bot.auto_post.run()

    prompt = openai_server.calls[0]["messages"][-1]["content"]
    assert prompt == "first 写一条随笔"


async def test_auto_post_generate_prepends_plugin_prompt(
    make_bot: MakeBot,
    write_config: WriteConfig,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(write_config())

    assert await bot.auto_post.generate("前缀 ") == DEFAULT_AI_REPLY

    messages = openai_server.calls[0]["messages"]
    assert messages[0]["content"] == "你是测试机器人"
    assert messages[-1]["content"] == "前缀 写一条随笔"


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


async def test_plugin_auto_post_can_attach_files(
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
) -> None:
    plugins_dir = make_plugin_dir(
        "album",
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n\n"
        "class AlbumPlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n\n"
        "    async def on_auto_post(self, event):\n"
        "        return {'contents': ['plain', {'text': 'pic', 'file_ids': ['f1', 'f2']}]}\n\n"
        "    async def on_auto_post_published(self, content):\n"
        "        await self.context.storage.set(content, 'done')\n\n"
        "plugin = AlbumPlugin\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
    bot.runtime.running = True
    bot.auto_post._PLUGIN_POST_INTERVAL_SECONDS = 0

    await bot.auto_post.run()

    first, second = misskey_server.calls["notes/create"]
    assert "fileIds" not in first
    assert (second["text"], second["fileIds"]) == ("pic", ["f1", "f2"])
    assert await bot.db.get_plugin_data("album", "pic") == "done"


async def test_auto_post_stops_at_daily_limit(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(write_config(autopost={"daily_max": 1}))
    bot.runtime.running = True
    bot.auto_post.posts_today = 1

    await bot.auto_post.run()

    assert openai_server.calls == []
    assert "notes/create" not in misskey_server.calls


async def test_scheduled_auto_post_ignores_daily_limit(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
) -> None:
    bot = await make_bot(
        write_config(
            autopost={
                "mode": "schedule",
                "times": ["09:00"],
                "daily_max": 0,
            }
        )
    )
    bot.runtime.running = True

    await bot.auto_post.run()
    await bot.auto_post.run()

    assert len(misskey_server.calls["notes/create"]) == 2
    assert bot.auto_post.posts_today == 0


async def test_plugin_can_modify_auto_post_prompt(
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    plugins_dir = make_plugin_dir(
        "prompt",
        "from twipsybot.plugin import AutoPostEvent, PLUGIN_API_VERSION, PluginBase\n\n\n"
        "class PromptPlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n\n"
        "    async def on_auto_post(self, event: AutoPostEvent):\n"
        "        return {'prompt': '今日主题：测试。'}\n\n"
        "plugin = PromptPlugin\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
    bot.runtime.running = True

    await bot.auto_post.run()

    prompt = openai_server.calls[0]["messages"][-1]["content"]
    assert prompt.endswith("今日主题：测试。写一条随笔")
    assert misskey_server.calls["notes/create"][0]["text"] == DEFAULT_AI_REPLY


async def test_auto_post_confirms_only_successful_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_note = AsyncMock(side_effect=[{}, RuntimeError("failed")])
    confirm = AsyncMock()
    set_auto_post_state = AsyncMock()
    bot = SimpleNamespace(
        runtime=SimpleNamespace(running=True, startup_time=None),
        db=SimpleNamespace(set_auto_post_state=set_auto_post_state),
        misskey=SimpleNamespace(create_note=create_note),
        plugin_manager=SimpleNamespace(confirm_auto_post_published=confirm),
    )
    service = AutoPostService(cast(Any, bot))
    monkeypatch.setattr(service, "_PLUGIN_POST_INTERVAL_SECONDS", 0)
    result = "Topics"

    with pytest.raises(RuntimeError, match="failed"):
        await service._post_plugin_contents(
            result, ["published", "failed"], "public", 2, False
        )

    confirm.assert_awaited_once_with(result, "published")
    set_auto_post_state.assert_awaited_once()
    assert service.posts_today == 1


async def test_auto_post_keeps_memory_count_when_persistence_fails() -> None:
    set_auto_post_state = AsyncMock(side_effect=RuntimeError("database failed"))
    service = AutoPostService(
        cast(
            Any,
            SimpleNamespace(
                db=SimpleNamespace(set_auto_post_state=set_auto_post_state)
            ),
        )
    )

    with pytest.raises(RuntimeError, match="database failed"):
        await service.post_count()

    assert service.posts_today == 1


async def test_auto_post_suppresses_cancellation_during_shutdown() -> None:
    bot = SimpleNamespace(
        config=SimpleNamespace(
            get=lambda key, default=None: {
                ConfigKeys.POST_MODE: "rotation",
                ConfigKeys.POST_DAILY_MAX: 1,
                ConfigKeys.POST_LOCAL_ONLY: False,
            }.get(key, default)
        ),
        runtime=SimpleNamespace(running=True),
    )

    async def cancel_during_shutdown(*args: Any) -> None:
        bot.runtime.running = False
        raise asyncio.CancelledError

    bot.plugin_manager = SimpleNamespace(call_plugin_hook=cancel_during_shutdown)

    await AutoPostService(cast(Any, bot)).run()


async def test_auto_post_propagates_cancellation_while_running() -> None:
    plugin_hook = AsyncMock(side_effect=asyncio.CancelledError)
    bot = SimpleNamespace(
        config=SimpleNamespace(
            get=lambda key, default=None: {
                ConfigKeys.POST_MODE: "rotation",
                ConfigKeys.POST_DAILY_MAX: 1,
                ConfigKeys.POST_LOCAL_ONLY: False,
            }.get(key, default)
        ),
        runtime=SimpleNamespace(running=True),
        plugin_manager=SimpleNamespace(call_plugin_hook=plugin_hook),
    )
    service = AutoPostService(cast(Any, bot))

    with pytest.raises(asyncio.CancelledError):
        await service.run()


async def test_auto_post_schedule_follows_settings(
    make_bot: MakeBot, write_config: WriteConfig
) -> None:
    bot = await make_bot(write_config())
    bot.auto_post.apply_schedule(initial=True)
    job = bot.scheduler.get_job("auto_post")
    assert isinstance(job.trigger, IntervalTrigger)
    assert job.trigger.interval.total_seconds() == 10800

    settings = read_settings(bot.config.settings_path)
    settings["autopost"] = {
        "mode": "schedule",
        "times": ["21:30", "08:00"],
    }
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
