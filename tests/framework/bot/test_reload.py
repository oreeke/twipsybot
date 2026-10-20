from __future__ import annotations

import ipaddress
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from support.fakes import FakeMisskeyServer
from support.types import MakeBot, WriteConfig

from twipsybot.bot.admin import handlers
from twipsybot.shared.keys import ConfigKeys
from twipsybot.shared.settings import read_settings, write_settings


async def test_settings_reload_applies_live_and_flags_restart(
    make_bot: MakeBot, write_config: WriteConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = await make_bot(write_config())
    warning = Mock()
    monkeypatch.setattr("twipsybot.bot.engine.reload.logger.warning", warning)
    monkeypatch.setenv("AI_API_KEY", "rotated-key")
    settings = read_settings(bot.config.settings_path)
    settings["bot"] = {
        "system_prompt": "新的人设",
        "admins": ["user-9"],
        "model": "live-model",
        "api_mode": "responses",
    }
    settings["timeline"] = {"home": True}
    settings["system"] = {"dump_events": True, "allow_nets": ["198.18.0.1/15"]}
    write_settings(bot.config.settings_path, settings)

    assert await bot.settings.reload() == {}

    assert bot.openai.model == "live-model"
    assert bot.openai.api_mode == "responses"
    assert bot.system_prompt == "新的人设"
    assert handlers.admins(bot) == ["user-9"]
    assert bot.streaming.log_dump_events is True
    assert bot.config.get(ConfigKeys.SYSTEM_ALLOW_NETS) == ["198.18.0.0/15"]
    for fetcher in (bot.fetcher, bot.misskey.fetcher):
        assert fetcher.is_public(ipaddress.ip_address("198.19.1.1"))
    warning.assert_called_once_with("Restart required for: connect.ai_api_key")


async def test_settings_reload_syncs_timeline_channels(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
) -> None:
    misskey_server.set_response(
        "antennas/list",
        lambda payload: [
            {"id": "antenna-1", "name": "Radar"},
            {"id": "antenna-2", "name": "Other"},
        ],
    )
    bot = await make_bot(write_config(timeline={"home": True}))
    bot.streaming.running = True
    await bot.streaming.connect_channel("main")
    await bot.streaming.connect_channel("homeTimeline")
    await bot.streaming.connect_channel("chatUser", {"otherId": "u1"})

    def subscribed() -> list[tuple[str, dict[str, Any]]]:
        return sorted((c["name"], c["params"]) for c in bot.streaming.channels.values())

    settings = read_settings(bot.config.settings_path)
    settings["timeline"] = {"local": True, "antennas": ["Radar"]}
    write_settings(bot.config.settings_path, settings)
    await bot.settings.reload()

    assert subscribed() == [
        ("antenna", {"antennaId": "antenna-1"}),
        ("chatUser", {"otherId": "u1"}),
        ("localTimeline", {}),
        ("main", {}),
    ]

    settings["timeline"] = {"local": True}
    write_settings(bot.config.settings_path, settings)
    await bot.settings.reload()

    assert subscribed() == [
        ("chatUser", {"otherId": "u1"}),
        ("localTimeline", {}),
        ("main", {}),
    ]


async def test_timeline_sync_keeps_going_when_a_channel_fails(
    make_bot: MakeBot, write_config: WriteConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = await make_bot(write_config())
    bot.streaming.running = True
    connect = AsyncMock(side_effect=[RuntimeError("boom"), "channel-id"])
    monkeypatch.setattr(bot.streaming, "connect_channel", connect)
    settings = read_settings(bot.config.settings_path)
    settings["timeline"] = {"home": True, "local": True}
    write_settings(bot.config.settings_path, settings)

    await bot.settings.reload()

    assert [c.args[0] for c in connect.await_args_list] == [
        "homeTimeline",
        "localTimeline",
    ]


async def test_settings_reload_keeps_current_on_invalid_file(
    make_bot: MakeBot, write_config: WriteConfig
) -> None:
    bot = await make_bot(write_config())
    bot.config.settings_path.write_text("reply: {memory: 999}\n", encoding="utf-8")

    assert await bot.settings.reload() == {}
    assert bot.config.get(ConfigKeys.REPLY_MEMORY) == 10
    assert bot.config.fingerprint == bot.config.stat()
