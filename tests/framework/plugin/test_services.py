from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from support.fakes import FakeMisskeyServer
from support.types import MakeBot, MakePluginDir, WriteConfig

from twipsybot.plugin.services import (
    BotControlAdapter,
    MisskeyServiceAdapter,
    OpenAIServiceAdapter,
)


async def test_misskey_service_adapter_sends_message() -> None:
    send_message = AsyncMock(return_value={"id": "message-1"})
    service = MisskeyServiceAdapter(
        SimpleNamespace(drive=SimpleNamespace(), send_message=send_message)
    )

    result = await service.send_message("admin-id", "alert")

    assert result == {"id": "message-1"}
    send_message.assert_awaited_once_with("admin-id", "alert", None)


async def test_misskey_service_adapter_create_note_with_files() -> None:
    create_note = AsyncMock(return_value={})
    service = MisskeyServiceAdapter(
        SimpleNamespace(drive=SimpleNamespace(), create_note=create_note)
    )

    await service.create_note("hi", file_ids=("f1", "f2"))
    await service.create_note("plain")

    assert create_note.await_args_list[0].args == ("hi", None, None, None, ("f1", "f2"))
    assert create_note.await_args_list[1].args == ("plain", None, None, None, None)


def test_bot_control_adapter_exposes_admins_and_delegates_check() -> None:
    is_admin = Mock(return_value=True)
    adapter = BotControlAdapter(
        SimpleNamespace(
            admin=SimpleNamespace(
                admins=("u1", "alice@example.com"), is_admin=is_admin
            ),
        )
    )

    assert adapter.admins == ("u1", "alice@example.com")
    assert adapter.is_admin("u1", "alice") is True
    is_admin.assert_called_once_with("u1", "alice")


async def test_openai_service_adapter_forwards_image_options() -> None:
    generate = AsyncMock(return_value=b"png")
    adapter = OpenAIServiceAdapter(SimpleNamespace(generate_image=generate), None)

    assert (
        await adapter.generate_image("猫", model="m", size="s", api_key="k") == b"png"
    )
    generate.assert_awaited_once_with(
        "猫", model="m", size="s", quality=None, base_url=None, api_key="k"
    )


async def test_plugin_context_uses_stable_plugin_id(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir("keyact")

    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    plugin = bot.plugin_manager.plugins.get("keyact")
    assert plugin is not None
    assert plugin.context.name == "keyact"
    assert plugin.__class__.__module__ != "plugins.keyact.plugin"


async def test_context_uses_isolated_service_adapters(
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
) -> None:
    plugins_dir = make_plugin_dir("context")
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
    plugin = bot.plugin_manager.plugins.get("context")
    assert plugin is not None

    assert not hasattr(plugin.context.misskey, "access_token")
    assert not hasattr(plugin.context.openai, "client")
    with pytest.raises(TypeError):
        plugin.context.config["enabled"] = False

    await plugin.context.misskey.create_note("adapter")
    await plugin.context.storage.set("key", "value")

    assert misskey_server.calls["notes/create"][-1]["text"] == "adapter"
    assert await bot.db.get_plugin_data("context", "key") == "value"
    assert await plugin.context.misskey.get_note("note-1") == {"visibility": "public"}
    assert misskey_server.calls["notes/show"][-1]["noteId"] == "note-1"

    antenna = {"id": "antenna-1", "name": "original"}
    misskey_server.set_response("antennas/list", lambda payload: [antenna])
    antennas = await plugin.context.misskey.list_antennas()
    antennas[0]["name"] = "changed"
    assert (await plugin.context.misskey.list_antennas())[0]["name"] == "original"
    assert antenna["name"] == "original"
