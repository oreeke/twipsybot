from __future__ import annotations

import asyncio
import signal
import sys
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest
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
    ConfigurationError,
)


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

    assert blocked is None
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
