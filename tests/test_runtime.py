from __future__ import annotations

import asyncio
import signal
import sys
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, call

import pytest
from apscheduler.schedulers.base import STATE_PAUSED, STATE_RUNNING, STATE_STOPPED
from conftest import MakeBot, WriteConfig

from twipsybot import MisskeyBot
from twipsybot.admin.service import AdminCommandService
from twipsybot.app import cli as app_cli
from twipsybot.app import main as app_main
from twipsybot.bot.engine.pipeline import AIResponse
from twipsybot.bot.flows.image import ImageGenerationService
from twipsybot.bot.flows.post import AutoPostService
from twipsybot.shared.config_keys import ConfigKeys
from twipsybot.shared.exceptions import (
    APIBadRequestError,
    APIConnectionError,
    APIRateLimitError,
    ConfigurationError,
)


@pytest.mark.parametrize("plugin_count", (0, 5))
async def test_admin_status_preserves_code_block_layout(
    make_bot: MakeBot,
    write_config: WriteConfig,
    monkeypatch: pytest.MonkeyPatch,
    plugin_count: int,
) -> None:
    bot = await make_bot(write_config(bot={"admin": {"allowed_users": ["user-2"]}}))
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
        "get_plugin_info",
        Mock(return_value=[{"enabled": True} for _ in range(plugin_count)]),
    )
    response = await bot.admin.on_message(
        {"text": "^status", "user": {"id": "user-2", "username": "bob"}}
    )
    assert response is not None
    assert response.count("```") == 2
    assert "状态  🟨 未运行 · " in response
    assert "连接  🟩 connected" in response.splitlines()
    assert "任务  stream 🟨 · scheduler 🟨" in response.splitlines()
    assert " · busy 0 · queue 0/1000" in response
    assert "存活" not in response
    assert "忙" not in response
    assert "queue 0/1000\n\n账号  @testbot (bot-id)" in response
    assert "实例  example.com" in response
    assert "secret" not in response
    assert "频道  main · home\n\u3000\u3000  antenna x2 · chat x1" in response
    if plugin_count:
        assert "chat x1\n\n插件  5/5 已启用\n授权  1" in response
    else:
        assert "chat x1\n授权  1" in response
    assert "授权  1" in response.splitlines()


@pytest.mark.parametrize(
    ("status", "marker"),
    (
        ("connected", "🟩"),
        ("initializing", "🟨"),
        ("reconnecting", "🟨"),
        ("disconnected", "🟥"),
    ),
)
@pytest.mark.parametrize("running", (True, False))
async def test_admin_status_connection_markers(
    make_bot: MakeBot,
    write_config: WriteConfig,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    marker: str,
    running: bool,
) -> None:
    bot = await make_bot(write_config())
    monkeypatch.setattr(bot.runtime, "running", running)
    bot.streaming.state = status
    expected = "🟨" if marker == "🟥" and not running else marker
    assert f"连接  {expected} {status}" in bot.admin._get_status_text().splitlines()


@pytest.mark.parametrize(
    ("state", "marker"),
    ((STATE_RUNNING, "🟩"), (STATE_PAUSED, "🟨"), (STATE_STOPPED, "🟥")),
)
@pytest.mark.parametrize("running", (True, False))
async def test_admin_status_scheduler_markers(
    make_bot: MakeBot,
    write_config: WriteConfig,
    monkeypatch: pytest.MonkeyPatch,
    state: int,
    marker: str,
    running: bool,
) -> None:
    bot = await make_bot(write_config())
    monkeypatch.setattr(bot.runtime, "running", running)
    monkeypatch.setattr(bot.scheduler, "state", state)
    expected = "🟨" if marker == "🟥" and not running else marker
    assert (
        bot.admin._get_task_status_text() == f"任务  stream 🟨 · scheduler {expected}"
    )


