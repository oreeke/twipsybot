from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest
from apscheduler.schedulers.base import STATE_PAUSED, STATE_RUNNING, STATE_STOPPED
from support.events import message
from support.factories import set_plugin_config
from support.types import MakeBot, MakePluginDir, WriteConfig

from twipsybot.bot.admin import handlers
from twipsybot.bot.admin.service import _extract_slash_command, _is_slash_command
from twipsybot.bot.flows.post import AutoPostService
from twipsybot.shared.keys import ConfigKeys
from twipsybot.shared.settings import read_settings


async def test_admin_check_matches_ids_and_canonical_handles(
    make_bot: MakeBot, write_config: WriteConfig
) -> None:
    bot = await make_bot(write_config())
    local = bot.limits.canonical_handle("alice")
    bot.config.data["bot"]["admins"] = ["user-1", local, "carol@remote.example"]

    admin = bot.admin
    assert admin.is_admin("user-1", None)
    assert admin.is_admin("other", "alice")
    assert admin.is_admin("other", "carol@remote.example")
    assert not admin.is_admin("other", "carol")
    assert not admin.is_admin(None, "alice")
    assert not admin.is_admin("other", None)


@pytest.mark.parametrize("plugin_count", (0, 5))
async def test_admin_status_preserves_code_block_layout(
    make_bot: MakeBot,
    write_config: WriteConfig,
    monkeypatch: pytest.MonkeyPatch,
    plugin_count: int,
) -> None:
    bot = await make_bot(write_config(bot={"admins": ["user-2"]}))
    bot.runtime.startup_time = datetime.now(UTC)
    bot.bot_username = "testbot"
    bot.bot_user_id = "bot-id"
    bot.streaming.state = "connected"
    bot.streaming.channels = {
        "main": {"name": "main"},
        "home": {"name": "homeTimeline"},
        "antenna-1": {"name": "antenna"},
        "antenna-2": {"name": "antenna"},
        "chat": {"name": "chatUser"},
    }
    monkeypatch.setattr(
        bot.misskey, "instance_url", "https://user:secret@example.com/path?token=secret"
    )
    monkeypatch.setattr(
        bot.plugin_manager,
        "get_plugin_states",
        Mock(return_value={f"p{i}": True for i in range(plugin_count)}),
    )
    response = await bot.admin.on_message(
        message(
            {
                "id": "message-1",
                "text": "^status",
                "user": {"id": "user-2", "username": "bob"},
            }
        )
    )
    assert response is not None
    assert response.count("```") == 2
    assert "状态  🟨 未运行 · " in response
    assert "任务  🟨 stream · 🟨 scheduler" in response.splitlines()
    assert " · busy 0 · queue 0/1000" in response
    assert "账号  @testbot (bot-id)" in response
    assert "实例  example.com" in response
    assert "secret" not in response
    assert "频道  main · home" in response
    assert "antenna x2 · chat x1" in response
    if plugin_count:
        assert "插件  5/5 已启用" in response
    else:
        assert "插件" not in response
    assert "授权  1" in response.splitlines()


@pytest.mark.parametrize(
    ("state", "running", "expected"),
    (
        (STATE_RUNNING, True, "🟩"),
        (STATE_PAUSED, True, "🟨"),
        (STATE_STOPPED, True, "🟥"),
        (STATE_STOPPED, False, "🟨"),
    ),
)
async def test_admin_status_scheduler_markers(
    make_bot: MakeBot,
    write_config: WriteConfig,
    monkeypatch: pytest.MonkeyPatch,
    state: int,
    running: bool,
    expected: str,
) -> None:
    bot = await make_bot(write_config())
    monkeypatch.setattr(bot.runtime, "running", running)
    monkeypatch.setattr(bot.scheduler, "state", state)
    assert handlers.task_status_text(bot) == f"任务  🟨 stream · {expected} scheduler"


