from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, tzinfo
from pathlib import Path
from typing import Self

import pytest
from support.events import message
from support.plugins import T2X_SOURCE
from support.types import MakeBot, MakePluginDir, WriteConfig

from twipsybot import Neuro
from twipsybot.bot.flows import post as post_module
from twipsybot.bot.flows.post import AutoPostService

type RunCommand = Callable[[Neuro, str], Awaitable[str]]
type MakeHookPlugin = Callable[[str, str], Path]


@pytest.fixture
def echo_plugin_dir(make_plugin_dir: MakePluginDir) -> Path:
    return make_plugin_dir(
        "echo",
        "from twipsybot.plugin import MessageEvent, PluginBase\n\n\n"
        "class EchoPlugin(PluginBase):\n"
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


@pytest.fixture
def make_hook_plugin(make_plugin_dir: MakePluginDir) -> MakeHookPlugin:
    def _make(hook: str, result: str) -> Path:
        return make_plugin_dir(
            "echo", body=f"async def {hook}(self, event):\n    return {result}\n"
        )

    return _make


@pytest.fixture
async def admin_bot(make_bot: MakeBot, write_config: WriteConfig) -> Neuro:
    return await make_bot(write_config(bot={"admins": ["user-2"]}))


@pytest.fixture
def run_command() -> RunCommand:
    async def _run(bot: Neuro, text: str) -> str:
        response = await bot.admin.on_message(
            message(
                {
                    "id": "message-1",
                    "text": text,
                    "user": {"id": "user-2", "username": "bob"},
                }
            )
        )
        assert response is not None
        return response

    return _run


@pytest.fixture
def today() -> str:
    return datetime.now().astimezone().date().isoformat()


@pytest.fixture
def next_day(monkeypatch: pytest.MonkeyPatch) -> Callable[[], str]:
    class Tomorrow(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> Self:
            return super().now(tz) + timedelta(days=1)

    def _advance() -> str:
        monkeypatch.setattr(post_module, "datetime", Tomorrow)
        return Tomorrow.now().astimezone().date().isoformat()

    return _advance


@pytest.fixture
def no_post_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AutoPostService, "_PLUGIN_POST_INTERVAL_SECONDS", 0)
