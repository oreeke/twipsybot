from __future__ import annotations

from support.fakes import FakeMisskeyServer
from support.types import MakeBot, WriteConfig

from twipsybot import Neuro


async def test_timeline_channels_are_independently_enabled(
    write_config: WriteConfig,
) -> None:
    config = write_config(timeline={"home": True, "local": False})
    bot = Neuro(config)

    assert await bot.connect.get_streaming_channels() == ["main", "homeTimeline"]
    assert config.data["timeline"]["global"] is False
    assert "global_" not in config.data["timeline"]


async def test_streaming_channels_follow_timeline_and_antenna_config(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
) -> None:
    misskey_server.set_response(
        "antennas/list",
        lambda payload: [
            {"id": "antenna-1", "name": "Shared"},
            {"id": "antenna-2", "name": "Shared"},
            {"id": "antenna-3", "name": "Unique"},
        ],
    )
    bot = await make_bot(
        write_config(
            timeline={
                "home": True,
                "local": True,
                "hybrid": False,
                "global": False,
                "antennas": [
                    "antenna-1",
                    "Unique",
                    "Shared",
                    "missing",
                    "antenna-1",
                ],
            }
        )
    )

    assert await bot.connect.get_streaming_channels() == [
        "main",
        "homeTimeline",
        "localTimeline",
        ("antenna", {"antennaId": "antenna-1"}),
        ("antenna", {"antennaId": "antenna-3"}),
    ]
    assert len(misskey_server.calls["antennas/list"]) == 1
