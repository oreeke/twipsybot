from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from conftest import FakeMisskeyServer, MakeBot, MakePluginDir, WriteConfig

from twipsybot.plugin import (
    MentionEvent,
    MessageEvent,
    PluginBase,
    PluginConfig,
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
        body="async def on_message(self, event):\n"
        "    await self.context.storage.set('called', 'yes')\n"
        "    return self.handled('low')\n",
        config="enabled: true\npriority: 10\n",
    )
    make_plugin_dir(
        "high",
        body="async def on_message(self, event):\n    return self.handled('high')\n",
        config="enabled: true\npriority: 20\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    results = await bot.plugin_manager.call_plugin_hook(
        "on_message", {"id": "message-1", "user": {"username": "a"}}
    )

    assert results == [{"handled": True, "response": "high", "plugin_name": "high"}]
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
        body="async def on_message(self, event):\n"
        "    import asyncio\n"
        "    await asyncio.sleep(1)\n",
        config="enabled: true\npriority: 30\n",
    )
    make_plugin_dir(
        "error",
        body="async def on_message(self, event):\n    raise RuntimeError('boom')\n",
        config="enabled: true\npriority: 20\n",
    )
    make_plugin_dir(
        "healthy",
        body="async def on_message(self, event):\n    return self.handled('ok')\n",
        config="enabled: true\npriority: 10\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    results = await bot.plugin_manager.call_plugin_hook(
        "on_message", {"id": "message-1", "user": {"username": "a"}}
    )

    assert results == [{"handled": True, "response": "ok", "plugin_name": "healthy"}]


async def test_auto_post_uses_shared_hook_timeout(
    monkeypatch: pytest.MonkeyPatch,
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
) -> None:
    import twipsybot.plugin.manager as manager_module

    monkeypatch.setattr(manager_module, "_PLUGIN_HOOK_TIMEOUT_SECONDS", 0.01)
    plugins_dir = make_plugin_dir(
        "slow_auto_post",
        body="async def on_auto_post(self, event):\n"
        "    import asyncio\n"
        "    await asyncio.sleep(0.03)\n"
        "    return {'prompt': 'ready'}\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    results = await bot.plugin_manager.call_plugin_hook("on_auto_post")

    assert results == []


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
    assert bot.plugin_manager.get_plugin_info()[0]["enabled"] is False
    assert plugin.cleaned is True


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
    assert bot.plugin_manager.get_plugin_info()[0]["enabled"] is False
    assert await bot.db.get_plugin_data("failing", "cleaned") == "yes"


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
        body="def on_message(self, event):\n    return self.handled('invalid')\n\n",
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
        (
            "missingcontent",
            "async def on_auto_post_published(self):\n        pass",
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
        f"    {method}\n\n"
        f"plugin = {class_name}Plugin\n",
    )

    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    assert bot.plugin_manager.get_plugin(name) is None


@pytest.mark.parametrize(
    ("name", "api_version"),
    [("boolean", "True"), ("float", "3.0"), ("legacy", "2"), ("future", "4")],
)
async def test_incompatible_plugin_api_is_rejected(
    name: str,
    api_version: str,
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
) -> None:
    plugins_dir = make_plugin_dir(
        name,
        "from twipsybot.plugin import PluginBase\n\n\n"
        "class Plugin(PluginBase):\n"
        f"    api_version = {api_version}\n\n"
        "plugin = Plugin\n",
    )

    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    assert bot.plugin_manager.get_plugin(name) is None


async def test_plugin_context_uses_stable_plugin_id(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir("keyact")

    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    plugin = bot.plugin_manager.get_plugin("keyact")
    assert plugin is not None
    assert plugin.context.name == "keyact"
    assert plugin.__class__.__module__ != "plugins.keyact.plugin"


async def test_plugin_module_uses_explicit_export(
    tmp_path: Path, make_bot: MakeBot, write_config: WriteConfig
) -> None:
    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / "echo"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "config.yaml").write_text("enabled: true\n", encoding="utf-8")
    (plugin_dir / "plugin.py").write_text(
        "from twipsybot.plugin import PluginBase\n\n"
        "class Reply(PluginBase):\n"
        "    api_version = 3\n"
        "    async def on_message(self, event):\n"
        "        return self.handled('explicit')\n\n"
        "plugin = Reply\n",
        encoding="utf-8",
    )

    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    plugin = bot.plugin_manager.get_plugin("echo")
    assert plugin is not None
    assert plugin.__class__.__name__ == "Reply"


async def test_legacy_named_module_is_not_loaded(
    tmp_path: Path, make_bot: MakeBot, write_config: WriteConfig
) -> None:
    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / "legacy"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "config.yaml").write_text("enabled: true\n", encoding="utf-8")
    (plugin_dir / "legacy.py").write_text(
        "from twipsybot.plugin import PluginBase\n\n"
        "class LegacyPlugin(PluginBase):\n"
        "    api_version = 3\n\n"
        "plugin = LegacyPlugin\n",
        encoding="utf-8",
    )

    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    assert bot.plugin_manager.get_plugin("legacy") is None


