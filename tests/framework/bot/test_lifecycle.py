from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from support.types import WriteConfig

from twipsybot import Neuro
from twipsybot.app import main as app_main
from twipsybot.bot.engine.runtime import BotRuntime


async def test_streaming_startup_failure_closes_initialized_services(
    monkeypatch: pytest.MonkeyPatch, write_config: WriteConfig
) -> None:
    config = write_config()
    bot = Neuro(config)
    monkeypatch.setattr(bot, "_initialize_services", AsyncMock())
    monkeypatch.setattr(bot, "_setup_scheduler", Mock())
    monkeypatch.setattr(
        bot.connect,
        "setup_streaming",
        AsyncMock(side_effect=RuntimeError("streaming failed")),
    )
    monkeypatch.setattr(bot.plugin_manager, "shutdown_plugins", AsyncMock())
    monkeypatch.setattr(bot.plugin_manager, "cleanup_plugins", AsyncMock())
    monkeypatch.setattr(bot.runtime, "cleanup_tasks", AsyncMock())
    monkeypatch.setattr(bot.streaming, "close", AsyncMock())
    monkeypatch.setattr(bot.misskey, "close", AsyncMock())
    monkeypatch.setattr(bot.openai, "close", AsyncMock())
    monkeypatch.setattr(bot.db, "close", AsyncMock())
    bot.scheduler = SimpleNamespace(running=True, shutdown=Mock())
    monkeypatch.setattr(app_main, "Config", lambda: config)
    monkeypatch.setattr(app_main, "Neuro", lambda _: bot)
    runner = app_main.BotRunner()

    with pytest.raises(RuntimeError, match="streaming failed"):
        await runner.run()

    assert bot.runtime.running is False
    bot.plugin_manager.shutdown_plugins.assert_awaited_once()
    bot.plugin_manager.cleanup_plugins.assert_awaited_once()
    bot.scheduler.shutdown.assert_called_once_with(wait=False)
    bot.runtime.cleanup_tasks.assert_awaited_once()
    bot.streaming.close.assert_awaited_once()
    bot.misskey.close.assert_awaited_once()
    bot.openai.close.assert_awaited_once()
    bot.db.close.assert_awaited_once()


async def test_runtime_spawn_tracks_and_cancels_background_tasks() -> None:
    runtime = BotRuntime()
    done = runtime.spawn(asyncio.sleep(0))
    pending = runtime.spawn(asyncio.sleep(10))
    await done
    await asyncio.sleep(0)

    assert runtime._background == {pending}

    await runtime.cleanup_tasks()

    assert pending.cancelled()
    assert not runtime._background


async def test_stop_continues_after_cleanup_failure() -> None:
    bot = object.__new__(Neuro)
    bot.runtime = SimpleNamespace(running=True, cleanup_tasks=AsyncMock())
    bot.plugin_manager = SimpleNamespace(
        shutdown_plugins=AsyncMock(side_effect=RuntimeError("shutdown failed")),
        cleanup_plugins=AsyncMock(),
    )
    bot.scheduler = SimpleNamespace(running=False)
    bot.streaming = SimpleNamespace(close=AsyncMock())
    bot.misskey = SimpleNamespace(close=AsyncMock())
    bot.fetcher = SimpleNamespace(close=AsyncMock())
    bot.openai = SimpleNamespace(close=AsyncMock())
    bot.db = SimpleNamespace(close=AsyncMock())

    await bot.stop()

    bot.plugin_manager.cleanup_plugins.assert_awaited_once()
    bot.runtime.cleanup_tasks.assert_awaited_once()
    bot.streaming.close.assert_awaited_once()
    bot.misskey.close.assert_awaited_once()
    bot.fetcher.close.assert_awaited_once()
    bot.openai.close.assert_awaited_once()
    bot.db.close.assert_awaited_once()


async def test_stop_cancels_remaining_cleanup_after_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import twipsybot.bot.engine.core as core_module

    monkeypatch.setattr(core_module, "_SHUTDOWN_TIMEOUT_SECONDS", 0.01)
    close_cancelled = asyncio.Event()

    async def blocking_close() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            close_cancelled.set()
            raise

    bot = object.__new__(Neuro)
    bot.runtime = SimpleNamespace(running=True, cleanup_tasks=AsyncMock())
    bot.plugin_manager = SimpleNamespace(
        shutdown_plugins=AsyncMock(), cleanup_plugins=AsyncMock()
    )
    bot.scheduler = SimpleNamespace(running=False)
    bot.streaming = SimpleNamespace(close=AsyncMock())
    bot.misskey = SimpleNamespace(close=AsyncMock())
    bot.fetcher = SimpleNamespace(close=AsyncMock())
    bot.openai = SimpleNamespace(close=AsyncMock())
    bot.db = SimpleNamespace(close=blocking_close)

    await bot.stop()

    assert close_cancelled.is_set()
