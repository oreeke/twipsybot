from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from support.types import MakeBot, MakePluginDir, WriteConfig

from twipsybot import Neuro

type Boot = Callable[..., Awaitable[Neuro]]


@pytest.fixture
def boot(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> Boot:
    async def _boot(**bodies: str) -> Neuro:
        plugins_dir = None
        for name, body in bodies.items():
            plugins_dir = make_plugin_dir(name, body=body)
        return await make_bot(write_config(), plugins_dir=plugins_dir)

    return _boot
