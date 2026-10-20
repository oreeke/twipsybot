from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from twipsybot.app import cli as app_cli
from twipsybot.app import main as app_main
from twipsybot.shared.errors import (
    APIConnectionError,
    APIPermissionError,
)
from twipsybot.shared.settings import write_settings
from twipsybot.shared.status import read_status


def test_cli_propagates_run_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["twipsybot", "run"])
    monkeypatch.setattr(app_cli.app_main, "main", lambda: 4)

    assert app_cli.main() == 4


async def test_heartbeat_reports_bot_and_stream_state(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    runner = app_main.BotRunner()
    beat = asyncio.create_task(runner._heartbeat(path))
    try:
        await asyncio.sleep(0.05)
        assert read_status(path) == {"bot": "waiting", "stream": "disconnected"}
    finally:
        beat.cancel()


async def test_startup_waits_for_settings_then_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for env in ("MISSKEY_INSTANCE_URL", "MISSKEY_ACCESS_TOKEN", "AI_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    bot = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
    monkeypatch.setattr(app_main, "Neuro", lambda _: bot)
    monkeypatch.setattr(app_main, "_RETRY_POLL_SECONDS", 0.01)
    config = app_main.Config(tmp_path)
    runner = app_main.BotRunner()
    started = asyncio.create_task(runner._start(config))
    await asyncio.sleep(0.05)
    assert not started.done()

    write_settings(
        config.secrets_path,
        {
            "misskey_url": "https://m.example",
            "misskey_token": "t",
            "ai_api_key": "k",
        },
    )

    async with asyncio.timeout(1):
        assert await started is True
    bot.start.assert_awaited_once()


async def test_startup_retries_after_connection_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for env in ("MISSKEY_INSTANCE_URL", "MISSKEY_ACCESS_TOKEN", "AI_API_KEY"):
        monkeypatch.setenv(env, "x")
    bot = SimpleNamespace(
        start=AsyncMock(side_effect=[APIConnectionError(), None]), stop=AsyncMock()
    )
    monkeypatch.setattr(app_main, "Neuro", lambda _: bot)
    monkeypatch.setattr(app_main, "_RETRY_POLL_SECONDS", 0.01)
    monkeypatch.setattr(app_main, "_RETRY_CONNECT_SECONDS", 0.05)
    error_log = Mock()
    monkeypatch.setattr(app_main.logger, "error", error_log)
    runner = app_main.BotRunner()

    async with asyncio.timeout(1):
        assert await runner._start(app_main.Config(tmp_path)) is True
    assert bot.start.await_count == 2
    bot.stop.assert_awaited_once()
    error_log.assert_called_once_with("Startup blocked: APIConnectionError")


async def test_startup_waits_on_token_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for env in ("MISSKEY_INSTANCE_URL", "MISSKEY_ACCESS_TOKEN", "AI_API_KEY"):
        monkeypatch.setenv(env, "x")
    bot = SimpleNamespace(
        start=AsyncMock(side_effect=APIPermissionError()), stop=AsyncMock()
    )
    monkeypatch.setattr(app_main, "Neuro", lambda _: bot)
    monkeypatch.setattr(app_main, "_RETRY_POLL_SECONDS", 0.01)
    monkeypatch.setattr(app_main, "_RETRY_CONNECT_SECONDS", 0.01)
    runner = app_main.BotRunner()
    started = asyncio.create_task(runner._start(app_main.Config(tmp_path)))
    await asyncio.sleep(0.1)

    assert not started.done()
    assert bot.start.await_count == 1
    runner.shutdown_event.set()
    async with asyncio.timeout(1):
        assert await started is False


async def test_startup_wait_stops_on_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for env in ("MISSKEY_INSTANCE_URL", "MISSKEY_ACCESS_TOKEN", "AI_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    runner = app_main.BotRunner()
    started = asyncio.create_task(runner._start(app_main.Config(tmp_path)))
    await asyncio.sleep(0.05)
    runner.shutdown_event.set()

    async with asyncio.timeout(1):
        assert await started is False
