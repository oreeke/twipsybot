from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import psutil
import pytest
import yaml
from conftest import MakeBot, WriteConfig
from httpx2 import Request, Response
from openai import APIStatusError

from twipsybot import Config, MisskeyBot
from twipsybot.app.cli import _write_pid_file
from twipsybot.bot.flows.post import AutoPostService
from twipsybot.bot.infra.limits import ResponseLimiter
from twipsybot.clients.openai.openai_api import OpenAIAPI
from twipsybot.db.sqlite import ConnectionPool
from twipsybot.shared.config_keys import ConfigKeys
from twipsybot.shared.exceptions import ConfigurationError


class _BadRequest:
    def __init__(
        self,
        message: str,
        code: str | None = None,
        status_code: int | None = None,
    ):
        self.message = message
        self.code = code
        self.status_code = status_code

    def __str__(self) -> str:
        return self.message


def test_invalid_config_fails_fast(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "misskey": {
                    "instance_url": "http://example.invalid",
                    "access_token": "token",
                },
                "openai": {"api_key": "key", "temperature": 5.0},
            }
        ),
        encoding="utf-8",
    )
    config = Config(config_path=str(config_path))
    with pytest.raises(ConfigurationError):
        config.load()


def test_environment_overrides_yaml_config(
    monkeypatch: pytest.MonkeyPatch,
    write_config: WriteConfig,
) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "model-from-env")
    monkeypatch.setenv("BOT_RESPONSE_RATE_LIMIT", "3")
    monkeypatch.setenv("BOT_TIMELINE_GLOBAL", "true")
    monkeypatch.setenv("DB_CLEAR", "30")

    config = write_config(
        openai={"model": "model-from-yaml"},
        bot={"timeline": {"global": False}},
    )

    assert config.get(ConfigKeys.OPENAI_MODEL) == "model-from-env"
    assert config.get(ConfigKeys.BOT_RESPONSE_RATE_LIMIT) == "3"
    assert config.get(ConfigKeys.BOT_TIMELINE_GLOBAL) is True
    assert config.data["bot"]["timeline"]["global"] is True
    assert "global_" not in config.data["bot"]["timeline"]
    assert (
        ResponseLimiter._parse_duration_seconds(
            config.get(ConfigKeys.BOT_RESPONSE_RATE_LIMIT)
        )
        == 3
    )
    assert config.get(ConfigKeys.DB_CLEAR) == 30


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (30, 30),
        ("30", 30),
        ("-1", -1),
        ("off", -1),
        ("30s", 30),
        ("5m", 300),
        ("1h", 3600),
        ("1d", 86400),
        ("1h30m", 5400),
        ("1h 30m", 5400),
        ("1w", None),
        ("1mm", None),
        ("1y", None),
        ("500ms", None),
        ("1us", None),
        ("1ns", None),
        ("1hXXX30m", None),
        ("invalid", None),
        (True, None),
    ],
)
def test_response_limit_duration_parsing(value: Any, expected: int | None) -> None:
    assert ResponseLimiter._parse_duration_seconds(value) == expected


async def test_database_persists_updates_and_cleans_expired_state(
    make_bot: MakeBot,
    write_config: WriteConfig,
) -> None:
    bot = await make_bot(write_config())

    await bot.db.set_plugin_data("test", "key", "first")
    await bot.db.set_plugin_data("test", "key", "second")
    assert await bot.db.get_plugin_data("test", "key") == "second"

    await bot.db.set_response_limit_state(
        user_id="old-user",
        last_reply_ts=1.0,
        turns=2,
        blocked_until_ts=None,
    )
    await bot.db._execute_write(
        "UPDATE response_limit_state SET updated_at = '2000-01-01 00:00:00'"
    )
    assert await bot.db.cleanup_response_limit_state() == 0
    assert await bot.db.get_response_limit_state("old-user") is not None
    assert await bot.db.cleanup_response_limit_state(max_age_days=30) == 1
    assert await bot.db.get_response_limit_state("old-user") is None


async def test_connection_returned_after_pool_close_is_closed(tmp_path: Path) -> None:
    pool = ConnectionPool(str(tmp_path / "pool.db"), max_connections=1)
    connection = await pool.get_connection()

    await pool.close_all()
    await pool.return_connection(connection)

    assert pool._pool.empty()
    assert pool._created_connections == 0
    with pytest.raises(RuntimeError, match="closed"):
        await pool.get_connection()