async def test_plugin_module_requires_explicit_export(
    tmp_path: Path, make_bot: MakeBot, write_config: WriteConfig
) -> None:
    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / "implicit"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "config.yaml").write_text("enabled: true\n", encoding="utf-8")
    (plugin_dir / "plugin.py").write_text(
        "from twipsybot.plugin import PluginBase\n\n"
        "class ImplicitPlugin(PluginBase):\n"
        "    api_version = 3\n",
        encoding="utf-8",
    )

    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    assert bot.plugin_manager.get_plugin("implicit") is None


async def test_failed_plugin_import_removes_partial_module(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    module_prefix = "_twipsybot_plugin_broken_import_"
    previous_modules = {name for name in sys.modules if name.startswith(module_prefix)}
    plugins_dir = make_plugin_dir(
        "broken_import",
        "from twipsybot.plugin import PluginBase\n\n"
        "class BrokenImportPlugin(PluginBase):\n"
        "    api_version = 3\n\n"
        "plugin = BrokenImportPlugin\n"
        "raise RuntimeError('broken import')\n",
    )

    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    assert bot.plugin_manager.get_plugin("broken_import") is None
    assert {name for name in sys.modules if name.startswith(module_prefix)} == (
        previous_modules
    )


async def test_entry_point_plugin_uses_central_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    make_bot: MakeBot,
    write_config: WriteConfig,
) -> None:
    import twipsybot.plugin.manager as manager_module

    class ExternalPlugin(PluginBase):
        api_version = 3

    entry_point = SimpleNamespace(name="external", load=lambda: ExternalPlugin)
    monkeypatch.setattr(
        manager_module,
        "entry_points",
        lambda *, group: [entry_point] if group == "twipsybot.plugins" else [],
    )
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "config.yaml").write_text(
        "external:\n  enabled: true\n", encoding="utf-8"
    )

    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    plugin = bot.plugin_manager.get_plugin("external")
    assert plugin is not None
    assert plugin.context.name == "external"


async def test_unconfigured_entry_point_is_not_loaded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    make_bot: MakeBot,
    write_config: WriteConfig,
) -> None:
    import twipsybot.plugin.manager as manager_module

    load = AsyncMock()
    entry_point = SimpleNamespace(name="external", load=load)
    monkeypatch.setattr(manager_module, "entry_points", lambda *, group: [entry_point])

    bot = await make_bot(write_config(), plugins_dir=tmp_path / "plugins")

    assert bot.plugin_manager.get_plugin("external") is None
    load.assert_not_called()


