from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from types import MappingProxyType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from conftest import FakeMisskeyServer, MakeBot, MakePluginDir, WriteConfig

from plugins.iincho.iincho import IinchoPlugin, _Sample
from plugins.keyact.keyact import KeyActPlugin
from plugins.radar.radar import RadarPlugin
from plugins.topics.topics import TopicsPlugin
from plugins.vision.vision import VisionPlugin
from twipsybot.plugin import (
    FileRef,
    MentionEvent,
    MessageEvent,
    PluginBase,
    PluginConfig,
    TimelineNoteEvent,
    UserRef,
)
from twipsybot.plugin.events import build_hook_event
from twipsybot.plugin.services import DriveServiceAdapter, MisskeyServiceAdapter


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


async def test_drive_service_adapter_uploads_bytes() -> None:
    upload = AsyncMock(return_value={"id": "file-1"})
    drive = DriveServiceAdapter(SimpleNamespace(upload_bytes=upload))

    result = await drive.upload_bytes(
        b"image", name="image.webp", content_type="image/webp"
    )

    assert result == {"id": "file-1"}
    upload.assert_awaited_once_with(
        b"image", name="image.webp", content_type="image/webp"
    )


async def test_misskey_service_adapter_sends_message() -> None:
    send_message = AsyncMock(return_value={"id": "message-1"})
    service = MisskeyServiceAdapter(
        SimpleNamespace(drive=SimpleNamespace(), send_message=send_message)
    )

    result = await service.send_message("admin-id", "alert")

    assert result == {"id": "message-1"}
    send_message.assert_awaited_once_with("admin-id", "alert")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, False),
        (True, True),
        (False, False),
        ("true", True),
        ("false", False),
    ],
)
def test_parse_bool(value: Any, expected: bool) -> None:
    assert PluginBase._parse_bool(value, False) is expected


@pytest.mark.parametrize("value", ("", "maybe", 0, 1, [], {}))
def test_parse_bool_rejects_invalid_values(value: Any) -> None:
    with pytest.raises(ValueError, match="invalid boolean value"):
        PluginBase._parse_bool(value, False)


def test_plugin_config_is_typed_immutable_and_ignores_framework_fields() -> None:
    class Config(PluginConfig):
        limit: int = 10

    class Plugin(PluginBase):
        config_class = Config
        settings: Config

    config = Plugin(_context({"enabled": True, "limit": "20"})).settings

    assert config.limit == 20
    assert config.model_dump() == {"limit": 20}
    with pytest.raises(ValueError):
        config.limit = 30


def test_keyact_parses_boolean_strings() -> None:
    plugin = KeyActPlugin(
        _context(
            {
                "enabled": True,
                "mention_enabled": "false",
                "chat_enabled": "true",
                "case_sensitive": "false",
                "rules": [],
            }
        )
    )

    assert plugin.settings.mention_enabled is False
    assert plugin.settings.chat_enabled is True
    assert plugin.settings.case_sensitive is False


def test_builtin_plugins_parse_boolean_strings() -> None:
    radar = RadarPlugin(
        _context({"enabled": "true", "reply": "false", "quote": "true"})
    )
    topics = TopicsPlugin(_context({"enabled": "true", "rss_ai": "false"}))
    vision = VisionPlugin(_context({"enabled": "true", "use_thumbnail": "false"}))

    assert radar._enabled is True
    assert radar.settings.reply_enabled is False
    assert radar.settings.quote_enabled is True
    assert topics.settings.rss_ai is False
    assert vision.settings.use_thumbnail is False


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


async def test_failed_plugin_initialization_runs_cleanup(
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
) -> None:
    plugins_dir = make_plugin_dir(
        "failing",
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n\n"
        "class FailingPlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n\n"
        "    async def initialize(self):\n"
        "        return False\n\n"
        "    async def cleanup(self):\n"
        "        await self.context.storage.set('cleaned', 'yes')\n",
    )

    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    plugin = bot.plugin_manager.get_plugin("failing")
    assert plugin is not None
    assert bot.plugin_manager.get_plugin_info()[0]["enabled"] is False
    assert await bot.db.get_plugin_data("Failing", "cleaned") == "yes"


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