async def test_pool_close_releases_connection_waiters(tmp_path: Path) -> None:
    pool = ConnectionPool(str(tmp_path / "waiting.db"), max_connections=1)
    connection = await pool.get_connection()
    waiting = asyncio.create_task(pool.get_connection())
    await asyncio.sleep(0)

    await pool.close_all()

    with pytest.raises(RuntimeError, match="closed"):
        await asyncio.wait_for(waiting, timeout=0.2)
    await pool.return_connection(connection)


async def test_cancelled_pool_waiter_does_not_consume_connection() -> None:
    pool = ConnectionPool("unused.db", max_connections=1)
    connection: Any = SimpleNamespace(close=AsyncMock())
    pool._created_connections = 1
    waiting = asyncio.create_task(pool.get_connection())
    await asyncio.sleep(0)

    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    await pool.return_connection(connection)

    assert pool._pool.get_nowait() is connection


async def test_pool_close_during_connection_creation_closes_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import twipsybot.db.sqlite as sqlite_module

    connection = SimpleNamespace(execute=AsyncMock(), close=AsyncMock())
    started = asyncio.Event()
    release = asyncio.Event()

    async def connect(*args: Any, **kwargs: Any) -> Any:
        started.set()
        await release.wait()
        return connection

    monkeypatch.setattr(sqlite_module.aiosqlite, "connect", connect)
    pool = ConnectionPool("unused.db", max_connections=1)
    acquiring = asyncio.create_task(pool.get_connection())
    await started.wait()

    await pool.close_all()
    release.set()

    with pytest.raises(RuntimeError, match="closed"):
        await acquiring
    connection.close.assert_awaited_once()
    assert pool._created_connections == 0


async def test_cancel_during_connection_handoff_closes_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import twipsybot.db.sqlite as sqlite_module

    connection = SimpleNamespace(execute=AsyncMock(), close=AsyncMock())
    started = asyncio.Event()
    release = asyncio.Event()

    async def connect(*args: Any, **kwargs: Any) -> Any:
        started.set()
        await release.wait()
        return connection

    monkeypatch.setattr(sqlite_module.aiosqlite, "connect", connect)
    pool = ConnectionPool("unused.db", max_connections=1)
    acquiring = asyncio.create_task(pool.get_connection())
    await started.wait()

    async with pool._condition:
        release.set()
        await asyncio.sleep(0)
        acquiring.cancel()

    with pytest.raises(asyncio.CancelledError):
        await acquiring
    connection.close.assert_awaited_once()
    assert pool._created_connections == 0


async def test_database_manager_can_reinitialize_after_close(
    make_bot: MakeBot, write_config: WriteConfig
) -> None:
    bot = await make_bot(write_config())
    await bot.db.close()

    await bot.db.initialize()
    await bot.db.set_plugin_data("test", "key", "value")

    assert await bot.db.get_plugin_data("test", "key") == "value"


async def test_model_reset_preserves_runtime_config(
    make_bot: MakeBot, write_config: WriteConfig
) -> None:
    bot = await make_bot(
        write_config(
            openai={"model": "default-model"},
            bot={"admin": {"enabled": True}},
        )
    )
    bot.admin._handle_set_bool("chat", ConfigKeys.BOT_RESPONSE_CHAT, "off")
    await bot.admin._handle_model("temporary-model")

    response = await bot.admin._handle_model("reset")

    assert response == "已恢复默认模型: default-model"
    assert bot.openai.model == "default-model"
    assert bot.config.get(ConfigKeys.BOT_RESPONSE_CHAT) is False


async def test_admin_string_allowlist_uses_exact_match(
    make_bot: MakeBot, write_config: WriteConfig
) -> None:
    bot = await make_bot(
        write_config(
            bot={
                "admin": {
                    "enabled": True,
                    "allowed_users": "admin@example.com",
                }
            }
        )
    )

    response = await bot.admin.on_message(
        {
            "id": "message-1",
            "text": "^help",
            "user": {"id": "admin", "username": "admin"},
        }
    )

    assert response is not None
    assert "没有权限" in response


