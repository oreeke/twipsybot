from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from support.types import WriteConfig

import twipsybot.bot.engine.core as core_module
from twipsybot import Neuro
from twipsybot.app import main as app_main
from twipsybot.bot.engine.runtime import BotRuntime

CLOSERS = ("streaming", "misskey", "fetcher", "openai", "db")


def stub_bot(**overrides: Any) -> Any:
    bot = cast(Any, object.__new__(Neuro))
    bot.runtime = SimpleNamespace(running=True, cleanup_tasks=AsyncMock())
    bot.plugin_manager = SimpleNamespace(
        shutdown_plugins=AsyncMock(), cleanup_plugins=AsyncMock()
    )
    bot.scheduler = SimpleNamespace(running=False)
    for name in CLOSERS:
        setattr(bot, name, SimpleNamespace(close=AsyncMock()))
    for name, value in overrides.items():
        setattr(bot, name, value)
    return bot


async def test_streaming_startup_failure_closes_initialized_services(
    monkeypatch: pytest.MonkeyPatch, write_config: WriteConfig, tmp_path: Path
) -> None:
    config = write_config()
    bot = Neuro(config)
    bot.plugin_manager.plugins_dir = tmp_path / "no-plugins"
    monkeypatch.setattr(
        bot.connect,
        "setup_streaming",
        AsyncMock(side_effect=RuntimeError("streaming failed")),
    )
    closers = {name: AsyncMock(wraps=getattr(bot, name).close) for name in CLOSERS}
    for name, closer in closers.items():
        monkeypatch.setattr(getattr(bot, name), "close", closer)
    monkeypatch.setattr(app_main, "Config", lambda: config)
    monkeypatch.setattr(app_main, "Neuro", lambda _: bot)

    runner = app_main.BotRunner()

    with pytest.raises(RuntimeError, match="streaming failed"):
        await runner.run()

    assert bot.runtime.running is False
    assert not bot.scheduler.running
    for closer in closers.values():
        closer.assert_awaited_once()


async def test_runtime_spawn_cancels_background_tasks_on_cleanup() -> None:
    runtime = BotRuntime()
    done = runtime.spawn(asyncio.sleep(0))
    pending = runtime.spawn(asyncio.sleep(10))
    await done

    await runtime.cleanup_tasks()

    assert pending.cancelled()


async def test_stop_continues_after_cleanup_failure() -> None:
    bot = stub_bot(
        plugin_manager=SimpleNamespace(
            shutdown_plugins=AsyncMock(side_effect=RuntimeError("shutdown failed")),
            cleanup_plugins=AsyncMock(),
        )
    )

    await bot.stop()

    bot.plugin_manager.cleanup_plugins.assert_awaited_once()
    bot.runtime.cleanup_tasks.assert_awaited_once()
    for name in CLOSERS:
        getattr(bot, name).close.assert_awaited_once()


async def test_stop_cancels_remaining_cleanup_after_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(core_module, "_SHUTDOWN_TIMEOUT_SECONDS", 0.01)
    close_cancelled = asyncio.Event()

    async def blocking_close() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            close_cancelled.set()
            raise

    bot = stub_bot(db=SimpleNamespace(close=blocking_close))

    await bot.stop()

    assert close_cancelled.is_set()
