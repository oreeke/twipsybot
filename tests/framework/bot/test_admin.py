from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from apscheduler.schedulers.base import STATE_PAUSED, STATE_RUNNING, STATE_STOPPED
from support.events import message
from support.factories import set_plugin_config
from support.fakes import FakeOpenAIServer
from support.flows import CHAT_MESSAGE
from support.types import MakeBot, MakePluginDir, WriteConfig

from twipsybot import Neuro
from twipsybot.bot.admin import handlers
from twipsybot.shared.settings import read_settings

type RunCommand = Callable[[Neuro, str], Awaitable[str]]


async def test_admin_check_matches_ids_and_canonical_handles(
    make_bot: MakeBot, write_config: WriteConfig
) -> None:
    bot = await make_bot(write_config())
    local = bot.limits.canonical_handle("alice")
    bot.config.settings.bot.admins = ["user-1", local, "carol@remote.example"]

    admin = bot.admin
    assert admin.is_admin("user-1", None)
    assert admin.is_admin("other", "alice")
    assert admin.is_admin("other", "carol@remote.example")
    assert not admin.is_admin("other", "carol")
    assert not admin.is_admin(None, "alice")
    assert not admin.is_admin("other", None)


async def test_admin_string_allowlist_uses_exact_match(
    make_bot: MakeBot, write_config: WriteConfig
) -> None:
    bot = await make_bot(write_config(bot={"admins": ["admin@example.com"]}))

    response = await bot.admin.on_message(
        message(
            {
                "id": "message-1",
                "text": "^help",
                "user": {"id": "admin", "username": "admin"},
            }
        )
    )

    assert response is not None
    assert "没有权限" in response