async def test_admin_can_reenable_chat(
    make_bot: MakeBot, write_config: WriteConfig
) -> None:
    bot = await make_bot(
        write_config(bot={"admin": {"enabled": True, "allowed_users": ["user-2"]}})
    )
    message = {
        "id": "message-1",
        "text": "^chat off",
        "user": {"id": "user-2", "username": "bob"},
    }
    await bot.handlers.chat.handle(message)
    assert bot.config.get("bot.response.chat") is False

    await bot.handlers.chat.handle({**message, "id": "message-2", "text": "^chat on"})

    assert bot.config.get("bot.response.chat") is True


async def test_stop_continues_after_cleanup_failure() -> None:
    bot = object.__new__(MisskeyBot)
    bot.runtime = SimpleNamespace(running=True, cleanup_tasks=AsyncMock())
    bot.plugin_manager = SimpleNamespace(
        shutdown_plugins=AsyncMock(side_effect=RuntimeError("shutdown failed")),
        cleanup_plugins=AsyncMock(),
    )
    bot.scheduler = SimpleNamespace(running=False)
    bot.streaming = SimpleNamespace(close=AsyncMock())
    bot.misskey = SimpleNamespace(close=AsyncMock())
    bot.openai = SimpleNamespace(close=AsyncMock())
    bot.db = SimpleNamespace(close=AsyncMock())

    await bot.stop()

    bot.plugin_manager.cleanup_plugins.assert_awaited_once()
    bot.runtime.cleanup_tasks.assert_awaited_once()
    bot.streaming.close.assert_awaited_once()
    bot.misskey.close.assert_awaited_once()
    bot.openai.close.assert_awaited_once()
    bot.db.close.assert_awaited_once()


async def test_auto_post_confirms_only_successful_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_note = AsyncMock(side_effect=[{}, RuntimeError("failed")])
    confirm = AsyncMock()
    bot = SimpleNamespace(
        runtime=SimpleNamespace(running=True, startup_time=None),
        misskey=SimpleNamespace(create_note=create_note),
        plugin_manager=SimpleNamespace(confirm_auto_post_published=confirm),
        format_log_text=lambda text: text,
    )
    service = AutoPostService(cast(Any, bot))
    monkeypatch.setattr(service, "_PLUGIN_POST_INTERVAL_SECONDS", 0)
    result = {"plugin_name": "Topics"}

    with pytest.raises(RuntimeError, match="failed"):
        await service._post_plugin_contents(
            result, ["published", "failed"], "public", 2, False
        )

    confirm.assert_awaited_once_with(result, "published")


def test_pid_file_uses_process_identity(tmp_path: Path) -> None:
    pid_file = tmp_path / "twipsybot.pid"
    process = psutil.Process()

    _write_pid_file(pid_file, process)

    assert json.loads(pid_file.read_text(encoding="utf-8")) == {
        "pid": process.pid,
        "create_time": process.create_time(),
    }


@pytest.mark.parametrize(
    "message",
    (
        "Model does not support the Responses API",
        "This model doesn't support the Responses API",
        "Responses API is not supported",
    ),
)
def test_responses_unavailable_recognizes_model_capability(message: str) -> None:
    assert OpenAIAPI._is_responses_unavailable(_BadRequest(message))


def test_responses_unavailable_rejects_parameter_error() -> None:
    assert not OpenAIAPI._is_responses_unavailable(
        _BadRequest("Invalid max_output_tokens")
    )


@pytest.mark.parametrize("status_code", (404, 405, 501))
def test_responses_unavailable_recognizes_http_status(status_code: int) -> None:
    assert OpenAIAPI._is_responses_unavailable(
        _BadRequest("unsupported", status_code=status_code)
    )


async def test_http_405_falls_back_to_chat_completions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import twipsybot.clients.openai.openai_api as module

    error = APIStatusError(
        "method not allowed",
        response=Response(
            405, request=Request("POST", "https://example.com/responses")
        ),
        body={},
    )
    responses = AsyncMock(side_effect=error)
    fallback = AsyncMock(return_value="fallback")
    monkeypatch.setattr(module, "make_responses_request", responses)
    api = OpenAIAPI("test", api_mode="auto")
    monkeypatch.setattr(api, "_call_api_common", fallback)

    result = await api.generate_text("hello")

    assert result == "fallback"
    fallback.assert_awaited_once()
    await api.close()


async def test_openai_client_uses_sdk_retries() -> None:
    api = OpenAIAPI("test", api_mode="auto")

    assert api.client.max_retries == 2
    await api.close()