@pytest.mark.parametrize(
    ("outcome", "running", "expected"),
    (
        ("running", True, "🟩"),
        ("success", True, "🟥"),
        ("error", True, "🟥"),
        ("cancel", True, "🟥"),
        ("success", False, "🟨"),
    ),
)
async def test_admin_status_reports_stream_task_state(
    make_bot: MakeBot,
    write_config: WriteConfig,
    outcome: str,
    running: bool,
    expected: str,
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
    (
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
    ),
)
async def test_admin_status_reports_auto_post_schedule(
    make_bot: MakeBot,
    write_config: WriteConfig,
    monkeypatch: pytest.MonkeyPatch,
    autopost: dict[str, Any],
    count: int,
    scheduler_state: int,
    has_job: bool,
    expected: str,
) -> None:
    bot = await make_bot(write_config(autopost={"daily_max": 5, **autopost}))
    bot.auto_post.posts_today = count
    next_run = datetime(2026, 9, 9, 14, 30, tzinfo=UTC)
    scheduler = SimpleNamespace(
        state=scheduler_state,
        get_job=Mock(
            return_value=SimpleNamespace(next_run_time=next_run) if has_job else None
        ),
    )
    monkeypatch.setattr(bot, "scheduler", scheduler)
    text = handlers.auto_post_status_text(bot)
    assert text.startswith(expected)
    if expected.endswith("下次"):
        assert next_run.astimezone().strftime("%m-%d %H:%M %z") in text
    else:
        assert "下次" not in text


def test_auto_post_status_does_not_show_previous_day_count() -> None:
    service = AutoPostService(cast(Any, None))
    service.posts_today = 5
    assert service.daily_post_count == 5
    service._post_date = "2000-01-01"
    assert service.daily_post_count == 0
    assert service.posts_today == 5


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/img 一只戴眼镜的猫", ("img", "一只戴眼镜的猫")),
        ("@testbot /post 星空下的城市", ("post", "星空下的城市")),
        ("@testbot\t/IMG\n星空下的城市", ("img", "星空下的城市")),
        ("/img", None),
        ("画图 一只戴眼镜的猫", None),
        ("/image watercolor landscape", ("image", "watercolor landscape")),
        ("介绍一下绘画", None),
        ("", None),
        ("   ", None),
    ],
)
def test_slash_command_detection(text: str, expected: tuple[str, str] | None) -> None:
    assert _extract_slash_command(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/bond", True),
        ("@testbot @alice /web 新闻", True),
        ("@testbot", False),
        ("/", False),
        ("你好 /web", False),
        ("https://example.com/web", False),
    ],
)
def test_slash_command_prefix_detection(text: str, expected: bool) -> None:
    assert _is_slash_command(text) is expected


async def test_admin_commands_persist_to_settings(
    make_bot: MakeBot, write_config: WriteConfig
) -> None:
    bot = await make_bot(write_config())
    await handlers.set_bool(bot, ConfigKeys.REPLY_CHAT, "chat", "off")
    await handlers.set_model(bot, "temporary-model")

    assert bot.openai.model == "temporary-model"
    assert read_settings(bot.config.settings_path)["bot"]["model"] == "temporary-model"

    response = await handlers.set_model(bot, "reset")

    assert response == "已恢复默认模型: gpt-6-luna"
    assert bot.openai.model == "gpt-6-luna"
    assert "model" not in read_settings(bot.config.settings_path)["bot"]
    assert bot.config.get(ConfigKeys.REPLY_CHAT) is False


async def test_admin_resets_auto_post_counter(
    make_bot: MakeBot, write_config: WriteConfig
) -> None:
    bot = await make_bot(write_config(bot={"admins": ["user-2"]}))
    await bot.auto_post.post_count()

    response = await bot.admin.on_message(
        message(
            {
                "id": "message-1",
                "text": "^autopost reset",
                "user": {"id": "user-2", "username": "bob"},
            }
        )
    )

    assert response
    assert "自动发帖计数器已重置" in response
    assert bot.auto_post.posts_today == 0
    assert await bot.db.get_auto_post_state() == (bot.auto_post._today(), 0)


