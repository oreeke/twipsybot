from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest
from support.events import message
from support.factories import set_plugin_config
from support.types import MakeBot, MakePluginDir, WriteConfig

import twipsybot.plugin.manager as manager_module
from twipsybot.plugin.manager import PluginManager

_MESSAGE = message({"id": "message-1", "user": {"username": "alice"}})
_RELOAD_BODY = (
    "from twipsybot.plugin import PluginConfig\n"
    "class Settings(PluginConfig):\n"
    "    reply: str = 'default'\n"
    "config_class = Settings\n"
    "def __init__(self, context):\n"
    "    super().__init__(context)\n"
    "    self.events = ['init']\n"
    "    self.gate = None\n"
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
    "    if gate := self.gate:\n"
    "        self.gate = None\n"
    "        self.events.append('hook')\n"
    "        await gate.wait()\n"
    "        self.events.append('hook-done')\n"
    "    return self.handled(self.settings.reply)\n"
)
_BLOCKING_BODY = (
    "entered = cancelled = False\n"
    "async def _block(self):\n"
    "    import asyncio\n"
    "    if self.cancelled:\n"
    "        return\n"
    "    self.entered = True\n"
    "    try:\n"
    "        await asyncio.Event().wait()\n"
    "    except asyncio.CancelledError:\n"
    "        self.cancelled = True\n"
    "        raise\n"
    "async def on_message(self, event):\n"
    "    await self._block()\n"
    "async def cleanup(self):\n"
    "    await self._block()\n"
    "async def on_auto_post_published(self, content):\n"
    "    await self._block()\n"
)


async def _until(condition: Callable[[], bool]) -> None:
    async with asyncio.timeout(1):
        while not condition():
            await asyncio.sleep(0)


async def _until_dispatch_stops(manager: PluginManager) -> None:
    async with asyncio.timeout(1):
        while await manager.call_plugin_hook("on_message", _MESSAGE):
            await asyncio.sleep(0)


async def test_lifecycle_order(boot) -> None:
    bot = await boot(lifecycle=_RELOAD_BODY)
    manager = bot.plugin_manager
    plugin = manager.plugins["lifecycle"]
    plugin.gate = asyncio.Event()
    plugin.gate.set()

    await manager.call_plugin_hook("on_message", _MESSAGE)
    await manager.shutdown_plugins()
    await manager.cleanup_plugins()

    assert plugin.events == [
        "init",
        "initialize",
        "startup",
        "hook",
        "hook-done",
        "shutdown",
        "cleanup",
    ]


@pytest.mark.parametrize(
    "body",
    [
        (
            "async def initialize(self):\n"
            "    import asyncio\n"
            "    await asyncio.sleep(1)\n"
            "    return True"
        ),
        "async def initialize(self):\n    return False",
        "async def initialize(self):\n    return 'yes'",
        "async def on_startup(self):\n    raise RuntimeError('boom')",
    ],
    ids=[
        "initialize-timeout",
        "initialize-false",
        "initialize-non-bool",
        "startup-error",
    ],
)
async def test_failed_initialization_or_startup_disables_plugin_and_cleans_up(
    body: str, monkeypatch: pytest.MonkeyPatch, boot
) -> None:
    monkeypatch.setattr(manager_module, "_PLUGIN_LIFECYCLE_TIMEOUT_SECONDS", 0.05)
    bot = await boot(
        failing=f"{body}\nasync def cleanup(self):\n    self.cleaned = True\n"
    )

    assert bot.plugin_manager.get_plugin_states() == {"failing": False}
    assert bot.plugin_manager.plugins["failing"].cleaned is True


async def test_shutdown_stops_hook_dispatch_and_reload(boot) -> None:
    bot = await boot(
        shutdown="async def on_message(self, event):\n"
        "    raise AssertionError('must not run')\n"
    )
    manager = bot.plugin_manager

    await manager.shutdown_plugins()

    assert await manager.call_plugin_hook("on_message", _MESSAGE) == []
    assert await manager.reload_plugin("shutdown") == "unavailable"


async def test_shutdown_waits_for_active_hook_and_rejects_new_hooks(boot) -> None:
    bot = await boot(shutdown=_RELOAD_BODY)
    manager = bot.plugin_manager
    plugin = manager.plugins["shutdown"]
    release = plugin.gate = asyncio.Event()
    active_hook = asyncio.create_task(manager.call_plugin_hook("on_message", _MESSAGE))
    await _until(lambda: "hook" in plugin.events)

    shutdown = asyncio.create_task(manager.shutdown_plugins())
    await _until_dispatch_stops(manager)

    assert not shutdown.done()
    assert plugin.events[-1] == "hook"

    release.set()
    await asyncio.gather(active_hook, shutdown)

    assert plugin.events[-3:] == ["hook", "hook-done", "shutdown"]


