from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from conftest import FakeMisskeyServer, MakeBot, MakePluginDir, WriteConfig

from plugins.keyact.keyact import KeyActPlugin
from plugins.radar.radar import RadarPlugin
from plugins.topics.topics import TopicsPlugin
from plugins.vision.vision import VisionPlugin
from twipsybot.bot.flows.post import AutoPostService
from twipsybot.plugin import (
    FileRef,
    MentionEvent,
    MessageEvent,
    TimelineNoteEvent,
    UserRef,
)


def _context(config: dict[str, Any], **services: Any) -> Any:
    defaults = {
        "name": "Test",
        "config": config,
        "storage": SimpleNamespace(),
        "misskey": SimpleNamespace(),
        "openai": SimpleNamespace(),
        "bot": SimpleNamespace(),
    }
    defaults.update(services)
    return SimpleNamespace(**defaults)


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
        "        self.received.append((type(event), bool(event.triggered_at), None))\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
    manager = bot.plugin_manager

    await manager.call_plugin_hook(
        "on_message",
        {
            "id": "message-1",
            "text": "hello",
            "user": {"username": "a"},
            "toRoom": {"id": "room-1"},
        },
    )
    await manager.call_plugin_hook(
        "on_mention", {"id": "mention-1", "text": "hi", "user": {"username": "b"}}
    )
    await manager.call_plugin_hook(
        "on_notification",
        {"id": "notification-1", "type": "reaction", "user": {"username": "c"}},
    )
    await manager.call_plugin_hook(
        "on_timeline_note",
        {
            "id": "note-1",
            "text": "post",
            "user": {"username": "d"},
            "streamingChannel": "antenna",
        },
    )
    await manager.call_plugin_hook("on_auto_post")

    plugin = manager.get_plugin("events")
    assert plugin is not None
    assert [(item[0].__name__, item[1], item[2]) for item in plugin.received] == [
        ("MessageEvent", "message-1", "room-1"),
        ("MentionEvent", "mention-1", "hi"),
        ("NotificationEvent", "notification-1", "reaction"),
        ("TimelineNoteEvent", "note-1", "antenna"),
        ("AutoPostEvent", True, None),
    ]


async def test_priority_and_handled_short_circuit(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir(
        "low",
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n"
        "class LowPlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n"
        "    async def on_message(self, event):\n"
        "        await self.context.storage.set('called', 'yes')\n"
        "        return self.handled('low')\n",
        config="enabled: true\npriority: 10\n",
    )
    make_plugin_dir(
        "high",
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n"
        "class HighPlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n"
        "    async def on_message(self, event):\n"
        "        return self.handled('high')\n",
        config="enabled: true\npriority: 20\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    results = await bot.plugin_manager.call_plugin_hook(
        "on_message", {"id": "message-1", "user": {"username": "a"}}
    )

    assert results == [{"handled": True, "response": "high", "plugin_name": "High"}]
    assert await bot.db.get_plugin_data("low", "called") is None


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
        "import asyncio\n"
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n"
        "class BrokenPlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n"
        "    async def on_message(self, event):\n"
        "        await asyncio.sleep(1)\n",
        config="enabled: true\npriority: 30\n",
    )
    make_plugin_dir(
        "error",
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n"
        "class ErrorPlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n"
        "    async def on_message(self, event):\n"
        "        raise RuntimeError('boom')\n",
        config="enabled: true\npriority: 20\n",
    )
    make_plugin_dir(
        "healthy",
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n"
        "class HealthyPlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n"
        "    async def on_message(self, event):\n"
        "        return self.handled('ok')\n",
        config="enabled: true\npriority: 10\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    results = await bot.plugin_manager.call_plugin_hook(
        "on_message", {"id": "message-1", "user": {"username": "a"}}
    )

    assert results == [{"handled": True, "response": "ok", "plugin_name": "Healthy"}]