@pytest.mark.parametrize("running", (True, False))
@pytest.mark.parametrize("outcome", ("running", "success", "error", "cancel"))
async def test_admin_status_reports_stream_task_state(
    make_bot: MakeBot, write_config: WriteConfig, outcome: str, running: bool
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
        expected = "🟩" if outcome == "running" else "🟥" if running else "🟨"
        text = bot.admin._get_status_text()
        scheduler = "🟥" if running else "🟨"
        assert f"任务  stream {expected} · scheduler {scheduler}" in text.splitlines()
        status = "🟩 运行中" if running else "🟨 未运行"
        assert f"状态  {status} · " in text
        assert "private details" not in text
    finally:
        bot.runtime.running = False
        await bot.runtime.cleanup_tasks()


@pytest.mark.parametrize(
    ("enabled", "count", "scheduler_state", "has_job", "expected"),
    (
        (False, 2, 1, True, "已关闭"),
        (True, 5, 1, True, "今日已达上限"),
        (True, 2, 0, True, "未调度"),
        (True, 2, 2, True, "未调度"),
        (True, 2, 1, False, "未调度"),
        (True, 2, 1, True, "下次"),
    ),
)
async def test_admin_status_reports_auto_post_schedule(
    make_bot: MakeBot,
    write_config: WriteConfig,
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
    count: int,
    scheduler_state: int,
    has_job: bool,
    expected: str,
) -> None:
    bot = await make_bot(
        write_config(bot={"auto_post": {"enabled": enabled, "max_posts_per_day": 5}})
    )
    bot.auto_post.posts_today = count
    next_run = datetime(2026, 9, 9, 14, 30, tzinfo=UTC)
    scheduler = SimpleNamespace(
        state=scheduler_state,
        get_job=Mock(
            return_value=SimpleNamespace(next_run_time=next_run) if has_job else None
        ),
    )
    monkeypatch.setattr(bot, "scheduler", scheduler)
    text = bot.admin._get_auto_post_status_text()
    assert text.startswith(f"发帖  {count}/5 · {expected}")
    if expected == "下次":
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
        ("@testbot 你好", True),
        ("你好 @TESTBOT。", True),
        ("@testbot@example.com 你好", True),
        ("@testbot@example.com. 你好", True),
        ("@testbot2 你好", False),
        ("@testbot_other 你好", False),
        ("@testbot@other.example 你好", False),
        ("@@testbot 你好", False),
        ("user@testbot.example", False),
    ],
)
def test_bot_mention_matches_complete_local_account(text: str, expected: bool) -> None:
    bot = cast(Any, object.__new__(MisskeyBot))
    bot.bot_username = "testbot"
    bot.misskey = SimpleNamespace(instance_url="https://example.com")

    assert bot.is_bot_mentioned(text) is expected


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
    assert AdminCommandService._extract_slash_command(text) == expected


async def test_image_service_downloads_url_before_upload() -> None:
    openai = SimpleNamespace(
        generate_image=AsyncMock(return_value="https://example.com/image.jpg")
    )
    drive = SimpleNamespace(
        fetch_bytes=AsyncMock(return_value=b"\xff\xd8\xffimage"),
        upload_bytes=AsyncMock(return_value={"id": "file-1"}),
    )
    service = ImageGenerationService(
        cast(Any, SimpleNamespace(openai=openai, misskey=SimpleNamespace(drive=drive)))
    )

    assert await service.generate_and_upload("一只猫") == "file-1"
    drive.fetch_bytes.assert_awaited_once_with(
        "https://example.com/image.jpg", max_bytes=32 * 1024 * 1024
    )
    drive.upload_bytes.assert_awaited_once_with(
        b"\xff\xd8\xffimage", name="generated.jpg", content_type="image/jpeg"
    )