async def test_shutdown_cancels_hook_after_grace_period(
    monkeypatch: pytest.MonkeyPatch, boot
) -> None:
    monkeypatch.setattr(manager_module, "_PLUGIN_SHUTDOWN_GRACE_SECONDS", 0.01)
    bot = await boot(blocking=_BLOCKING_BODY)
    manager = bot.plugin_manager
    plugin = manager.plugins["blocking"]
    active_hook = asyncio.create_task(manager.call_plugin_hook("on_message", _MESSAGE))
    await _until(lambda: plugin.entered)

    await manager.shutdown_plugins()

    assert active_hook.cancelled()
    assert plugin.cancelled is True


@pytest.mark.parametrize(
    "trigger",
    [
        lambda manager: manager.cleanup_plugins(),
        lambda manager: manager.confirm_auto_post_published("blocking", "content"),
    ],
    ids=["cleanup", "auto-post-confirmation"],
)
async def test_blocked_plugin_callback_responds_to_cancellation(
    trigger: Callable[[PluginManager], Any], boot
) -> None:
    bot = await boot(blocking=_BLOCKING_BODY)
    plugin = bot.plugin_manager.plugins["blocking"]
    task = asyncio.create_task(trigger(bot.plugin_manager))
    await _until(lambda: plugin.entered)

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert plugin.cancelled is True


async def test_reload_plugin_replaces_instance_with_new_config(boot) -> None:
    bot = await boot(reload=_RELOAD_BODY)
    manager = bot.plugin_manager
    old = manager.plugins["reload"]
    await old.context.storage.set("kept", "yes")
    set_plugin_config(bot.config.root, "reload", {"enabled": True, "reply": "updated"})

    assert await bot.settings.reload() == {"reload": "enabled"}

    new = manager.plugins["reload"]
    assert new is not old
    assert type(new) is type(old)
    assert old.events == ["init", "initialize", "startup", "shutdown", "cleanup"]
    assert new.events == ["init", "initialize", "startup"]
    assert await new.context.storage.get("kept") == "yes"
    assert await manager.call_plugin_hook("on_message", _MESSAGE) == [
        ("reload", {"handled": True, "response": "updated"})
    ]


async def test_reload_plugin_failure_disables_only_that_plugin(boot) -> None:
    bot = await boot(
        broken=_RELOAD_BODY,
        healthy="async def on_message(self, event):\n    return self.handled('ok')\n",
    )
    manager = bot.plugin_manager
    old = manager.plugins["broken"]
    set_plugin_config(bot.config.root, "broken", {"enabled": True, "reply": [1]})

    assert await bot.settings.reload() == {"broken": "failed"}

    assert manager.plugins["broken"] is old
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
    assert "toggle" not in manager.plugins

    set_plugin_config(root, "toggle", {"enabled": True})
    assert await bot.settings.reload() == {"toggle": "enabled"}
    plugin = manager.plugins["toggle"]
    assert manager.get_plugin_states() == {"toggle": True}

    set_plugin_config(root, "toggle", None)
    assert await bot.settings.reload() == {"toggle": "disabled"}
    assert plugin.events[-2:] == ["shutdown", "cleanup"]
    assert manager.get_plugin_states() == {"toggle": False}
    assert await manager.call_plugin_hook("on_message", _MESSAGE) == []

    set_plugin_config(root, "toggle", {"enabled": True})
    assert await bot.settings.reload() == {"toggle": "enabled"}
    assert type(manager.plugins["toggle"]) is type(plugin)


async def test_settings_reload_only_restarts_changed_plugins(boot) -> None:
    bot = await boot(first=_RELOAD_BODY, second=_RELOAD_BODY)
    second = bot.plugin_manager.plugins["second"]
    set_plugin_config(bot.config.root, "first", {"enabled": True, "reply": "new"})

    assert await bot.settings.reload() == {"first": "enabled"}
    assert bot.plugin_manager.plugins["second"] is second
    assert await bot.settings.reload() == {}


@pytest.mark.parametrize("name", ["missing", "../toggle", "."])
async def test_reload_plugin_rejects_unknown_names(name: str, boot) -> None:
    bot = await boot(toggle=_RELOAD_BODY)

    assert await bot.plugin_manager.reload_plugin(name) == "unknown"


async def test_reload_plugin_drains_active_hook_before_shutdown(boot) -> None:
    bot = await boot(slow=_RELOAD_BODY)
    manager = bot.plugin_manager
    plugin = manager.plugins["slow"]
    release = plugin.gate = asyncio.Event()
    active_hook = asyncio.create_task(manager.call_plugin_hook("on_message", _MESSAGE))
    await _until(lambda: "hook" in plugin.events)

    reload = asyncio.create_task(manager.reload_plugin("slow"))
    await _until_dispatch_stops(manager)

    assert "shutdown" not in plugin.events

    release.set()
    assert await reload == "enabled"
    assert (await active_hook)[0][1]["response"] == "default"
    assert plugin.events[-4:] == ["hook", "hook-done", "shutdown", "cleanup"]