async def test_incompatible_plugin_api_is_rejected(
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
) -> None:
    plugins_dir = make_plugin_dir(
        "future",
        "from twipsybot.plugin import PluginBase\n\n\n"
        "class FuturePlugin(PluginBase):\n"
        "    api_version = 2\n",
    )

    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    assert bot.plugin_manager.get_plugin("future") is None


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


async def test_keyact_matches_body_when_mention_has_cw() -> None:
    plugin = KeyActPlugin(
        _context(
            {
                "enabled": True,
                "rules": [{"keywords": ["ping"], "response": "pong"}],
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


async def test_keyact_normalizes_case_once_when_loading_rules() -> None:
    plugin = KeyActPlugin(
        _context(
            {
                "enabled": True,
                "rules": [{"keywords": ["PING"], "response": "pong"}],
            }
        )
    )
    await plugin.initialize()
    event = MessageEvent(
        id="message-1",
        text="ping",
        user=UserRef(id="user-1", username="alice", host=None),
        room_id=None,
        files=(),
        raw={},
    )

    assert await plugin.on_message(event) == {"handled": True, "response": "pong"}


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


async def test_topics_initializes_rss_storage_defaults() -> None:
    storage = SimpleNamespace(get=AsyncMock(return_value=None), set=AsyncMock())
    plugin = TopicsPlugin(_context({"enabled": True, "source": "rss"}, storage=storage))

    assert await plugin.initialize() is True
    storage.set.assert_any_await("rss_recent_keys", "[]")
    storage.set.assert_any_await("rss_last_feed_idx", "0")


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


async def test_topics_rss_retains_2000_published_keys() -> None:
    plugin = TopicsPlugin(_context({"enabled": True, "source": "rss"}))
    plugin._pending_rss["content"] = [("new-key", None)]
    plugin._get_recent_rss_keys = AsyncMock(
        return_value=[f"key-{index}" for index in range(2000)]
    )
    plugin._set_recent_rss_keys = AsyncMock(return_value=True)

    await plugin._on_auto_post_published("content")

    saved_keys = plugin._set_recent_rss_keys.await_args_list[0].args[0]
    assert len(saved_keys) == 2000
    assert saved_keys[0] == "key-1"
    assert saved_keys[-1] == "new-key"


async def test_topics_rotate_advances_only_after_publish() -> None:
    stored = {"rss_recent_keys": "[]", "rss_last_feed_idx": "0"}
    storage = SimpleNamespace(
        get=AsyncMock(side_effect=lambda key: stored.get(key)), set=AsyncMock()
    )
    plugin = TopicsPlugin(
        _context(
            {
                "enabled": True,
                "source": "rss",
                "rss_post_mode": "rotate",
            },
            storage=storage,
        )
    )
    entry = {
        "key": "entry-key",
        "title": "title",
        "link": "https://example.com/post",
        "summary": "",
        "ts": 1,
        "entry_idx": 0,
        "feed_idx": 0,
    }
    plugin._fetch_rss_candidates = AsyncMock(return_value=[entry])
    plugin._render_selected_rss_entries = AsyncMock(return_value=["content"])

    assert await plugin._get_next_rss_posts_rotate(["feed"]) == ["content"]
    storage.set.assert_not_awaited()

    await plugin._on_auto_post_published("content")

    storage.set.assert_any_await("rss_recent_keys", '["entry-key"]')
    storage.set.assert_any_await("rss_last_feed_idx", "0")


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

    event = build_hook_event("on_message", raw)

    assert isinstance(event, MessageEvent)
    assert len(event.files) == 1
    assert event.files[0].id == "file-1"
    assert event.files[0].mime_type == "image/png"
    assert event.files[0].url == "https://example.com/image.png"
    assert event.files[0].thumbnail_url == "https://example.com/thumbnail.webp"


def test_mention_hook_event_preserves_note_files() -> None:
    raw = {
        "type": "mention",
        "note": {
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
        },
    }

    event = build_hook_event("on_mention", raw)

    assert isinstance(event, MentionEvent)
    assert len(event.files) == 1
    assert event.files[0].id == "file-1"
    assert event.files[0].mime_type == "image/jpeg"
    assert event.files[0].url == "https://example.com/image.jpg"


async def test_vision_resolves_missing_mime_once() -> None:
    drive = SimpleNamespace(
        fetch_bytes=AsyncMock(return_value=b"image"),
        show_file=AsyncMock(return_value={"type": "image/png"}),
        download_bytes=AsyncMock(),
    )
    plugin = VisionPlugin(
        _context({"enabled": True}, misskey=SimpleNamespace(drive=drive))
    )
    file = FileRef(
        id="file-1",
        mime_type=None,
        url="https://example.com/image.png",
        thumbnail_url=None,
        raw={},
    )

    result = await plugin._to_image_part(file, use_responses=False)

    assert result is not None
    assert result["type"] == "image_url"
    drive.show_file.assert_awaited_once_with("file-1")
    drive.download_bytes.assert_not_awaited()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("6 MB", 6_000_000),
        ("6 MiB", 6 * 1024 * 1024),
    ],
)
def test_vision_size_parsing(value: Any, expected: int) -> None:
    plugin = VisionPlugin(_context({"enabled": True, "max_bytes": value}))

    assert plugin.settings.max_bytes == expected


@pytest.mark.parametrize("value", (1024.9, -1, "invalid", True))
def test_vision_rejects_invalid_size(value: Any) -> None:
    context = _context({"enabled": True, "max_bytes": value})

    with pytest.raises(ValueError, match="max_bytes"):
        VisionPlugin(context)


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


async def test_radar_preserves_reply_and_quote_precedence() -> None:
    misskey = SimpleNamespace(
        create_reaction=AsyncMock(return_value={}),
        create_note=AsyncMock(return_value={}),
        create_renote=AsyncMock(return_value={}),
    )
    plugin = RadarPlugin(
        _context(
            {
                "enabled": True,
                "reply": True,
                "reply_text": "hello {username}",
                "quote": True,
                "quote_text": "quote",
                "renote": True,
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


def _iincho_context(config: dict[str, Any] | None = None) -> Any:
    openai = SimpleNamespace(
        generate_text=AsyncMock(),
        moderate_texts=AsyncMock(
            side_effect=lambda texts: [frozenset() for _ in texts]
        ),
    )
    misskey = SimpleNamespace(
        instance_url="https://misskey.example",
        create_note=AsyncMock(return_value={}),
        send_message=AsyncMock(return_value={}),
    )
    return SimpleNamespace(
        name="iincho",
        config={"enabled": True, "interval": "5m", **(config or {})},
        storage=SimpleNamespace(),
        openai=openai,
        misskey=misskey,
        bot=SimpleNamespace(user_id="bot-id", username="iincho"),
    )


def _iincho_event(
    text: str = "本地帖子",
    *,
    event_id: str = "note-1",
    channel: str = "localTimeline",
    user_id: str = "user-1",
    username: str = "user",
    cw: str | None = None,
) -> TimelineNoteEvent:
    return TimelineNoteEvent(
        id=event_id,
        text=text,
        cw=cw,
        user=UserRef(id=user_id, username=username, host=None),
        channel=channel,
        files=(),
        raw=MappingProxyType({}),
    )


def _iincho_result() -> str:
    return json.dumps({"trends": ["新功能体验", "部署问题"]}, ensure_ascii=False)


@pytest.mark.parametrize("interval", ("4m", "nope", 0))
def test_iincho_rejects_invalid_interval(interval: Any) -> None:
    context = _iincho_context({"interval": interval})

    with pytest.raises(ValueError, match="interval"):
        IinchoPlugin(context)


def test_iincho_uses_defaults_for_one_hundred_notes() -> None:
    plugin = IinchoPlugin(_iincho_context())

    assert plugin.settings.sample_size == 100
    assert plugin.settings.max_input_chars == 24000
    assert plugin.settings.max_tokens == 2000


def test_iincho_rejects_fractional_integer_config() -> None:
    context = _iincho_context({"sample_size": 10.5})

    with pytest.raises(ValueError, match="sample_size"):
        IinchoPlugin(context)


@pytest.mark.parametrize("admin_ids", (None, "", ["", " "]))
def test_iincho_accepts_empty_admin_ids(admin_ids: Any) -> None:
    plugin = IinchoPlugin(_iincho_context({"admin_ids": admin_ids}))

    assert plugin.settings.admin_ids == ()


async def test_iincho_collects_only_eligible_local_notes() -> None:
    plugin = IinchoPlugin(_iincho_context({"sample_size": 2, "min_notes": 1}))

    await plugin.on_timeline_note(_iincho_event(channel="globalTimeline"))
    await plugin.on_timeline_note(_iincho_event(user_id="bot-id"))
    await plugin.on_timeline_note(_iincho_event(text=""))
    await plugin.on_timeline_note(_iincho_event(text="正文", cw="预警"))

    assert plugin._window.total == 3
    assert plugin._window.eligible == 1
    assert [sample.text for sample in plugin._window.samples] == ["预警\n正文"]


async def test_iincho_reservoir_stays_bounded() -> None:
    plugin = IinchoPlugin(_iincho_context({"sample_size": 2, "min_notes": 1}))
    plugin._rng.seed(1)

    for index in range(20):
        await plugin.on_timeline_note(
            _iincho_event(str(index), event_id=str(index), user_id=str(index))
        )

    assert plugin._window.eligible == 20
    assert len(plugin._window.samples) == 2


async def test_iincho_publishes_formatted_summary() -> None:
    context = _iincho_context({"sample_size": 2, "min_notes": 2})
    context.openai.generate_text.return_value = _iincho_result()
    context.openai.moderate_texts.side_effect = None
    context.openai.moderate_texts.return_value = [
        frozenset({"harassment", "harassment/threatening"}),
        frozenset({"illicit"}),
    ]
    plugin = IinchoPlugin(context)
    await plugin.on_timeline_note(
        _iincho_event("第一条 https://example.com @alice", event_id="1")
    )
    await plugin.on_timeline_note(_iincho_event("第二条", event_id="2"))

    await plugin._process_window()

    prompt = context.openai.generate_text.await_args.args[0]
    assert "不可信帖子数组" in prompt
    assert "第一条" in prompt
    assert "example.com" not in prompt
    assert "@alice" not in prompt
    assert json.loads(prompt.partition("DATA=")[2]) == [
        "第一条 [链接] [账号]",
        "第二条",
    ]
    context.openai.moderate_texts.assert_awaited_once_with(
        ["第一条 [链接] [账号]", "第二条"]
    )
    assert context.openai.generate_text.await_args.kwargs["json_output"] is True
    created = context.misskey.create_note.await_args.kwargs
    assert created["visibility"] == "public"
    assert created["local_only"] is True
    assert created["validate_reply"] is False
    assert created["text"].startswith("📊 Iincho 时间线观察\n\n🕒")
    assert "本地时间线观察" not in created["text"]
    assert "概览" not in created["text"]
    assert "热点" not in created["text"]
    assert "氛围" not in created["text"]
    assert "🚨 违规审查：" in created["text"]
    assert "💢 骚扰攻击 1" in created["text"]
    assert "⚖️ 违法活动 1" in created["text"]


async def test_iincho_limits_serialized_input() -> None:
    context = _iincho_context({"min_notes": 1, "max_input_chars": 1000})
    context.openai.generate_text.return_value = _iincho_result()
    plugin = IinchoPlugin(context)
    await plugin.on_timeline_note(_iincho_event("\\" * 2000, event_id="1"))
    await plugin.on_timeline_note(_iincho_event("第二条", event_id="2"))
    await plugin.on_timeline_note(_iincho_event("第三条", event_id="3"))

    await plugin._process_window()

    prompt = context.openai.generate_text.await_args.args[0]
    payload = prompt.partition("DATA=")[2]
    assert len(payload) <= 1000
    assert json.loads(payload)[0]
    context.openai.moderate_texts.assert_awaited_once_with(json.loads(payload))
    summary = context.misskey.create_note.await_args.kwargs["text"]
    assert "覆盖 3 篇有效帖子，AI 均匀抽样 1 篇" in summary


def test_iincho_serializes_special_characters_losslessly() -> None:
    plugin = IinchoPlugin(_iincho_context({"min_notes": 1}))

    payload, selected = plugin._serialize_samples(
        [_Sample(note_id='id"\\', text='line 1\n"line 2"\\')]
    )

    assert [sample.note_id for sample in selected] == ['id"\\']
    assert json.loads(payload) == ['line 1\n"line 2"\\']


async def test_iincho_keeps_notes_arriving_during_generation() -> None:
    context = _iincho_context({"sample_size": 2, "min_notes": 1})
    plugin = IinchoPlugin(context)
    await plugin.on_timeline_note(_iincho_event("旧窗口"))

    async def generate(*args: Any, **kwargs: Any) -> str:
        await plugin.on_timeline_note(_iincho_event("新窗口", event_id="new"))
        return _iincho_result()

    context.openai.generate_text.side_effect = generate
    await plugin._process_window()

    assert [sample.text for sample in plugin._window.samples] == ["新窗口"]
    assert plugin._window.eligible == 1


async def test_iincho_notifies_all_admins_with_verified_note_link() -> None:
    context = _iincho_context(
        {"min_notes": 1, "admin_ids": ["admin-1", "admin-2", "admin-1"]}
    )
    context.openai.generate_text.return_value = _iincho_result()
    context.openai.moderate_texts.side_effect = None
    context.openai.moderate_texts.return_value = [
        frozenset({"harassment", "harassment/threatening", "illicit"})
    ]
    plugin = IinchoPlugin(context)
    await plugin.on_timeline_note(_iincho_event())

    await plugin._process_window()

    assert context.misskey.send_message.await_count == 2
    for call in context.misskey.send_message.await_args_list:
        message = call.args[1]
        assert message.startswith("🚨 Iincho 近期小报告\n\n🔥 热点")
        assert "💢 骚扰攻击、⚖️ 违法活动: note-1" in message
        assert "https://misskey.example" not in message
        assert "• 新功能体验" in message
    assert {call.args[0] for call in context.misskey.send_message.await_args_list} == {
        "admin-1",
        "admin-2",
    }


async def test_iincho_admin_failure_does_not_block_others_or_summary() -> None:
    context = _iincho_context({"min_notes": 1, "admin_ids": "admin-1, admin-2"})
    context.openai.generate_text.return_value = _iincho_result()
    context.openai.moderate_texts.side_effect = None
    context.openai.moderate_texts.return_value = [frozenset({"harassment"})]
    context.misskey.send_message.side_effect = [RuntimeError("unavailable"), {}]
    plugin = IinchoPlugin(context)
    await plugin.on_timeline_note(_iincho_event())

    await plugin._process_window()

    assert [call.args[0] for call in context.misskey.send_message.await_args_list] == [
        "admin-1",
        "admin-2",
    ]
    context.misskey.create_note.assert_awaited_once()


async def test_iincho_sends_trends_to_admin_without_violations() -> None:
    context = _iincho_context({"min_notes": 1, "admin_ids": ["admin-1"]})
    context.openai.generate_text.return_value = _iincho_result()
    plugin = IinchoPlugin(context)
    await plugin.on_timeline_note(_iincho_event())

    await plugin._process_window()

    message = context.misskey.send_message.await_args.args[1]
    assert "🔥 热点\n• 新功能体验\n• 部署问题" in message
    assert "🚨 违规审查：\n✅ 未发现明显违规" in message


async def test_iincho_discards_invalid_ai_result_without_retry() -> None:
    context = _iincho_context({"min_notes": 1})
    context.openai.generate_text.return_value = "not json"
    plugin = IinchoPlugin(context)
    await plugin.on_timeline_note(_iincho_event())

    with pytest.raises(ValueError):
        await plugin._process_window()

    context.openai.generate_text.assert_awaited_once()
    context.misskey.create_note.assert_not_awaited()
    assert plugin._window.eligible == 0


async def test_iincho_rejects_mismatched_moderation_results() -> None:
    context = _iincho_context({"min_notes": 1})
    context.openai.generate_text.return_value = _iincho_result()
    context.openai.moderate_texts.side_effect = None
    context.openai.moderate_texts.return_value = []
    plugin = IinchoPlugin(context)
    await plugin.on_timeline_note(_iincho_event())

    with pytest.raises(ValueError, match="count mismatch"):
        await plugin._process_window()

    context.misskey.create_note.assert_not_awaited()


async def test_iincho_skips_small_window() -> None:
    context = _iincho_context({"min_notes": 2})
    plugin = IinchoPlugin(context)
    await plugin.on_timeline_note(_iincho_event())

    await plugin._process_window()

    context.openai.generate_text.assert_not_awaited()
    context.openai.moderate_texts.assert_not_awaited()
    context.misskey.create_note.assert_not_awaited()


async def test_iincho_stops_background_task() -> None:
    plugin = IinchoPlugin(_iincho_context())

    await plugin.on_startup()
    assert plugin._task is not None
    await plugin.on_shutdown()

    assert plugin._task is None
