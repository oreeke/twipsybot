from __future__ import annotations

from pathlib import Path

import pytest
from support.plugins import T2X_SOURCE
from support.types import MakePluginDir


@pytest.fixture
def echo_plugin_dir(make_plugin_dir: MakePluginDir) -> Path:
    return make_plugin_dir(
        "echo",
        "from twipsybot.plugin import PLUGIN_API_VERSION, MessageEvent, PluginBase\n\n\n"
        "class EchoPlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n\n"
        "    async def on_message(self, event: MessageEvent):\n"
        "        assert event.id == 'msg-1'\n"
        "        assert event.text == '你好，机器人'\n"
        "        assert event.user.username == 'bob'\n"
        "        return self.handled('echo: plugin took over')\n\n"
        "plugin = EchoPlugin\n",
    )


@pytest.fixture
def t2x_dir(make_plugin_dir: MakePluginDir) -> Path:
    return make_plugin_dir(
        "t2x",
        T2X_SOURCE,
        config="enabled: true\nimage:\n  model: gpt-image-1\n",
    )