async def test_invalid_central_plugin_config_does_not_fall_back_to_local_config(
    tmp_path: Path, make_bot: MakeBot, write_config: WriteConfig
) -> None:
    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / "invalid_config"
    plugin_dir.mkdir(parents=True)
    (plugins_dir / "config.yaml").write_text("invalid_config: true\n", encoding="utf-8")
    (plugin_dir / "config.yaml").write_text("enabled: true\n", encoding="utf-8")
    (plugin_dir / "plugin.py").write_text(
        "from twipsybot.plugin import PluginBase\n\n"
        "class InvalidConfigPlugin(PluginBase):\n"
        "    api_version = 3\n\n"
        "plugin = InvalidConfigPlugin\n",
        encoding="utf-8",
    )

    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    assert bot.plugin_manager.get_plugin("invalid_config") is None


async def test_local_plugin_shadows_entry_point(
    monkeypatch: pytest.MonkeyPatch,
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
) -> None:
    import twipsybot.plugin.manager as manager_module

    load = AsyncMock()
    entry_point = SimpleNamespace(name="echo", load=load)
    monkeypatch.setattr(manager_module, "entry_points", lambda *, group: [entry_point])
    plugins_dir = make_plugin_dir("echo")

    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    assert bot.plugin_manager.get_plugin("echo") is not None
    load.assert_not_called()


async def test_invalid_event_input_is_rejected(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir(
        "input",
        body="async def on_message(self, event):\n"
        "    raise AssertionError('must not run')\n",
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
        body="async def on_message(self, event):\n"
        "    event.raw['user']['username'] = 'changed'\n",
        config="enabled: true\npriority: 20\n",
    )
    make_plugin_dir(
        "observer",
        body="async def on_message(self, event):\n"
        "    return self.handled(event.raw['user']['username'])\n",
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

    info = {item["name"]: item for item in bot.plugin_manager.get_plugin_info()}
    assert info["nonbool"]["enabled"] is False
    assert info["startup"]["enabled"] is False
    assert nonbool.cleaned is True
    assert startup.cleaned is True


async def test_context_uses_isolated_service_adapters(
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
) -> None:
    plugins_dir = make_plugin_dir("context")
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
    assert await bot.db.get_plugin_data("context", "key") == "value"

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
        body="def __init__(self, context):\n"
        "    super().__init__(context)\n"
        "    self.result = self.handled('ok')\n"
        "async def on_message(self, event):\n"
        "    return self.result\n",
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
        body="async def on_message(self, event):\n"
        "    raise AssertionError('must not run')\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    await bot.plugin_manager.shutdown_plugins()

    assert (
        await bot.plugin_manager.call_plugin_hook(
            "on_message", {"id": "message-1", "user": {"username": "alice"}}
        )
        == []
    )


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
    payload = {"id": "message-1", "user": {"username": "alice"}}
    active_hook = asyncio.create_task(
        bot.plugin_manager.call_plugin_hook("on_message", payload)
    )
    await hook_started.wait()
    shutdown = asyncio.create_task(bot.plugin_manager.shutdown_plugins())
    while bot.plugin_manager._accepting_hooks:
        await asyncio.sleep(0)

    assert await bot.plugin_manager.call_plugin_hook("on_message", payload) == []
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
        bot.plugin_manager.call_plugin_hook(
            "on_message", {"id": "message-1", "user": {"username": "alice"}}
        )
    )
    await hook_started.wait()

    await bot.plugin_manager.shutdown_plugins()

    assert active_hook.cancelled()
    assert hook_cancelled.is_set()


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
        bot.plugin_manager.confirm_auto_post_published(
            {"plugin_name": "publisher"}, "content"
        )
    )
    await confirmation_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert confirmation_cancelled.is_set()


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
        body="async def on_message(self, event):\n"
        "    async with self.context.bot.actor_lock(event.user.id, event.user.username):\n"
        "        return self.handled('ok')\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    results = await asyncio.wait_for(
        bot.plugin_manager.call_plugin_hook(
            "on_message",
            {"id": "message-1", "user": {"id": "user-1", "username": "alice"}},
        ),
        timeout=0.2,
    )

    assert results == [{"handled": True, "response": "ok", "plugin_name": "actor"}]


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
