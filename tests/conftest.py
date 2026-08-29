from __future__ import annotations

import itertools
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Protocol

import pytest
import pytest_asyncio
import yaml
from aiohttp import web
from aiohttp.test_utils import TestServer

from twipsybot import Config, MisskeyBot

__all__ = (
    "DEFAULT_AI_REPLY",
    "FakeMisskeyServer",
    "FakeOpenAIServer",
    "MakeBot",
    "MakePluginDir",
    "WriteConfig",
)

DEFAULT_AI_REPLY = "这是 AI 生成的回复"


class WriteConfig(Protocol):
    def __call__(self, **overrides: Any) -> Config: ...


class MakeBot(Protocol):
    async def __call__(
        self, config: Config, *, plugins_dir: Path | None = None
    ) -> MisskeyBot: ...


class MakePluginDir(Protocol):
    def __call__(
        self, name: str, source: str, *, config: str = "enabled: true\n"
    ) -> Path: ...


class FakeMisskeyServer:
    def __init__(
        self,
        server: TestServer,
        calls: dict[str, list[dict[str, Any]]],
        responses: dict[str, Any],
    ):
        self._server = server
        self.calls = calls
        self._responses = responses

    @property
    def url(self) -> str:
        return str(self._server.make_url("/")).rstrip("/")

    def set_response(self, endpoint: str, response: Any) -> None:
        self._responses[endpoint] = response


def _build_misskey_app(
    calls: dict[str, list[dict[str, Any]]], responses: dict[str, Any]
) -> web.Application:
    note_ids = itertools.count(1)
    responses.update(
        {
            "i": lambda payload: {"id": "bot-id", "username": "testbot"},
            "notes/create": lambda payload: {
                "createdNote": {
                    "id": f"note-{next(note_ids)}",
                    "visibility": payload.get("visibility", "public"),
                }
            },
            "notes/show": lambda payload: {"visibility": "public"},
            "chat/messages/create-to-user": lambda payload: {"id": "msg-1"},
            "chat/messages/create-to-room": lambda payload: {"id": "msg-1"},
            "chat/messages/user-timeline": lambda payload: [],
            "chat/messages/room-timeline": lambda payload: [],
            "antennas/list": lambda payload: [],
        }
    )

    async def handle(request: web.Request) -> web.Response:
        endpoint = request.match_info["endpoint"]
        payload = await request.json()
        calls.setdefault(endpoint, []).append(payload)
        responder = responses.get(endpoint)
        return web.json_response(responder(payload) if responder else {})

    app = web.Application()
    app.router.add_post("/api/{endpoint:.*}", handle)
    return app


@pytest_asyncio.fixture
async def misskey_server() -> AsyncIterator[FakeMisskeyServer]:
    calls: dict[str, list[dict[str, Any]]] = {}
    responses: dict[str, Any] = {}
    server = TestServer(_build_misskey_app(calls, responses))
    await server.start_server()
    try:
        yield FakeMisskeyServer(server, calls, responses)
    finally:
        await server.close()


class FakeOpenAIServer:
    def __init__(
        self, server: TestServer, calls: list[dict[str, Any]], reply: dict[str, str]
    ):
        self._server = server
        self.calls = calls
        self._reply = reply

    @property
    def url(self) -> str:
        return str(self._server.make_url("/v1")).rstrip("/")

    def set_reply(self, text: str) -> None:
        self._reply["text"] = text


def _build_openai_app(
    calls: list[dict[str, Any]], reply: dict[str, str]
) -> web.Application:
    async def chat_completions(request: web.Request) -> web.Response:
        payload = await request.json()
        calls.append(payload)
        return web.json_response(
            {
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 0,
                "model": payload.get("model", "test-model"),
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": reply["text"]},
                    }
                ],
            }
        )

    app = web.Application()
    app.router.add_post("/v1/chat/completions", chat_completions)
    return app


@pytest_asyncio.fixture
async def openai_server() -> AsyncIterator[FakeOpenAIServer]:
    calls: list[dict[str, Any]] = []
    reply = {"text": DEFAULT_AI_REPLY}
    server = TestServer(_build_openai_app(calls, reply))
    await server.start_server()
    try:
        yield FakeOpenAIServer(server, calls, reply)
    finally:
        await server.close()


@pytest.fixture
def write_config(
    tmp_path: Path, misskey_server: FakeMisskeyServer, openai_server: FakeOpenAIServer
) -> WriteConfig:
    def merge(target: dict[str, Any], updates: dict[str, Any]) -> None:
        for key, value in updates.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                merge(target[key], value)
            else:
                target[key] = value

    def _write(**overrides: Any) -> Config:
        data: dict[str, Any] = {
            "misskey": {
                "instance_url": misskey_server.url,
                "access_token": "test-token",
            },
            "openai": {
                "api_key": "test-key",
                "api_base": openai_server.url,
                "api_mode": "chat",
            },
            "bot": {
                "system_prompt": "你是测试机器人",
                "auto_post": {"prompt": "写一条随笔"},
            },
            "db": {"path": str(tmp_path / "test.db")},
            "log": {"path": str(tmp_path / "test.log")},
        }
        if blacklist := overrides.pop("blacklist", None):
            data["bot"]["response"] = {"blacklist": blacklist}
        merge(data, overrides)
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            yaml.safe_dump(data, allow_unicode=True), encoding="utf-8"
        )
        config = Config(config_path=str(config_path))
        config.load()
        return config

    return _write


@pytest.fixture
def make_plugin_dir(tmp_path: Path) -> MakePluginDir:
    plugins_dir = tmp_path / "plugins"

    def _make(name: str, source: str, *, config: str = "enabled: true\n") -> Path:
        plugin_dir = plugins_dir / name
        plugin_dir.mkdir(parents=True, exist_ok=True)
        (plugin_dir / "config.yaml").write_text(config, encoding="utf-8")
        (plugin_dir / f"{name}.py").write_text(source, encoding="utf-8")
        return plugins_dir

    return _make


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
        "        return self.handled('echo: plugin took over')\n",
    )


@pytest_asyncio.fixture
async def make_bot(tmp_path: Path) -> AsyncIterator[MakeBot]:
    created: list[MisskeyBot] = []

    async def _make(config: Config, *, plugins_dir: Path | None = None) -> MisskeyBot:
        bot = MisskeyBot(config)
        bot.plugin_manager.plugins_dir = plugins_dir or (tmp_path / "no-plugins")
        await bot.db.initialize()
        bot.openai.initialize()
        current_user = await bot.misskey.get_current_user()
        bot.bot_user_id = current_user.get("id")
        bot.bot_username = current_user.get("username")
        await bot.plugin_manager.load_plugins()
        if config.get("bot.admin.enabled"):
            await bot.admin.start()
        await bot.plugin_manager.startup_plugins()
        created.append(bot)
        return bot

    yield _make

    for bot in created:
        await bot.plugin_manager.shutdown_plugins()
        await bot.plugin_manager.cleanup_plugins()
        await bot.streaming.close()
        await bot.misskey.close()
        await bot.openai.close()
        await bot.db.close()