@pytest.mark.parametrize("failure", ["generate", "upload"])
async def test_image_response_handles_provider_failures(failure: str) -> None:
    generate_image = AsyncMock(return_value=b"\x89PNG\r\n\x1a\nimage")
    upload_bytes = AsyncMock(return_value={"id": "file-1"})
    if failure == "generate":
        generate_image.side_effect = RuntimeError("generation failed")
    else:
        upload_bytes.side_effect = RuntimeError("upload failed")
    service = ImageGenerationService(
        cast(
            Any,
            SimpleNamespace(
                openai=SimpleNamespace(generate_image=generate_image),
                misskey=SimpleNamespace(
                    drive=SimpleNamespace(upload_bytes=upload_bytes)
                ),
            ),
        )
    )

    assert await service.generate_response("一只猫") == AIResponse(
        "图片生成失败，请稍后再试。"
    )


@pytest.mark.parametrize(
    "data",
    (b"not-an-image", b"\x89PNG\r\n\x1a\n" + b"x" * (32 * 1024 * 1024)),
    ids=("invalid", "oversized"),
)
async def test_image_service_rejects_invalid_or_oversized_data(data: bytes) -> None:
    upload_bytes = AsyncMock()
    service = ImageGenerationService(
        cast(
            Any,
            SimpleNamespace(
                openai=SimpleNamespace(generate_image=AsyncMock(return_value=data)),
                misskey=SimpleNamespace(
                    drive=SimpleNamespace(upload_bytes=upload_bytes)
                ),
            ),
        )
    )

    assert await service.generate_response("一只猫") == AIResponse(
        "图片生成失败，请稍后再试。"
    )
    upload_bytes.assert_not_awaited()


def test_cli_propagates_run_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["twipsybot", "run"])
    monkeypatch.setattr(app_cli.app_main, "main", lambda: 4)

    assert app_cli.main() == 4


@pytest.mark.parametrize(("hold", "expected"), ((False, 2), (True, 0)))
def test_startup_error_hold_is_container_only(
    monkeypatch: pytest.MonkeyPatch, hold: bool, expected: int
) -> None:
    async def fail_start(_: app_main.BotRunner) -> None:
        raise ConfigurationError("invalid")

    wait = AsyncMock()
    monkeypatch.setattr(app_main.BotRunner, "run", fail_start)
    monkeypatch.setattr(app_main, "_hold_until_terminated", wait)
    if hold:
        monkeypatch.setenv("TWIPSYBOT_HOLD_ON_STARTUP_ERROR", "1")
    else:
        monkeypatch.delenv("TWIPSYBOT_HOLD_ON_STARTUP_ERROR", raising=False)

    assert app_main.main() == expected
    assert wait.await_count == int(hold)


async def test_startup_error_hold_stops_on_termination_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def terminate(handler: Any) -> None:
        handler(signal.SIGTERM)

    monkeypatch.setattr(app_main, "_set_termination_handlers", terminate)

    await app_main._hold_until_terminated()


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


async def test_database_manager_can_reinitialize_after_close(
    make_bot: MakeBot, write_config: WriteConfig
) -> None:
    bot = await make_bot(write_config())
    await bot.db.close()

    await bot.db.initialize()
    await bot.db.set_plugin_data("test", "key", "value")

    assert await bot.db.get_plugin_data("test", "key") == "value"


async def test_legacy_permanent_turn_block_migrates_to_blacklist(
    make_bot: MakeBot, write_config: WriteConfig
) -> None:
    bot = await make_bot(write_config())
    await bot.db.set_response_limit_state(
        user_id="legacy-user",
        last_reply_ts=1.0,
        turns=3,
        blocked_until_ts=-1,
    )

    blocked = await bot.limits.get_response_block_reply(
        user_id="legacy-user", handle=None
    )

    assert blocked == (False, "")
    assert bot.config.get(ConfigKeys.BOT_RESPONSE_BLACKLIST) == ["legacy-user"]
    assert (
        await bot.db.get_plugin_data("Admin", ConfigKeys.BOT_RESPONSE_BLACKLIST)
        == '["legacy-user"]'
    )
    assert await bot.db.get_response_limit_state("legacy-user") == (1.0, 0, None)