async def test_admin_lists_and_toggles_plugins(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir("demo")
    bot = await make_bot(
        write_config(bot={"admins": ["user-2"]}),
        plugins_dir=plugins_dir,
    )
    user = {"id": "user-2", "username": "bob"}

    async def run(text: str) -> str:
        response = await bot.admin.on_message(
            message({"id": "message-1", "text": text, "user": user})
        )
        assert response is not None
        return response

    assert "1/1 已启用\n● demo" in await run("^plugin")
    assert "demo 已启用" in await run("^plugin on demo")
    assert "demo 已停用" in await run("^plugin off demo")
    assert "0/1 已启用\n○ demo" in await run("^plugin list")
    assert (
        read_settings(bot.config.settings_path)["plugins"]["demo"]["enabled"] is False
    )
    assert "demo 已启用" in await run("^plugin on demo")
    assert bot.plugin_manager.get_plugin("demo")._enabled  # type: ignore[union-attr]
    assert "未知插件: ghost" in await run("^plugin on ghost")
    assert "用法: ^plugin on|off <插件名>" in await run("^plugin on")
    assert "未知命令: reload" in await run("^reload demo")


async def test_admin_plugin_on_rolls_back_when_start_fails(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir("demo")
    config = write_config(bot={"admins": ["user-2"]})
    set_plugin_config(config.root, "demo", {"enabled": False, "priority": "x"})
    bot = await make_bot(config, plugins_dir=plugins_dir)

    response = await bot.admin.on_message(
        message(
            {
                "id": "message-1",
                "text": "^plugin on demo",
                "user": {"id": "user-2", "username": "bob"},
            }
        )
    )

    assert response
    assert "demo 启用失败，详见日志" in response
    assert (
        read_settings(bot.config.settings_path)["plugins"]["demo"]["enabled"] is False
    )


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


async def test_admin_can_reenable_chat(
    make_bot: MakeBot,
    write_config: WriteConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    debug_log = Mock()
    info_log = Mock()
    monkeypatch.setattr("twipsybot.bot.flows.chat.logger.debug", debug_log)
    monkeypatch.setattr("twipsybot.bot.flows.chat.logger.info", info_log)
    bot = await make_bot(
        write_config(
            bot={"admins": ["user-2"]},
            reply={"blacklist": ["user-2"], "rate_limit": "1h"},
        )
    )
    plugin_hook = AsyncMock(wraps=bot.plugin_manager.call_plugin_hook)
    monkeypatch.setattr(bot.plugin_manager, "call_plugin_hook", plugin_hook)
    raw = {
        "id": "message-1",
        "text": "^chat off",
        "user": {"id": "user-2", "username": "bob"},
    }
    await bot.chat.handle(message(raw))
    assert bot.config.get("reply.chat") is False

    await bot.chat.handle(message({**raw, "id": "message-2", "text": "^chat on"}))

    assert bot.config.get("reply.chat") is True
    assert any(
        call.args == ("Chat handled by Admin",) for call in debug_log.call_args_list
    )
    assert not any(
        call.args == ("Chat handled by plugin: Admin",)
        for call in debug_log.call_args_list
    )
    assert any(
        call.args and call.args[0].startswith("Admin replied to @bob:")
        for call in info_log.call_args_list
    )
    plugin_hook.assert_not_awaited()
    assert await bot.db.get_response_limit_state("user-2") is None
    assert "user-2" not in bot.chat._histories


async def test_admin_autopost_switches_mode(
    make_bot: MakeBot, write_config: WriteConfig
) -> None:
    bot = await make_bot(write_config())

    assert "需要先" in str(await handlers.set_autopost(bot, "schedule"))
    assert bot.auto_post.mode == "rotation"
    assert await handlers.set_autopost(bot, "on") is None

    await bot.settings.update({ConfigKeys.POST_TIMES: ["09:00"]})
    assert await handlers.set_autopost(bot, "schedule") == "autopost: schedule"
    post = read_settings(bot.config.settings_path)["autopost"]
    assert post["mode"] == "schedule"
    assert await handlers.set_autopost(bot, "off") == "autopost: off"
    assert bot.auto_post.mode == "off"
