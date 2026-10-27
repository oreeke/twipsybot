from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from textwrap import indent
from typing import Any

import pytest
import pytest_asyncio
import yaml
from aiohttp.test_utils import TestServer
from support.factories import set_plugin_config
from support.fakes import (
    DEFAULT_AI_REPLY,
    FakeMisskeyServer,
    FakeOpenAIServer,
    build_misskey_app,
    build_openai_app,
)
from support.types import MakeBot, MakePluginDir, WriteConfig

from twipsybot import Config, Neuro
from twipsybot.shared.settings import read_settings, write_settings


@pytest_asyncio.fixture
async def misskey_server() -> AsyncIterator[FakeMisskeyServer]:
    calls: dict[str, list[dict[str, Any]]] = {}
    responses: dict[str, Any] = {}
    server = TestServer(build_misskey_app(calls, responses))
    await server.start_server()
    try:
        yield FakeMisskeyServer(server, calls, responses)
    finally:
        await server.close()


@pytest_asyncio.fixture
async def openai_server() -> AsyncIterator[FakeOpenAIServer]:
    calls: list[dict[str, Any]] = []
    reply: dict[str, Any] = {"text": DEFAULT_AI_REPLY}
    server = TestServer(build_openai_app(calls, reply))
    await server.start_server()
    try:
        yield FakeOpenAIServer(server, calls, reply)
    finally:
        await server.close()


def _merge(target: dict[str, Any], updates: dict[str, Any]) -> None:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = value


@pytest.fixture
def write_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> WriteConfig:
    monkeypatch.setenv("MISSKEY_INSTANCE_URL", misskey_server.url)
    monkeypatch.setenv("MISSKEY_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("AI_API_KEY", "test-key")
    monkeypatch.setenv("AI_BASE_URL", openai_server.url)
    settings_path = tmp_path / "data" / "settings.yaml"

    def _write(*, load: bool = True, **overrides: Any) -> Config:
        data: dict[str, Any] = {
            "bot": {"system_prompt": "你是测试机器人", "api_mode": "chat"},
            "autopost": {"prompt": "写一条随笔", "mode": "rotation"},
        }
        if plugins := read_settings(settings_path).get("plugins"):
            data["plugins"] = plugins
        if blacklist := overrides.pop("blacklist", None):
            data["reply"] = {"blacklist": blacklist}
        _merge(data, overrides)
        write_settings(settings_path, data)
        config = Config(tmp_path)
        if load:
            config.load()
        return config

    return _write


@pytest.fixture
def make_plugin_dir(tmp_path: Path) -> MakePluginDir:
    plugins_dir = tmp_path / "plugins"

    def _make(
        name: str,
        source: str | None = None,
        *,
        body: str = "",
        config: str = "enabled: true\n",
    ) -> Path:
        plugin_dir = plugins_dir / name
        plugin_dir.mkdir(parents=True, exist_ok=True)
        set_plugin_config(tmp_path, name, yaml.safe_load(config) or {})
        if source is None:
            source = (
                "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n"
                "class Plugin(PluginBase):\n"
                "    api_version = PLUGIN_API_VERSION\n"
                f"{indent(body, '    ')}\n"
                "plugin = Plugin\n"
            )
        if "\nplugin = " not in source:
            raise ValueError("test plugin source must explicitly export plugin")
        (plugin_dir / "plugin.py").write_text(source, encoding="utf-8")
        return plugins_dir

    return _make


@pytest_asyncio.fixture
async def make_bot(tmp_path: Path) -> AsyncIterator[MakeBot]:
    created: list[Neuro] = []

    async def _make(config: Config, *, plugins_dir: Path | None = None) -> Neuro:
        config.load()
        bot = Neuro(config)
        bot.plugin_manager.plugins_dir = plugins_dir or (tmp_path / "no-plugins")
        await bot.db.initialize()
        await bot.auto_post.start()
        current_user = await bot.misskey.get_current_user()
        bot.bot_user_id = current_user.get("id")
        bot.bot_username = current_user.get("username")
        await bot.plugin_manager.load_plugins()
        await bot.plugin_manager.startup_plugins()
        created.append(bot)
        return bot

    yield _make

    for bot in created:
        await bot.plugin_manager.shutdown_plugins()
        await bot.plugin_manager.cleanup_plugins()
        await bot.streaming.close()
        await bot.misskey.close()
        await bot.fetcher.close()
        await bot.openai.close()
        await bot.db.close()