async def test_model_reset_preserves_runtime_config(
    make_bot: MakeBot, write_config: WriteConfig
) -> None:
    bot = await make_bot(
        write_config(
            openai={"model": "default-model"},
        )
    )
    bot.admin._handle_set_bool("chat", ConfigKeys.BOT_RESPONSE_CHAT, "off")
    await bot.admin._handle_model("temporary-model")

    response = await bot.admin._handle_model("reset")

    assert response == "已恢复默认模型: default-model"
    assert bot.openai.model == "default-model"
    assert bot.config.get(ConfigKeys.BOT_RESPONSE_CHAT) is False


async def test_admin_resets_auto_post_counter(
    make_bot: MakeBot, write_config: WriteConfig
) -> None:
    bot = await make_bot(write_config(bot={"admin": {"allowed_users": ["user-2"]}}))
    await bot.auto_post.post_count()

    response = await bot.admin.on_message(
        {
            "text": "^autopost reset",
            "user": {"id": "user-2", "username": "bob"},
        }
    )

    assert response
    assert "自动发帖计数器已重置" in response
    assert bot.auto_post.posts_today == 0
    assert await bot.db.get_auto_post_state() == (bot.auto_post._today(), 0)


def _cleanable_note(note_id: str, created_at: str) -> dict[str, Any]:
    return {
        "id": note_id,
        "createdAt": created_at,
        "replyId": None,
        "renoteId": None,
        "channelId": None,
        "mentions": [],
        "repliesCount": 0,
        "renoteCount": 0,
        "reactionCount": 0,
        "reactions": {},
        "clippedCount": 0,
        "poll": None,
    }


