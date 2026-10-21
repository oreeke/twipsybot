from __future__ import annotations

import asyncio
from typing import Any

import pytest
from support.events import message
from support.factories import set_plugin_config
from support.types import MakeBot, MakePluginDir, WriteConfig

_RELOAD_BODY = (
    "from twipsybot.plugin import PluginConfig\n"
    "class Settings(PluginConfig):\n"
    "    reply: str = 'default'\n"
    "config_class = Settings\n"
    "def __init__(self, context):\n"
    "    super().__init__(context)\n"
    "    self.events = ['init']\n"
    "async def initialize(self):\n"
    "    self.events.append('initialize')\n"
    "    return True\n"
    "async def on_startup(self):\n"
    "    self.events.append('startup')\n"
    "async def on_shutdown(self):\n"
    "    self.events.append('shutdown')\n"
    "async def cleanup(self):\n"
    "    self.events.append('cleanup')\n"
    "async def on_message(self, event):\n"
    "    return self.handled(self.settings.reply)\n"
)
_MESSAGE = message({"id": "_MESSAGE-1", "user": {"username": "alice"}})


async def test_lifecycle_order(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir(
        "lifecycle",
        body="def __init__(self, context):\n"
        "    super().__init__(context)\n"
        "    self.events = ['init']\n"
        "async def initialize(self):\n"
        "    self.events.append('initialize')\n"
        "    return True\n"
        "async def on_startup(self):\n"
        "    self.events.append('startup')\n"
        "async def on_message(self, event):\n"
        "    self.events.append('hook')\n"
        "async def on_shutdown(self):\n"
        "    self.events.append('shutdown')\n"
        "async def cleanup(self):\n"
        "    self.events.append('cleanup')\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
    plugin = bot.plugin_manager.get_plugin("lifecycle")
    assert plugin is not None

    await bot.plugin_manager.call_plugin_hook("on_message", _MESSAGE)
    await bot.plugin_manager.shutdown_plugins()
    await bot.plugin_manager.cleanup_plugins()

    assert plugin.events == [
        "init",
        "initialize",
        "startup",
        "hook",
        "shutdown",
        "cleanup",
    ]


async def test_initialize_timeout_runs_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
) -> None:
    import twipsybot.plugin.manager as manager_module

    monkeypatch.setattr(manager_module, "_PLUGIN_LIFECYCLE_TIMEOUT_SECONDS", 0.01)
    plugins_dir = make_plugin_dir(
        "slow",
        body="async def initialize(self):\n"
        "    import asyncio\n"
        "    await asyncio.sleep(1)\n"
        "    return True\n"
        "async def cleanup(self):\n"
        "    self.cleaned = True\n",
    )

    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
    plugin = bot.plugin_manager.get_plugin("slow")

    assert plugin is not None
    assert bot.plugin_manager.get_plugin_states() == {"slow": False}


async def test_failed_plugin_initialization_runs_cleanup(
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
) -> None:
    plugins_dir = make_plugin_dir(
        "failing",
        body="async def initialize(self):\n"
        "    return False\n"
        "async def cleanup(self):\n"
        "    await self.context.storage.set('cleaned', 'yes')\n",
    )

    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    plugin = bot.plugin_manager.get_plugin("failing")
    assert plugin is not None
    assert bot.plugin_manager.get_plugin_states() == {"failing": False}


async def test_non_bool_initialize_and_startup_failure_cleanup(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir(
        "nonbool",
        body="async def initialize(self):\n"
        "    return 'yes'\n"
        "async def cleanup(self):\n"
        "    self.cleaned = True\n",
    )
    make_plugin_dir(
        "startup",
        body="async def on_startup(self):\n"
        "    raise RuntimeError('boom')\n"
        "async def cleanup(self):\n"
        "    self.cleaned = True\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
    nonbool = bot.plugin_manager.get_plugin("nonbool")
    startup = bot.plugin_manager.get_plugin("startup")
    assert nonbool is not None
    assert startup is not None

    assert bot.plugin_manager.get_plugin_states() == {
        "nonbool": False,
        "startup": False,
    }
    assert nonbool.cleaned is True
    assert startup.cleaned is True


async def test_shutdown_stops_hook_dispatch(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir(
        "shutdown",
        body="async def on_message(self, event):\n"
        "    raise AssertionError('must not run')\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    await bot.plugin_manager.shutdown_plugins()

    assert await bot.plugin_manager.call_plugin_hook("on_message", _MESSAGE) == []


async def test_shutdown_waits_for_active_hook_and_rejects_new_hooks(
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugins_dir = make_plugin_dir("shutdown")
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
    plugin = bot.plugin_manager.get_plugin("shutdown")
    assert plugin is not None
    hook_started = asyncio.Event()
    release_hook = asyncio.Event()
    events: list[str] = []

    async def on_message(_: Any) -> None:
        events.append("hook-started")
        hook_started.set()
        await release_hook.wait()
        events.append("hook-finished")

    async def on_shutdown() -> None:
        events.append("shutdown")

    monkeypatch.setattr(plugin, "on_message", on_message, raising=False)
    monkeypatch.setattr(plugin, "on_shutdown", on_shutdown, raising=False)
    active_hook = asyncio.create_task(
        bot.plugin_manager.call_plugin_hook("on_message", _MESSAGE)
    )
    await hook_started.wait()
    shutdown = asyncio.create_task(bot.plugin_manager.shutdown_plugins())
    async with asyncio.timeout(1):
        while bot.plugin_manager._accepting_hooks:
            await asyncio.sleep(0)

    assert await bot.plugin_manager.call_plugin_hook("on_message", _MESSAGE) == []
    assert not shutdown.done()
    assert events == ["hook-started"]

    release_hook.set()
    await asyncio.gather(active_hook, shutdown)

    assert events == ["hook-started", "hook-finished", "shutdown"]


async def test_shutdown_cancels_hook_after_grace_period(
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import twipsybot.plugin.manager as manager_module

    monkeypatch.setattr(manager_module, "_PLUGIN_SHUTDOWN_GRACE_SECONDS", 0.01)
    plugins_dir = make_plugin_dir("shutdown")
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
    plugin = bot.plugin_manager.get_plugin("shutdown")
    assert plugin is not None
    hook_started = asyncio.Event()
    hook_cancelled = asyncio.Event()

    async def on_message(_: Any) -> None:
        hook_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            hook_cancelled.set()
            raise

    monkeypatch.setattr(plugin, "on_message", on_message, raising=False)
    active_hook = asyncio.create_task(
        bot.plugin_manager.call_plugin_hook("on_message", _MESSAGE)
    )
    await hook_started.wait()

    await bot.plugin_manager.shutdown_plugins()

    assert active_hook.cancelled()
    assert hook_cancelled.is_set()


async def test_reload_plugin_replaces_instance_with_new_config(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir("reload", body=_RELOAD_BODY)
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
    manager = bot.plugin_manager
    old = manager.get_plugin("reload")
    assert old is not None
    await old.context.storage.set("kept", "yes")
    set_plugin_config(bot.config.root, "reload", {"enabled": True, "reply": "updated"})

    assert await bot.settings.reload() == {"reload": "enabled"}

    new = manager.get_plugin("reload")
    assert new is not None
    assert new is not old
    assert type(new) is type(old)
    assert old.events == ["init", "initialize", "startup", "shutdown", "cleanup"]
    assert new.events == ["init", "initialize", "startup"]
    assert await new.context.storage.get("kept") == "yes"
    assert await manager.call_plugin_hook("on_message", _MESSAGE) == [
        ("reload", {"handled": True, "response": "updated"})
    ]


async def test_reload_plugin_failure_disables_only_that_plugin(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir(
        "broken", body=_RELOAD_BODY, config="enabled: true\npriority: 20\n"
    )
    make_plugin_dir(
        "healthy",
        body="async def on_message(self, event):\n    return self.handled('ok')\n",
        config="enabled: true\npriority: 10\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
    manager = bot.plugin_manager
    old = manager.get_plugin("broken")
    assert old is not None
    set_plugin_config(
        bot.config.root, "broken", {"enabled": True, "priority": 20, "reply": [1]}
    )

    assert await bot.settings.reload() == {"broken": "failed"}

    assert manager.get_plugin("broken") is old
    assert old.events[-2:] == ["shutdown", "cleanup"]
    assert manager.get_plugin_states() == {"broken": False, "healthy": True}
    assert await manager.call_plugin_hook("on_message", _MESSAGE) == [
        ("healthy", {"handled": True, "response": "ok"})
    ]


async def test_reload_plugin_follows_enabled_flag(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir(
        "toggle", body=_RELOAD_BODY, config="enabled: false\n"
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
    manager = bot.plugin_manager
    root = bot.config.root
    assert manager.get_plugin("toggle") is None

    set_plugin_config(root, "toggle", {"enabled": True})
    assert await bot.settings.reload() == {"toggle": "enabled"}
    plugin = manager.get_plugin("toggle")
    assert plugin is not None
    assert plugin._started

    set_plugin_config(root, "toggle", None)
    assert await bot.settings.reload() == {"toggle": "disabled"}
    assert plugin.events[-2:] == ["shutdown", "cleanup"]
    assert manager.get_plugin_states() == {"toggle": False}
    assert await manager.call_plugin_hook("on_message", _MESSAGE) == []

    set_plugin_config(root, "toggle", {"enabled": True})
    assert await bot.settings.reload() == {"toggle": "enabled"}
    reloaded = manager.get_plugin("toggle")
    assert reloaded is not None
    assert type(reloaded) is type(plugin)


async def test_settings_reload_only_restarts_changed_plugins(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir("first", body=_RELOAD_BODY)
    make_plugin_dir("second", body=_RELOAD_BODY)
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
    manager = bot.plugin_manager
    second = manager.get_plugin("second")
    set_plugin_config(bot.config.root, "first", {"enabled": True, "reply": "new"})

    assert await bot.settings.reload() == {"first": "enabled"}
    assert manager.get_plugin("second") is second
    assert await bot.settings.reload() == {}


@pytest.mark.parametrize("name", ("missing", "../toggle", "."))
async def test_reload_plugin_rejects_unknown_names(
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
    name: str,
) -> None:
    plugins_dir = make_plugin_dir("toggle", body=_RELOAD_BODY)
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    assert await bot.plugin_manager.reload_plugin(name) == "unknown"


async def test_reload_plugin_drains_active_hook_before_shutdown(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir(
        "slow",
        body=_RELOAD_BODY.replace(
            "async def on_message(self, event):\n",
            "async def on_message(self, event):\n"
            "    self.events.append('hook')\n"
            "    await self.release.wait()\n",
        ),
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
    manager = bot.plugin_manager
    plugin = manager.get_plugin("slow")
    assert plugin is not None
    release = asyncio.Event()
    plugin.release = release
    active_hook = asyncio.create_task(manager.call_plugin_hook("on_message", _MESSAGE))
    async with asyncio.timeout(1):
        while "hook" not in plugin.events:
            await asyncio.sleep(0)

    reload = asyncio.create_task(manager.reload_plugin("slow"))
    async with asyncio.timeout(1):
        while "slow" not in manager._reloading:
            await asyncio.sleep(0)

    assert await manager.call_plugin_hook("on_message", _MESSAGE) == []
    assert "shutdown" not in plugin.events

    release.set()
    assert await reload == "enabled"
    assert (await active_hook)[0][1]["response"] == "default"
    assert plugin.events[-3:] == ["hook", "shutdown", "cleanup"]


async def test_reload_plugin_is_unavailable_after_shutdown(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir("toggle", body=_RELOAD_BODY)
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    await bot.plugin_manager.shutdown_plugins()

    assert await bot.plugin_manager.reload_plugin("toggle") == "unavailable"


async def test_plugin_cleanup_responds_to_cancellation(
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugins_dir = make_plugin_dir("cleanup")
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
    plugin = bot.plugin_manager.get_plugin("cleanup")
    assert plugin is not None
    cleanup_started = asyncio.Event()
    cleanup_cancelled = asyncio.Event()

    async def cleanup() -> None:
        cleanup_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleanup_cancelled.set()
            raise

    monkeypatch.setattr(plugin, "cleanup", cleanup)
    task = asyncio.create_task(bot.plugin_manager._cleanup_plugin(plugin))
    await cleanup_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleanup_cancelled.is_set()


async def test_auto_post_confirmation_responds_to_cancellation(
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugins_dir = make_plugin_dir("publisher")
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
    plugin = bot.plugin_manager.get_plugin("publisher")
    assert plugin is not None
    confirmation_started = asyncio.Event()
    confirmation_cancelled = asyncio.Event()

    async def confirm(_: str) -> None:
        confirmation_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            confirmation_cancelled.set()
            raise

    monkeypatch.setattr(plugin, "on_auto_post_published", confirm)
    task = asyncio.create_task(
        bot.plugin_manager.confirm_auto_post_published("publisher", "content")
    )
    await confirmation_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert confirmation_cancelled.is_set()
