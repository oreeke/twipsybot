from __future__ import annotations

import base64
import signal
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
import yaml
from conftest import MakeBot, WriteConfig
from httpx2 import Request, Response
from openai import APIStatusError

from twipsybot import Config, MisskeyBot
from twipsybot.admin.service import AdminCommandService
from twipsybot.app import cli as app_cli
from twipsybot.app import main as app_main
from twipsybot.bot.engine.pipeline import AIResponse
from twipsybot.bot.flows.image import ImageGenerationService
from twipsybot.bot.flows.post import AutoPostService
from twipsybot.clients.misskey.api import MisskeyAPI
from twipsybot.clients.misskey.socket import _redact_access_token
from twipsybot.clients.openai.api import OpenAIAPI
from twipsybot.shared.config_keys import ConfigKeys
from twipsybot.shared.constants import API_MAX_RETRIES
from twipsybot.shared.exceptions import APIConnectionError, ConfigurationError


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


def test_misskey_access_token_is_redacted() -> None:
    text = 'https://example.com/streaming?i=secret&x=1 {"i":"token"}'

    assert _redact_access_token(text) == (
        'https://example.com/streaming?i=***&x=1 {"i":"***"}'
    )


async def test_misskey_api_retries_connection_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = AsyncMock(side_effect=[APIConnectionError(), {"ok": True}])
    sleep = AsyncMock()
    api = object.__new__(MisskeyAPI)
    api._make_request_once = request
    monkeypatch.setattr("twipsybot.clients.misskey.api.asyncio.sleep", sleep)

    assert await api.make_read_request("notes/show") == {"ok": True}
    assert request.await_count == 2
    sleep.assert_awaited_once()


async def test_misskey_api_stops_after_max_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = AsyncMock(side_effect=APIConnectionError())
    sleep = AsyncMock()
    api = object.__new__(MisskeyAPI)
    api._make_request_once = request
    monkeypatch.setattr("twipsybot.clients.misskey.api.asyncio.sleep", sleep)

    with pytest.raises(APIConnectionError):
        await api.make_read_request("notes/show")

    assert request.await_count == API_MAX_RETRIES + 1
    assert sleep.await_count == API_MAX_RETRIES


async def test_misskey_api_does_not_retry_writes() -> None:
    request = AsyncMock(side_effect=APIConnectionError())
    api = object.__new__(MisskeyAPI)
    api._make_request_once = request

    with pytest.raises(APIConnectionError):
        await api.make_request("notes/create")

    request.assert_awaited_once_with("notes/create", None)


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
    ],
)
def test_slash_command_detection(text: str, expected: tuple[str, str] | None) -> None:
    assert AdminCommandService._extract_slash_command(text) == expected


async def test_openai_generates_png_bytes() -> None:
    api = OpenAIAPI("test", image_model="gpt-image-1")
    generate = AsyncMock(
        return_value=SimpleNamespace(
            data=[SimpleNamespace(b64_json=base64.b64encode(b"png").decode())]
        )
    )
    api.client = cast(Any, SimpleNamespace(images=SimpleNamespace(generate=generate)))

    assert await api.generate_image("一只猫") == b"png"
    generate.assert_awaited_once_with(
        model="gpt-image-1",
        prompt="一只猫",
    )


async def test_openai_forwards_explicit_image_options() -> None:
    api = OpenAIAPI(
        "test",
        image_model="gpt-image-2",
        image_size="2048x2048",
        image_quality="medium",
    )
    generate = AsyncMock(
        return_value=SimpleNamespace(
            data=[SimpleNamespace(b64_json=None, url="https://example.com")]
        )
    )
    api.client = cast(Any, SimpleNamespace(images=SimpleNamespace(generate=generate)))

    assert await api.generate_image("一只猫") == "https://example.com"
    generate.assert_awaited_once_with(
        model="gpt-image-2",
        prompt="一只猫",
        size="2048x2048",
        quality="medium",
    )


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


@pytest.mark.parametrize(
    "unknown_config",
    [
        {"unexpected": True},
        {"bot": {"response": {"caht": False}}},
    ],
)
def test_unknown_config_fields_fail_fast(
    tmp_path: Path, unknown_config: dict[str, Any]
) -> None:
    data = {
        "misskey": {
            "instance_url": "http://example.invalid",
            "access_token": "token",
        },
        "openai": {"api_key": "key"},
        **unknown_config,
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    config = Config(config_path=str(config_path))

    with pytest.raises(ConfigurationError, match="Extra inputs are not permitted"):
        config.load()


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
    assert config.get(ConfigKeys.BOT_RESPONSE_RATE_LIMIT) == 3
    assert config.get(ConfigKeys.BOT_TIMELINE_GLOBAL) is True
    assert config.data["bot"]["timeline"]["global"] is True
    assert "global_" not in config.data["bot"]["timeline"]
    assert config.get(ConfigKeys.DB_CLEAR) == 30


def test_timeline_channels_are_independently_enabled(write_config: WriteConfig) -> None:
    config = write_config(bot={"timeline": {"home": True, "local": False}})
    bot = MisskeyBot(config)

    assert bot.connect._timeline_channels == {"homeTimeline"}


def test_legacy_auto_post_interval_field_is_rejected(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "misskey": {
                    "instance_url": "http://example.invalid",
                    "access_token": "token",
                },
                "openai": {"api_key": "key"},
                "bot": {"auto_post": {"interval_minutes": 180}},
            }
        ),
        encoding="utf-8",
    )
    config = Config(config_path=str(config_path))

    with pytest.raises(ConfigurationError, match="interval_minutes"):
        config.load()


@pytest.mark.parametrize("interval", (180, "180", "3h"))
def test_auto_post_interval_accepts_minutes_and_units(
    write_config: WriteConfig, interval: int | str
) -> None:
    config = write_config(bot={"auto_post": {"interval": interval}})

    assert config.get(ConfigKeys.BOT_AUTO_POST_INTERVAL).total_seconds() == 10800


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
    ],
)
def test_response_limit_duration_normalization(
    write_config: WriteConfig, value: Any, expected: int
) -> None:
    config = write_config(bot={"response": {"rate_limit": value}})

    assert config.get(ConfigKeys.BOT_RESPONSE_RATE_LIMIT) == expected


@pytest.mark.parametrize(
    "value",
    (
        "1w",
        "1mm",
        "1y",
        "500ms",
        "1us",
        "1ns",
        "0.5s",
        "1.5m",
        "1h0.5m",
        "1hXXX30m",
        "invalid",
        True,
    ),
)
def test_response_limit_rejects_invalid_duration(
    write_config: WriteConfig, value: Any
) -> None:
    with pytest.raises(ConfigurationError, match="limits must use"):
        write_config(bot={"response": {"rate_limit": value}})


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
    make_bot: MakeBot, write_config: WriteConfig
) -> None:
    bot = await make_bot(write_config(bot={"admin": {"allowed_users": ["user-2"]}}))
    message = {
        "id": "message-1",
        "text": "^chat off",
        "user": {"id": "user-2", "username": "bob"},
    }
    await bot.chat.handle(message)
    assert bot.config.get("bot.response.chat") is False

    await bot.chat.handle({**message, "id": "message-2", "text": "^chat on"})

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
    import twipsybot.clients.openai.api as module

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