async def test_admin_clean_posts_preview_uses_requested_format(
    make_bot: MakeBot, write_config: WriteConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = await make_bot(write_config(bot={"admin": {"allowed_users": ["user-2"]}}))
    notes = [
        _cleanable_note("new-note", "2026-01-02T00:00:00Z"),
        _cleanable_note("old-note", "2025-01-02T00:00:00Z"),
    ]
    monkeypatch.setattr(
        bot.misskey, "get_current_user", AsyncMock(return_value={"pinnedNotes": []})
    )
    monkeypatch.setattr(bot.misskey, "get_user_notes", AsyncMock(return_value=notes))

    response = await bot.admin.on_message(
        {"text": "^clean posts 30", "user": {"id": "user-2", "username": "bob"}}
    )

    assert response is not None
    assert "发现 2 条超过 30 天未被互动的帖子" in response
    assert "最旧：2025-01-02 · old-note" in response
    assert "最新：2026-01-02 · new-note" in response
    assert "使用 ^clean posts 30 -y 确认删除" in response
    assert "危险：删除后无法恢复" in response


async def test_admin_clean_posts_rechecks_and_deletes_oldest_first(
    make_bot: MakeBot, write_config: WriteConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = await make_bot(write_config(bot={"admin": {"allowed_users": ["user-2"]}}))
    new_note = _cleanable_note("new-note", "2026-01-02T00:00:00Z")
    old_note = _cleanable_note("old-note", "2025-01-02T00:00:00Z")
    delete_note = AsyncMock(return_value={})
    monkeypatch.setattr(
        bot.misskey, "get_current_user", AsyncMock(return_value={"pinnedNotes": []})
    )
    monkeypatch.setattr(
        bot.misskey, "get_user_notes", AsyncMock(return_value=[new_note, old_note])
    )
    monkeypatch.setattr(
        bot.misskey, "get_note", AsyncMock(side_effect=[old_note, new_note])
    )
    monkeypatch.setattr(bot.misskey, "delete_note", delete_note)
    monkeypatch.setattr("twipsybot.admin.handlers.asyncio.sleep", AsyncMock())

    response = await bot.admin.on_message(
        {
            "text": "^clean posts 30 -y",
            "user": {"id": "user-2", "username": "bob"},
        }
    )

    assert response is not None
    assert "已删除 2 条超过 30 天未被互动的帖子 · 跳过 0 条" in response
    assert delete_note.await_args_list == [call("old-note"), call("new-note")]


async def test_admin_clean_posts_skips_note_interacted_with_before_delete(
    make_bot: MakeBot, write_config: WriteConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = await make_bot(write_config(bot={"admin": {"allowed_users": ["user-2"]}}))
    note = _cleanable_note("note-1", "2025-01-02T00:00:00Z")
    interacted = {**note, "reactionCount": 1, "reactions": {"👍": 1}}
    delete_note = AsyncMock(return_value={})
    monkeypatch.setattr(
        bot.misskey, "get_current_user", AsyncMock(return_value={"pinnedNotes": []})
    )
    monkeypatch.setattr(bot.misskey, "get_user_notes", AsyncMock(return_value=[note]))
    monkeypatch.setattr(bot.misskey, "get_note", AsyncMock(return_value=interacted))
    monkeypatch.setattr(bot.misskey, "delete_note", delete_note)

    response = await bot.admin.on_message(
        {
            "text": "^clean posts 30 -y",
            "user": {"id": "user-2", "username": "bob"},
        }
    )

    assert response is not None
    assert "已删除 0 条超过 30 天未被互动的帖子 · 跳过 1 条" in response
    delete_note.assert_not_awaited()


async def test_admin_clean_posts_limits_each_batch_to_300(
    make_bot: MakeBot, write_config: WriteConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = await make_bot(write_config(bot={"admin": {"allowed_users": ["user-2"]}}))
    notes = [
        _cleanable_note(f"note-{index}", "2025-01-02T00:00:00Z") for index in range(301)
    ]
    monkeypatch.setattr(
        bot.misskey, "get_current_user", AsyncMock(return_value={"pinnedNotes": []})
    )
    monkeypatch.setattr(bot.misskey, "get_user_notes", AsyncMock(return_value=notes))
    monkeypatch.setattr(bot.misskey, "get_note", AsyncMock(side_effect=notes))
    delete_note = AsyncMock(return_value={})
    monkeypatch.setattr(bot.misskey, "delete_note", delete_note)
    monkeypatch.setattr("twipsybot.admin.handlers.asyncio.sleep", AsyncMock())

    response = await bot.admin.on_message(
        {
            "text": "^clean posts 30 -y",
            "user": {"id": "user-2", "username": "bob"},
        }
    )

    assert response is not None
    assert "已删除 300 条" in response
    assert "未处理 1 条" in response
    assert delete_note.await_count == 300


async def test_admin_clean_posts_stops_and_reports_rate_limit(
    make_bot: MakeBot, write_config: WriteConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = await make_bot(write_config(bot={"admin": {"allowed_users": ["user-2"]}}))
    notes = [
        _cleanable_note(f"note-{index}", "2025-01-02T00:00:00Z") for index in range(3)
    ]
    monkeypatch.setattr(
        bot.misskey, "get_current_user", AsyncMock(return_value={"pinnedNotes": []})
    )
    monkeypatch.setattr(bot.misskey, "get_user_notes", AsyncMock(return_value=notes))
    monkeypatch.setattr(bot.misskey, "get_note", AsyncMock(side_effect=notes))
    monkeypatch.setattr(
        bot.misskey,
        "delete_note",
        AsyncMock(side_effect=[{}, APIRateLimitError("rate limited")]),
    )
    monkeypatch.setattr("twipsybot.admin.handlers.asyncio.sleep", AsyncMock())

    response = await bot.admin.on_message(
        {
            "text": "^clean posts 30 -y",
            "user": {"id": "user-2", "username": "bob"},
        }
    )

    assert response is not None
    assert "已删除 1 条" in response
    assert "未处理 2 条" in response


async def test_admin_clean_posts_skips_note_deleted_during_recheck(
    make_bot: MakeBot, write_config: WriteConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = await make_bot(write_config(bot={"admin": {"allowed_users": ["user-2"]}}))
    notes = [
        _cleanable_note(f"note-{index}", "2025-01-02T00:00:00Z") for index in range(2)
    ]
    monkeypatch.setattr(
        bot.misskey, "get_current_user", AsyncMock(return_value={"pinnedNotes": []})
    )
    monkeypatch.setattr(bot.misskey, "get_user_notes", AsyncMock(return_value=notes))
    monkeypatch.setattr(bot.misskey, "get_note", AsyncMock(side_effect=notes))
    monkeypatch.setattr(
        bot.misskey,
        "delete_note",
        AsyncMock(side_effect=[APIBadRequestError("missing"), {}]),
    )
    monkeypatch.setattr("twipsybot.admin.handlers.asyncio.sleep", AsyncMock())

    response = await bot.admin.on_message(
        {
            "text": "^clean posts 30 -y",
            "user": {"id": "user-2", "username": "bob"},
        }
    )

    assert response is not None
    assert "已删除 1 条" in response
    assert "跳过 1 条" in response
    assert "未处理 0 条" in response


async def test_admin_clean_posts_stops_and_reports_connection_error(
    make_bot: MakeBot, write_config: WriteConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = await make_bot(write_config(bot={"admin": {"allowed_users": ["user-2"]}}))
    notes = [
        _cleanable_note(f"note-{index}", "2025-01-02T00:00:00Z") for index in range(3)
    ]
    monkeypatch.setattr(
        bot.misskey, "get_current_user", AsyncMock(return_value={"pinnedNotes": []})
    )
    monkeypatch.setattr(bot.misskey, "get_user_notes", AsyncMock(return_value=notes))
    monkeypatch.setattr(bot.misskey, "get_note", AsyncMock(side_effect=notes))
    monkeypatch.setattr(
        bot.misskey,
        "delete_note",
        AsyncMock(side_effect=[{}, APIConnectionError("disconnected")]),
    )
    monkeypatch.setattr("twipsybot.admin.handlers.asyncio.sleep", AsyncMock())

    response = await bot.admin.on_message(
        {
            "text": "^clean posts 30 -y",
            "user": {"id": "user-2", "username": "bob"},
        }
    )

    assert response is not None
    assert "已删除 1 条" in response
    assert "跳过 0 条" in response
    assert "未处理 2 条" in response


@pytest.mark.parametrize("confirmation", ("yes", "-Y", "--yes"))
async def test_admin_clean_posts_only_accepts_exact_y_confirmation(
    make_bot: MakeBot,
    write_config: WriteConfig,
    monkeypatch: pytest.MonkeyPatch,
    confirmation: str,
) -> None:
    bot = await make_bot(write_config(bot={"admin": {"allowed_users": ["user-2"]}}))
    get_user_notes = AsyncMock()
    monkeypatch.setattr(bot.misskey, "get_user_notes", get_user_notes)

    response = await bot.admin.on_message(
        {
            "text": f"^clean posts 30 {confirmation}",
            "user": {"id": "user-2", "username": "bob"},
        }
    )

    assert response is not None
    assert "用法: ^clean posts <天数> [-y]" in response
    get_user_notes.assert_not_awaited()


async def test_admin_string_allowlist_uses_exact_match(
    make_bot: MakeBot, write_config: WriteConfig
) -> None:
    bot = await make_bot(
        write_config(bot={"admin": {"allowed_users": "admin@example.com"}})
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
    make_bot: MakeBot,
    write_config: WriteConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    debug_log = Mock()
    info_log = Mock()
    monkeypatch.setattr("twipsybot.bot.engine.pipeline.logger.debug", debug_log)
    monkeypatch.setattr("twipsybot.bot.flows.chat.logger.info", info_log)
    bot = await make_bot(
        write_config(
            bot={
                "admin": {"allowed_users": ["user-2"]},
                "response": {"blacklist": ["user-2"], "rate_limit": "1h"},
            }
        )
    )
    plugin_hook = AsyncMock(wraps=bot.plugin_manager.call_plugin_hook)
    monkeypatch.setattr(bot.plugin_manager, "call_plugin_hook", plugin_hook)
    message = {
        "id": "message-1",
        "text": "^chat off",
        "user": {"id": "user-2", "username": "bob"},
    }
    await bot.chat.handle(message)
    assert bot.config.get("bot.response.chat") is False

    await bot.chat.handle({**message, "id": "message-2", "text": "^chat on"})

    assert bot.config.get("bot.response.chat") is True
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
    assert "user-2" not in bot._chat_histories


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

    bot = object.__new__(MisskeyBot)
    bot.runtime = SimpleNamespace(running=True, cleanup_tasks=AsyncMock())
    bot.plugin_manager = SimpleNamespace(
        shutdown_plugins=AsyncMock(), cleanup_plugins=AsyncMock()
    )
    bot.scheduler = SimpleNamespace(running=False)
    bot.streaming = SimpleNamespace(close=AsyncMock())
    bot.misskey = SimpleNamespace(close=AsyncMock())
    bot.openai = SimpleNamespace(close=AsyncMock())
    bot.db = SimpleNamespace(close=blocking_close)

    await bot.stop()

    assert close_cancelled.is_set()


async def test_auto_post_confirms_only_successful_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_note = AsyncMock(side_effect=[{}, RuntimeError("failed")])
    confirm = AsyncMock()
    set_auto_post_state = AsyncMock()
    bot = SimpleNamespace(
        runtime=SimpleNamespace(running=True, startup_time=None),
        db=SimpleNamespace(set_auto_post_state=set_auto_post_state),
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
    set_auto_post_state.assert_awaited_once()
    assert service.posts_today == 1


async def test_auto_post_keeps_memory_count_when_persistence_fails() -> None:
    set_auto_post_state = AsyncMock(side_effect=RuntimeError("database failed"))
    service = AutoPostService(
        cast(
            Any,
            SimpleNamespace(
                db=SimpleNamespace(set_auto_post_state=set_auto_post_state)
            ),
        )
    )

    with pytest.raises(RuntimeError, match="database failed"):
        await service.post_count()

    assert service.posts_today == 1


async def test_auto_post_suppresses_cancellation_during_shutdown() -> None:
    bot = SimpleNamespace(
        config=SimpleNamespace(
            get=lambda key, default=None: {
                ConfigKeys.BOT_AUTO_POST_ENABLED: True,
                ConfigKeys.BOT_AUTO_POST_MAX_PER_DAY: 1,
                ConfigKeys.BOT_AUTO_POST_LOCAL_ONLY: False,
            }.get(key, default)
        ),
        runtime=SimpleNamespace(running=True),
    )

    async def cancel_during_shutdown(*args: Any) -> None:
        bot.runtime.running = False
        raise asyncio.CancelledError

    bot.plugin_manager = SimpleNamespace(call_plugin_hook=cancel_during_shutdown)

    await AutoPostService(cast(Any, bot)).run()


async def test_auto_post_propagates_cancellation_while_running() -> None:
    plugin_hook = AsyncMock(side_effect=asyncio.CancelledError)
    bot = SimpleNamespace(
        config=SimpleNamespace(
            get=lambda key, default=None: {
                ConfigKeys.BOT_AUTO_POST_ENABLED: True,
                ConfigKeys.BOT_AUTO_POST_MAX_PER_DAY: 1,
                ConfigKeys.BOT_AUTO_POST_LOCAL_ONLY: False,
            }.get(key, default)
        ),
        runtime=SimpleNamespace(running=True),
        plugin_manager=SimpleNamespace(call_plugin_hook=plugin_hook),
    )
    service = AutoPostService(cast(Any, bot))

    with pytest.raises(asyncio.CancelledError):
        await service.run()