@pytest.mark.parametrize("plugin_count", [0, 5])
async def test_admin_status_summarizes_runtime_without_leaking_secrets(
    plugin_count: int,
    make_bot: MakeBot,
    write_config: WriteConfig,
    run_command: RunCommand,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = await make_bot(write_config(bot={"admins": ["user-2"]}))
    bot.runtime.startup_time = datetime.now(UTC)
    bot.streaming.state = "connected"
    bot.streaming.channels = {
        "main": {"name": "main"},
        "home": {"name": "homeTimeline"},
        "antenna-1": {"name": "antenna"},
        "antenna-2": {"name": "antenna"},
        "chat": {"name": "chatUser"},
    }
    monkeypatch.setattr(
        bot.plugin_manager,
        "get_plugin_states",
        Mock(return_value={f"p{i}": True for i in range(plugin_count)}),
    )

    response = await run_command(bot, "^status")

    assert response.count("```") == 2
    assert f"实例  {bot.me.host}" in response
    assert "test-token" not in response
    assert "频道  main · home" in response
    assert "antenna x2 · chat x1" in response
    assert "授权  1" in response.splitlines()
    assert ("插件  5/5 已启用" in response) is bool(plugin_count)


@pytest.mark.parametrize(
    ("state", "running", "expected"),
    [
        (STATE_RUNNING, True, "🟩"),
        (STATE_PAUSED, True, "🟨"),
        (STATE_STOPPED, True, "🟥"),
        (STATE_STOPPED, False, "🟨"),
    ],
)
async def test_admin_status_reports_scheduler_state(
    state: int,
    running: bool,
    expected: str,
    make_bot: MakeBot,
    write_config: WriteConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = await make_bot(write_config())
    monkeypatch.setattr(bot.runtime, "running", running)
    monkeypatch.setattr(bot.scheduler, "state", state)

    assert handlers.task_status_text(bot) == f"任务  🟨 stream · {expected} scheduler"


@pytest.mark.parametrize(
    ("outcome", "running", "expected"),
    [
        ("running", True, "🟩"),
        ("success", True, "🟥"),
        ("error", True, "🟥"),
        ("cancel", True, "🟥"),
        ("success", False, "🟨"),
    ],
)
async def test_admin_status_reports_stream_task_state(
    outcome: str,
    running: bool,
    expected: str,
    make_bot: MakeBot,
    write_config: WriteConfig,
) -> None:
    bot = await make_bot(write_config())
    bot.runtime.running = running

    async def run() -> None:
        if outcome in {"running", "cancel"}:
            await asyncio.Event().wait()
        if outcome == "error":
            raise ValueError("private details")

    task = bot.runtime.add_task("streaming", run())
    try:
        if outcome == "cancel":
            task.cancel()
        if outcome != "running":
            await asyncio.gather(task, return_exceptions=True)
        text = handlers.status_text(bot)
        scheduler = "🟥" if running else "🟨"
        assert f"任务  {expected} stream · {scheduler} scheduler" in text.splitlines()
        status = "🟩 运行中" if running else "🟨 未运行"
        assert f"状态  {status} · " in text
        assert "private details" not in text
    finally:
        bot.runtime.running = False
        await bot.runtime.cleanup_tasks()


@pytest.mark.parametrize(
    ("autopost", "count", "scheduler_state", "has_job", "expected"),
    [
        ({"mode": "off"}, 2, 1, True, "发帖  已关闭"),
        ({}, 5, 1, True, "发帖  轮转 5/5 · 今日已达上限"),
        ({}, 2, 0, True, "发帖  轮转 2/5 · 未调度"),
        ({}, 2, 2, True, "发帖  轮转 2/5 · 未调度"),
        ({}, 2, 1, False, "发帖  轮转 2/5 · 未调度"),
        ({}, 2, 1, True, "发帖  轮转 2/5 · 下次"),
        (
            {"mode": "schedule", "times": ["09:00", "21:00"]},
            9,
            1,
            True,
            "发帖  定时 2 个时间点 · 下次",
        ),
    ],
)
async def test_admin_status_reports_auto_post_schedule(
    autopost: dict[str, Any],
    count: int,
    scheduler_state: int,
    has_job: bool,
    expected: str,
    make_bot: MakeBot,
    write_config: WriteConfig,
    monkeypatch: pytest.MonkeyPatch,
    today: str,
) -> None:
    bot = await make_bot(write_config(autopost={"daily_max": 5, **autopost}))
    await bot.db.set_auto_post_state(today, count)
    await bot.auto_post.start()
    next_run = datetime(2026, 9, 9, 14, 30, tzinfo=UTC)
    job = SimpleNamespace(next_run_time=next_run) if has_job else None
    scheduler = SimpleNamespace(state=scheduler_state, get_job=Mock(return_value=job))
    monkeypatch.setattr(bot, "scheduler", scheduler)

    text = handlers.auto_post_status_text(bot)

    assert text.startswith(expected)
    if expected.endswith("下次"):
        assert next_run.astimezone().strftime("%m-%d %H:%M %z") in text
    else:
        assert "下次" not in text


async def test_admin_commands_persist_to_settings(
    make_bot: MakeBot, write_config: WriteConfig
) -> None:
    bot = await make_bot(write_config())
    await handlers.set_bool(bot, "chat", "off")
    await handlers.set_model(bot, "temporary-model")

    assert bot.openai.model == "temporary-model"
    assert read_settings(bot.config.settings_path)["bot"]["model"] == "temporary-model"

    response = await handlers.set_model(bot, "reset")

    assert response == "已恢复默认模型: gpt-6-luna"
    assert bot.openai.model == "gpt-6-luna"
    assert "model" not in read_settings(bot.config.settings_path)["bot"]
    assert bot.config.settings.reply.chat is False


async def test_admin_resets_auto_post_counter(
    make_bot: MakeBot, write_config: WriteConfig, run_command: RunCommand, today: str
) -> None:
    bot = await make_bot(write_config(bot={"admins": ["user-2"]}))
    await bot.auto_post.post_count()

    response = await run_command(bot, "^autopost reset")

    assert "自动发帖计数器已重置" in response
    assert bot.auto_post.posts_today == 0
    assert await bot.db.get_auto_post_state() == (today, 0)


async def test_admin_autopost_switches_mode(
    make_bot: MakeBot, write_config: WriteConfig
) -> None:
    bot = await make_bot(write_config())

    assert "需要先" in str(await handlers.set_autopost(bot, "schedule"))
    assert bot.auto_post.mode == "rotation"
    assert await handlers.set_autopost(bot, "on") is None

    await bot.settings.update({"autopost.times": ["09:00"]})
    assert await handlers.set_autopost(bot, "schedule") == "autopost: schedule"
    assert read_settings(bot.config.settings_path)["autopost"]["mode"] == "schedule"
    assert await handlers.set_autopost(bot, "off") == "autopost: off"
    assert bot.auto_post.mode == "off"


async def test_admin_lists_and_toggles_plugins(
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
    run_command: RunCommand,
) -> None:
    bot = await make_bot(
        write_config(bot={"admins": ["user-2"]}),
        plugins_dir=make_plugin_dir("demo"),
    )

    assert "1/1 已启用\n● demo" in await run_command(bot, "^plugin")
    assert "demo 已启用" in await run_command(bot, "^plugin on demo")
    assert "demo 已停用" in await run_command(bot, "^plugin off demo")
    assert "0/1 已启用\n○ demo" in await run_command(bot, "^plugin list")
    plugin_settings = read_settings(bot.config.settings_path)["plugins"]["demo"]
    assert plugin_settings["enabled"] is False
    assert "demo 已启用" in await run_command(bot, "^plugin on demo")
    assert bot.plugin_manager.get_plugin_states() == {"demo": True}
    assert "未知插件: ghost" in await run_command(bot, "^plugin on ghost")
    assert "用法: ^plugin on|off <插件名>" in await run_command(bot, "^plugin on")
    assert "未知命令: reload" in await run_command(bot, "^reload demo")


async def test_admin_plugin_on_rolls_back_when_start_fails(
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
    run_command: RunCommand,
) -> None:
    plugins_dir = make_plugin_dir(
        "demo", body="async def on_startup(self):\n    raise RuntimeError('boom')\n"
    )
    config = write_config(bot={"admins": ["user-2"]})
    set_plugin_config(config.root, "demo", {"enabled": False})
    bot = await make_bot(config, plugins_dir=plugins_dir)

    response = await run_command(bot, "^plugin on demo")

    assert "demo 启用失败，详见日志" in response
    plugin_settings = read_settings(bot.config.settings_path)["plugins"]["demo"]
    assert plugin_settings["enabled"] is False


async def test_admin_chat_commands_skip_plugins_limits_and_ai_history(
    make_bot: MakeBot,
    write_config: WriteConfig,
    openai_server: FakeOpenAIServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = await make_bot(
        write_config(bot={"admins": ["user-2"]}, reply={"rate_limit": "1h"})
    )
    plugin_hook = AsyncMock(wraps=bot.plugin_manager.call_plugin_hook)
    monkeypatch.setattr(bot.plugin_manager, "call_plugin_hook", plugin_hook)
    raw = {**CHAT_MESSAGE, "text": "^chat off"}

    await bot.chat.handle(message(raw))
    assert bot.config.settings.reply.chat is False

    await bot.chat.handle(message({**raw, "id": "message-2", "text": "^chat on"}))

    assert bot.config.settings.reply.chat is True
    plugin_hook.assert_not_awaited()
    assert await bot.db.get_response_limit_state("user-2") is None

    await bot.chat.handle(message({**raw, "id": "message-3", "text": "你好"}))

    assert [m["content"] for m in openai_server.calls[0]["messages"]] == [
        "你是测试机器人",
        "你好",
    ]
