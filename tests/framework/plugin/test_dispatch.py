from __future__ import annotations

import asyncio

import pytest
from support.events import message
from support.types import MakeBot, MakePluginDir, WriteConfig


async def test_handled_result_short_circuits_later_plugins(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir(
        "second",
        body="async def on_message(self, event):\n"
        "    await self.context.storage.set('called', 'yes')\n"
        "    return self.handled('second')\n",
    )
    make_plugin_dir(
        "first",
        body="async def on_message(self, event):\n    return self.handled('first')\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    results = await bot.plugin_manager.call_plugin_hook(
        "on_message", message({"id": "message-1", "user": {"username": "a"}})
    )

    assert results == [("first", {"handled": True, "response": "first"})]
    assert await bot.db.get_plugin_data("second", "called") is None


async def test_hook_timeout_and_exception_are_isolated(
    monkeypatch: pytest.MonkeyPatch,
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
) -> None:
    import twipsybot.plugin.manager as manager_module

    monkeypatch.setattr(manager_module, "_PLUGIN_HOOK_TIMEOUT_SECONDS", 0.01)
    plugins_dir = make_plugin_dir(
        "broken",
        body="async def on_message(self, event):\n"
        "    import asyncio\n"
        "    await asyncio.sleep(1)\n",
    )
    make_plugin_dir(
        "error",
        body="async def on_message(self, event):\n    raise RuntimeError('boom')\n",
    )
    make_plugin_dir(
        "healthy",
        body="async def on_message(self, event):\n    return self.handled('ok')\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    results = await bot.plugin_manager.call_plugin_hook(
        "on_message", message({"id": "message-1", "user": {"username": "a"}})
    )

    assert results == [("healthy", {"handled": True, "response": "ok"})]


async def test_auto_post_plugins_share_trigger_time(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir(
        "first",
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n"
        "class FirstPlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n\n"
        "plugin = FirstPlugin\n",
    )
    make_plugin_dir(
        "second",
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n"
        "class SecondPlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n\n"
        "plugin = SecondPlugin\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
    timestamps = []

    async def capture(event):
        timestamps.append(event.triggered_at)

    first = bot.plugin_manager.plugins.get("first")
    second = bot.plugin_manager.plugins.get("second")
    assert first is not None
    assert second is not None
    first.on_auto_post = capture
    second.on_auto_post = capture

    await bot.plugin_manager.call_plugin_hook("on_auto_post")

    assert len(timestamps) == 2
    assert timestamps[0] == timestamps[1]


async def test_actor_lock_does_not_reenter_response_pipeline(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir(
        "actor",
        body="async def on_message(self, event):\n"
        "    async with self.context.bot.actor_lock(event.user.id, event.user.username):\n"
        "        return self.handled('ok')\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    results = await asyncio.wait_for(
        bot.plugin_manager.call_plugin_hook(
            "on_message",
            message({"id": "message-1", "user": {"id": "user-1", "username": "alice"}}),
        ),
        timeout=0.2,
    )

    assert results == [("actor", {"handled": True, "response": "ok"})]