async def test_lifecycle_order(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir(
        "lifecycle",
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n"
        "class LifecyclePlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n"
        "    def __init__(self, context):\n"
        "        super().__init__(context)\n"
        "        self.events = ['init']\n"
        "    async def initialize(self):\n"
        "        self.events.append('initialize')\n"
        "        return True\n"
        "    async def on_startup(self):\n"
        "        self.events.append('startup')\n"
        "    async def on_message(self, event):\n"
        "        self.events.append('hook')\n"
        "    async def on_shutdown(self):\n"
        "        self.events.append('shutdown')\n"
        "    async def cleanup(self):\n"
        "        self.events.append('cleanup')\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
    plugin = bot.plugin_manager.get_plugin("lifecycle")
    assert plugin is not None

    await bot.plugin_manager.call_plugin_hook(
        "on_message", {"id": "message-1", "user": {"username": "a"}}
    )
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
        "import asyncio\n"
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n"
        "class SlowPlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n"
        "    async def initialize(self):\n"
        "        await asyncio.sleep(1)\n"
        "        return True\n"
        "    async def cleanup(self):\n"
        "        self.cleaned = True\n",
    )

    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
    plugin = bot.plugin_manager.get_plugin("slow")

    assert plugin is not None
    assert bot.plugin_manager.get_plugin_info()[0]["enabled"] is False
    assert plugin.cleaned is True


async def test_disable_and_reload_cleanup_instances(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir(
        "reloadable",
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n"
        "class ReloadablePlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n"
        "    async def initialize(self):\n"
        "        value = int(await self.context.storage.get('initialized') or 0) + 1\n"
        "        await self.context.storage.set('initialized', str(value))\n"
        "        return True\n"
        "    async def cleanup(self):\n"
        "        value = int(await self.context.storage.get('cleaned') or 0) + 1\n"
        "        await self.context.storage.set('cleaned', str(value))\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    assert await bot.plugin_manager.reload_plugin("reloadable") == ""
    assert await bot.db.get_plugin_data("Reloadable", "initialized") == "2"
    assert await bot.db.get_plugin_data("Reloadable", "cleaned") == "1"
    assert await bot.plugin_manager.set_plugin_enabled("reloadable", False) == ""
    assert await bot.db.get_plugin_data("Reloadable", "cleaned") == "2"


async def test_invalid_hook_results_are_rejected(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir(
        "invalid",
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n"
        "class InvalidPlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n"
        "    async def on_message(self, event):\n"
        "        return {'handled': True, 'response': 42}\n"
        "    async def on_auto_post(self, event):\n"
        "        return {'contents': []}\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    assert (
        await bot.plugin_manager.call_plugin_hook(
            "on_message", {"id": "message-1", "user": {"username": "a"}}
        )
        == []
    )
    assert await bot.plugin_manager.call_plugin_hook("on_auto_post") == []
    plugin = bot.plugin_manager.get_plugin("invalid")
    assert plugin is not None

    async def invalid_visibility(event):
        return {"contents": ["x"], "visibility": []}

    plugin.on_auto_post = invalid_visibility
    assert await bot.plugin_manager.call_plugin_hook("on_auto_post") == []


async def test_sync_hook_is_rejected_at_load(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir(
        "sync",
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n"
        "class SyncPlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n"
        "    def on_message(self, event):\n"
        "        return self.handled('invalid')\n",
    )

    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    assert bot.plugin_manager.get_plugin("sync") is None


@pytest.mark.parametrize(
    ("name", "method"),
    [
        ("missingevent", "async def on_message(self):\n        pass"),
        (
            "extrarequired",
            "async def initialize(self, required):\n        return True",
        ),
    ],
)
async def test_incompatible_plugin_signature_is_rejected_at_load(
    name: str,
    method: str,
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
) -> None:
    class_name = "".join(part.capitalize() for part in name.split("_"))
    plugins_dir = make_plugin_dir(
        name,
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n"
        f"class {class_name}Plugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n"
        f"    {method}\n",
    )

    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    assert bot.plugin_manager.get_plugin(name) is None


async def test_plugin_api_version_requires_exact_integer(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir(
        "boolean",
        "from twipsybot.plugin import PluginBase\n\n"
        "class BooleanPlugin(PluginBase):\n"
        "    api_version = True\n",
    )
    make_plugin_dir(
        "float",
        "from twipsybot.plugin import PluginBase\n\n"
        "class FloatPlugin(PluginBase):\n"
        "    api_version = 1.0\n",
    )

    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    assert bot.plugin_manager.get_plugin("boolean") is None
    assert bot.plugin_manager.get_plugin("float") is None


async def test_plugin_class_name_allows_acronym_casing(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir(
        "keyact",
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n"
        "class KeyActPlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n",
    )

    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    plugin = bot.plugin_manager.get_plugin("keyact")
    assert plugin is not None
    assert plugin.context.name == "KeyAct"


async def test_invalid_event_input_is_rejected(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir(
        "input",
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n"
        "class InputPlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n"
        "    async def on_message(self, event):\n"
        "        raise AssertionError('must not run')\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    assert await bot.plugin_manager.call_plugin_hook("on_message", {}) == []
    assert await bot.plugin_manager.call_plugin_hook("on_mention", {}) == []
    assert await bot.plugin_manager.call_plugin_hook("on_notification", {}) == []
    assert await bot.plugin_manager.call_plugin_hook("on_timeline_note", {}) == []


async def test_event_raw_is_isolated_between_plugins(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir(
        "mutator",
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n"
        "class MutatorPlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n"
        "    async def on_message(self, event):\n"
        "        event.raw['user']['username'] = 'changed'\n",
        config="enabled: true\npriority: 20\n",
    )
    make_plugin_dir(
        "observer",
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n"
        "class ObserverPlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n"
        "    async def on_message(self, event):\n"
        "        return self.handled(event.raw['user']['username'])\n",
        config="enabled: true\npriority: 10\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
    payload = {"id": "message-1", "user": {"username": "original"}}

    results = await bot.plugin_manager.call_plugin_hook("on_message", payload)

    assert results[0]["response"] == "original"
    assert payload["user"]["username"] == "original"


async def test_non_bool_initialize_and_startup_failure_cleanup(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir(
        "nonbool",
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n"
        "class NonboolPlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n"
        "    async def initialize(self):\n"
        "        return 'yes'\n"
        "    async def cleanup(self):\n"
        "        self.cleaned = True\n",
    )
    make_plugin_dir(
        "startup",
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n"
        "class StartupPlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n"
        "    async def on_startup(self):\n"
        "        raise RuntimeError('boom')\n"
        "    async def cleanup(self):\n"
        "        self.cleaned = True\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
    nonbool = bot.plugin_manager.get_plugin("nonbool")
    startup = bot.plugin_manager.get_plugin("startup")
    assert nonbool is not None
    assert startup is not None

    info = {item["name"]: item for item in bot.plugin_manager.get_plugin_info()}
    assert info["Nonbool"]["enabled"] is False
    assert info["Startup"]["enabled"] is False
    assert nonbool.cleaned is True
    assert startup.cleaned is True


async def test_reload_disabled_config_updates_status(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir(
        "toggle",
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n"
        "class TogglePlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
    (plugins_dir / "toggle" / "config.yaml").write_text(
        "enabled: false\n", encoding="utf-8"
    )

    assert await bot.plugin_manager.reload_plugin("toggle") == ""

    assert bot.plugin_manager.get_plugin("toggle") is None
    assert bot.plugin_manager.get_plugin_info()[0]["enabled"] is False


async def test_context_uses_isolated_service_adapters(
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
) -> None:
    plugins_dir = make_plugin_dir(
        "context",
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n"
        "class ContextPlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
    plugin = bot.plugin_manager.get_plugin("context")
    assert plugin is not None

    assert not hasattr(plugin.context.misskey, "access_token")
    assert not hasattr(plugin.context.openai, "client")
    with pytest.raises(TypeError):
        plugin.context.config["enabled"] = False

    await plugin.context.misskey.create_note("adapter")
    await plugin.context.storage.set("key", "value")

    assert misskey_server.calls["notes/create"][-1]["text"] == "adapter"
    assert await bot.db.get_plugin_data("Context", "key") == "value"
    assert await bot.db.get_plugin_data("context", "key") is None

    antenna = {"id": "antenna-1", "name": "original"}
    misskey_server.set_response("antennas/list", lambda payload: [antenna])
    antennas = await plugin.context.misskey.list_antennas()
    antennas[0]["name"] = "changed"
    assert (await plugin.context.misskey.list_antennas())[0]["name"] == "original"
    assert antenna["name"] == "original"


async def test_reused_result_is_not_mutated(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir(
        "reuse",
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n"
        "class ReusePlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n"
        "    def __init__(self, context):\n"
        "        super().__init__(context)\n"
        "        self.result = self.handled('ok')\n"
        "    async def on_message(self, event):\n"
        "        return self.result\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
    payload = {"id": "message-1", "user": {"username": "alice"}}

    first = await bot.plugin_manager.call_plugin_hook("on_message", payload)
    second = await bot.plugin_manager.call_plugin_hook("on_message", payload)

    assert first == second
    plugin = bot.plugin_manager.get_plugin("reuse")
    assert plugin is not None
    assert plugin.result == {"handled": True, "response": "ok"}


async def test_shutdown_stops_hook_dispatch(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir(
        "shutdown",
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n"
        "class ShutdownPlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n"
        "    async def on_message(self, event):\n"
        "        raise AssertionError('must not run')\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    await bot.plugin_manager.shutdown_plugins()

    assert (
        await bot.plugin_manager.call_plugin_hook(
            "on_message", {"id": "message-1", "user": {"username": "alice"}}
        )
        == []
    )


async def test_auto_post_plugins_share_trigger_time(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir(
        "first",
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n"
        "class FirstPlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n",
    )
    make_plugin_dir(
        "second",
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n"
        "class SecondPlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
    timestamps = []

    async def capture(event):
        timestamps.append(event.triggered_at)

    first = bot.plugin_manager.get_plugin("first")
    second = bot.plugin_manager.get_plugin("second")
    assert first is not None
    assert second is not None
    first.on_auto_post = capture
    second.on_auto_post = capture

    await bot.plugin_manager.call_plugin_hook("on_auto_post")

    assert len(timestamps) == 2
    assert timestamps[0] == timestamps[1]


async def test_reload_waits_for_inflight_hook(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir(
        "drain",
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n"
        "class DrainPlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
    plugin = bot.plugin_manager.get_plugin("drain")
    assert plugin is not None
    entered = asyncio.Event()
    release = asyncio.Event()
    events: list[str] = []

    async def on_message(event):
        events.append("hook-start")
        entered.set()
        await release.wait()
        events.append("hook-end")

    async def cleanup():
        events.append("cleanup")

    plugin.on_message = on_message
    plugin.cleanup = cleanup
    hook_task = asyncio.create_task(
        bot.plugin_manager.call_plugin_hook(
            "on_message", {"id": "message-1", "user": {"username": "alice"}}
        )
    )
    await entered.wait()
    reload_task = asyncio.create_task(bot.plugin_manager.reload_plugin("drain"))
    await asyncio.sleep(0)

    assert events == ["hook-start"]
    assert not reload_task.done()
    release.set()
    await hook_task
    assert await reload_task == ""
    assert events == ["hook-start", "hook-end", "cleanup"]


async def test_concurrent_reloads_are_serialized(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir(
        "serial",
        "import asyncio\n"
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n"
        "class SerialPlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n"
        "    async def initialize(self):\n"
        "        value = int(await self.context.storage.get('initialized') or 0) + 1\n"
        "        await asyncio.sleep(0.01)\n"
        "        await self.context.storage.set('initialized', str(value))\n"
        "        return True\n"
        "    async def cleanup(self):\n"
        "        value = int(await self.context.storage.get('cleaned') or 0) + 1\n"
        "        await self.context.storage.set('cleaned', str(value))\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    results = await asyncio.gather(
        bot.plugin_manager.reload_plugin("serial"),
        bot.plugin_manager.reload_plugin("serial"),
    )

    assert results == ["", ""]
    assert await bot.db.get_plugin_data("Serial", "initialized") == "3"
    assert await bot.db.get_plugin_data("Serial", "cleaned") == "2"
    assert bot.plugin_manager.get_plugin("serial") is not None


async def test_cancelled_reload_while_draining_restores_dispatch(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir(
        "cancelwait",
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n"
        "class CancelwaitPlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
    plugin = bot.plugin_manager.get_plugin("cancelwait")
    assert plugin is not None
    entered = asyncio.Event()
    release = asyncio.Event()

    async def on_message(event):
        entered.set()
        await release.wait()
        return plugin.handled("ok")

    plugin.on_message = on_message
    hook_task = asyncio.create_task(
        bot.plugin_manager.call_plugin_hook(
            "on_message", {"id": "message-1", "user": {"username": "alice"}}
        )
    )
    await entered.wait()
    reload_task = asyncio.create_task(bot.plugin_manager.reload_plugin("cancelwait"))
    await asyncio.sleep(0)
    reload_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await reload_task
    release.set()
    await hook_task

    results = await bot.plugin_manager.call_plugin_hook(
        "on_message", {"id": "message-2", "user": {"username": "alice"}}
    )
    assert results == [{"handled": True, "response": "ok", "plugin_name": "Cancelwait"}]


async def test_cancelled_reload_completes_consistently(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir(
        "cancelrun",
        "import asyncio\n"
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n"
        "class CancelrunPlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n"
        "    async def initialize(self):\n"
        "        await asyncio.sleep(0.02)\n"
        "        await self.context.storage.set('initialized', 'yes')\n"
        "        return True\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
    reload_task = asyncio.create_task(bot.plugin_manager.reload_plugin("cancelrun"))
    await asyncio.sleep(0.005)
    reload_task.cancel()
    await asyncio.sleep(0)
    reload_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await reload_task

    plugin = bot.plugin_manager.get_plugin("cancelrun")
    assert plugin is not None
    assert await bot.db.get_plugin_data("Cancelrun", "initialized") == "yes"
    assert bot.plugin_manager.get_plugin_info()[0]["enabled"] is True


async def test_actor_lock_does_not_reenter_response_pipeline(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir(
        "actor",
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n"
        "class ActorPlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n"
        "    async def on_message(self, event):\n"
        "        async with self.context.bot.actor_lock(event.user.id, event.user.username):\n"
        "            return self.handled('ok')\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    results = await asyncio.wait_for(
        bot.plugin_manager.call_plugin_hook(
            "on_message",
            {"id": "message-1", "user": {"id": "user-1", "username": "alice"}},
        ),
        timeout=0.2,
    )

    assert results == [{"handled": True, "response": "ok", "plugin_name": "Actor"}]


async def test_admin_string_allowlist_uses_exact_match(
    make_bot: MakeBot, write_config: WriteConfig
) -> None:
    bot = await make_bot(
        write_config(
            bot={
                "admin": {
                    "enabled": True,
                    "allowed_users": "admin@example.com",
                }
            }
        )
    )

    response = await bot.admin.on_message(
        {
            "id": "message-1",
            "text": "^help",
            "user": {"id": "admin", "username": "admin"},
        }
    )

    assert response is not None
    assert "没有权限" in response


async def test_admin_can_reenable_chat(
    make_bot: MakeBot, write_config: WriteConfig
) -> None:
    bot = await make_bot(
        write_config(bot={"admin": {"enabled": True, "allowed_users": ["user-2"]}})
    )
    message = {
        "id": "message-1",
        "text": "^chat off",
        "user": {"id": "user-2", "username": "bob"},
    }
    await bot.handlers.chat.handle(message)
    assert bot.config.get("bot.response.chat") is False

    await bot.handlers.chat.handle({**message, "id": "message-2", "text": "^chat on"})

    assert bot.config.get("bot.response.chat") is True


async def test_keyact_matches_body_when_mention_has_cw() -> None:
    plugin = KeyActPlugin(
        _context(
            {
                "enabled": True,
                "rules": [{"keyword": "ping", "response": "pong"}],
            }
        )
    )
    await plugin.initialize()
    event = MentionEvent(
        id="note-1",
        text="ping",
        cw="content warning",
        user=UserRef(id="user-1", username="alice", host=None),
        files=(),
        raw={},
    )

    assert await plugin.on_mention(event) == {"handled": True, "response": "pong"}


async def test_topics_rss_ai_uses_public_openai_service() -> None:
    generate_text = AsyncMock(return_value="rewritten title\nignored")
    openai = SimpleNamespace(
        generate_text=generate_text,
        system_prompt="system",
        max_tokens=100,
        temperature=0.5,
    )
    plugin = TopicsPlugin(
        _context(
            {"enabled": True, "rss_ai": True, "rss_ai_prefix": "{title}"},
            openai=openai,
        )
    )

    result = await plugin._rewrite_rss_title_with_ai(
        "original", "https://example.com", summary="summary"
    )

    assert result == "rewritten title"
    generate_text.assert_awaited_once_with(
        "original",
        "system",
        max_tokens=100,
        temperature=0.5,
    )


async def test_topics_rss_is_recorded_only_after_publish() -> None:
    storage = SimpleNamespace()
    plugin = TopicsPlugin(
        _context(
            {"enabled": True, "source": "rss", "rss_list": ["feed"]},
            storage=storage,
        )
    )
    entry = {
        "key": "entry-key",
        "title": "title",
        "link": "https://example.com/post",
        "summary": "",
    }
    plugin._get_recent_rss_keys = AsyncMock(return_value=[])
    plugin._fetch_all_rss_candidates = AsyncMock(return_value=[entry])
    plugin._select_latest_per_feed = lambda urls, candidates, recent_set: [entry]
    plugin._render_selected_rss_entries = AsyncMock(return_value=["content"])
    plugin._set_recent_rss_keys = AsyncMock(return_value=True)

    assert await plugin._get_next_rss_posts() == ["content"]
    plugin._set_recent_rss_keys.assert_not_awaited()

    await plugin._on_auto_post_published("content")

    plugin._set_recent_rss_keys.assert_awaited_once_with(["entry-key"])

    plugin._pending_rss["failed"] = [("failed-key", None)]
    plugin._set_recent_rss_keys = AsyncMock(return_value=False)
    with pytest.raises(RuntimeError, match="persist published RSS entry"):
        await plugin._on_auto_post_published("failed")


async def test_auto_post_confirms_only_successful_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_note = AsyncMock(side_effect=[{}, RuntimeError("failed")])
    confirm = AsyncMock()
    bot = SimpleNamespace(
        runtime=SimpleNamespace(running=True, startup_time=None),
        misskey=SimpleNamespace(create_note=create_note),
        plugin_manager=SimpleNamespace(confirm_auto_post_published=confirm),
        format_log_text=lambda text: text,
    )
    service = AutoPostService(cast(Any, bot))
    monkeypatch.setattr(service, "_PLUGIN_POST_INTERVAL_SECONDS", 0)
    result = {"plugin_name": "Topics"}

    with pytest.raises(RuntimeError, match="failed"):
        await service._post_plugin_contents(
            result, ["published", "failed"], "public", 2, False
        )

    confirm.assert_awaited_once_with(result, "published")


async def test_vision_handles_image_with_public_services() -> None:
    drive = SimpleNamespace(fetch_bytes=AsyncMock(return_value=b"image"))
    misskey = SimpleNamespace(drive=drive)
    generate_chat = AsyncMock(return_value="image reply")
    openai = SimpleNamespace(
        uses_responses_api=False,
        system_prompt="system",
        max_tokens=100,
        temperature=0.5,
        generate_chat=generate_chat,
    )
    plugin = VisionPlugin(
        _context(
            {"enabled": True, "max_images": 1},
            misskey=misskey,
            openai=openai,
        )
    )
    event = MessageEvent(
        id="message-1",
        text="describe",
        user=UserRef(id="user-1", username="alice", host=None),
        room_id=None,
        files=(
            FileRef(
                id="file-1",
                mime_type="image/png",
                url="https://example.com/image.png",
                thumbnail_url=None,
                raw={},
            ),
        ),
        raw={},
    )

    assert await plugin.on_message(event) == {
        "handled": True,
        "response": "image reply",
    }
    generate_chat.assert_awaited_once()


async def test_radar_reacts_through_public_misskey_service() -> None:
    create_reaction = AsyncMock(return_value={})
    misskey = SimpleNamespace(create_reaction=create_reaction)

    @asynccontextmanager
    async def actor_lock(user_id: str | None, username: str | None):
        yield

    bot = SimpleNamespace(user_id="bot-id", username="bot", actor_lock=actor_lock)
    plugin = RadarPlugin(
        _context(
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

    create_reaction.assert_awaited_once_with("note-1", "heart")

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
