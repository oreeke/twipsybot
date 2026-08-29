from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import psutil
import pytest
import yaml
from conftest import (
    DEFAULT_AI_REPLY,
    FakeMisskeyServer,
    FakeOpenAIServer,
    MakeBot,
    MakePluginDir,
    WriteConfig,
)
from httpx import Request, Response
from openai import APIStatusError, APITimeoutError

from twipsybot import Config
from twipsybot.app.cli import _get_bot_process, _read_pid_record, _write_pid_file
from twipsybot.clients.openai.openai_api import OpenAIAPI
from twipsybot.shared.config_keys import ConfigKeys
from twipsybot.shared.exceptions import ConfigurationError

_MENTION_NOTE = {
    "id": "note-mention-1",
    "text": "@testbot 你好",
    "user": {"id": "user-1", "username": "alice"},
    "mentions": ["bot-id"],
}

_CHAT_MESSAGE = {
    "id": "msg-1",
    "text": "你好，机器人",
    "user": {"id": "user-2", "username": "bob"},
}


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


async def test_mention_triggers_ai_reply(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(write_config())

    await bot.handlers.mention.handle(dict(_MENTION_NOTE))

    assert len(openai_server.calls) == 1
    notes = misskey_server.calls["notes/create"]
    assert len(notes) == 1
    assert notes[0]["text"] == f"@alice\n{DEFAULT_AI_REPLY}"
    assert notes[0]["replyId"] == "note-mention-1"


async def test_chat_message_plugin_takeover(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
    echo_plugin_dir: Path,
) -> None:
    bot = await make_bot(write_config(), plugins_dir=echo_plugin_dir)

    await bot.handlers.chat.handle(dict(_CHAT_MESSAGE))

    assert openai_server.calls == []
    replies = misskey_server.calls["chat/messages/create-to-user"]
    assert len(replies) == 1
    assert replies[0]["toUserId"] == "user-2"
    assert replies[0]["text"] == "echo: plugin took over"


async def test_admin_command_takes_priority_over_plugins(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
    echo_plugin_dir: Path,
) -> None:
    bot = await make_bot(
        write_config(bot={"admin": {"enabled": True, "allowed_users": ["user-2"]}}),
        plugins_dir=echo_plugin_dir,
    )

    await bot.handlers.chat.handle({**_CHAT_MESSAGE, "text": "^help"})

    assert openai_server.calls == []
    reply = misskey_server.calls["chat/messages/create-to-user"][0]
    assert reply["text"].startswith("可用命令:\n```\n")
    assert "^status" in reply["text"]


async def test_auto_post_publishes_note(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    openai_server.set_reply("今天天气不错")
    bot = await make_bot(write_config())
    bot.runtime.running = True

    await bot.handlers.auto_post.run()

    notes = misskey_server.calls["notes/create"]
    assert len(notes) == 1
    assert notes[0]["text"] == "今天天气不错"
    assert notes[0]["visibility"] == "public"
    assert bot.handlers.auto_post.posts_today == 1


async def test_blacklisted_user_is_ignored(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(write_config(blacklist=["alice"]))

    await bot.handlers.mention.handle(dict(_MENTION_NOTE))

    assert openai_server.calls == []
    assert "notes/create" not in misskey_server.calls


async def test_chat_uses_history_and_replies_to_user(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    misskey_server.set_response(
        "chat/messages/user-timeline",
        lambda payload: [
            {
                "text": "上次回答",
                "fromUser": {"id": "bot-id", "username": "testbot"},
            },
            {
                "text": "上次问题",
                "fromUser": {"id": payload["userId"], "username": "bob"},
            },
        ],
    )
    bot = await make_bot(write_config())

    await bot.handlers.chat.handle(dict(_CHAT_MESSAGE))

    assert [message["content"] for message in openai_server.calls[0]["messages"]] == [
        "你是测试机器人",
        "上次问题",
        "上次回答",
        "你好，机器人",
    ]
    reply = misskey_server.calls["chat/messages/create-to-user"][0]
    assert reply == {"i": "test-token", "toUserId": "user-2", "text": DEFAULT_AI_REPLY}


async def test_room_chat_requires_mention_and_replies_to_room(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(write_config())
    room_message = {
        **_CHAT_MESSAGE,
        "toRoomId": "room-1",
        "toRoom": {"id": "room-1", "name": "测试房间"},
    }

    await bot.handlers.chat.handle(room_message)
    assert openai_server.calls == []

    await bot.handlers.chat.handle(
        {**room_message, "id": "msg-2", "text": "@testbot 你好"}
    )

    reply = misskey_server.calls["chat/messages/create-to-room"][0]
    assert reply["toRoomId"] == "room-1"
    assert reply["text"] == f"@bob\n{DEFAULT_AI_REPLY}"


async def test_reply_to_bot_triggers_ai_reply(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(write_config())
    event = {
        "type": "reply",
        "note": {
            "id": "reply-1",
            "text": "继续说说",
            "user": {"id": "user-1", "username": "alice"},
            "reply": {
                "text": "机器人原帖",
                "user": {"id": "bot-id", "username": "testbot"},
            },
        },
    }

    await bot.handlers.mention.handle(event)

    prompt = openai_server.calls[0]["messages"][-1]["content"]
    assert prompt == "机器人原帖\n\n继续说说"
    assert misskey_server.calls["notes/create"][0]["replyId"] == "reply-1"


async def test_rate_limit_returns_configured_reply_without_second_ai_call(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(
        write_config(
            bot={
                "response": {
                    "rate_limit": "1h",
                    "rate_limit_reply": "请稍后再试",
                }
            }
        )
    )

    await bot.handlers.chat.handle(dict(_CHAT_MESSAGE))
    await bot.handlers.chat.handle({**_CHAT_MESSAGE, "id": "msg-2"})

    assert len(openai_server.calls) == 1
    replies = misskey_server.calls["chat/messages/create-to-user"]
    assert [reply["text"] for reply in replies] == [DEFAULT_AI_REPLY, "请稍后再试"]


async def test_plugin_can_take_over_mention(
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    plugins_dir = make_plugin_dir(
        "echo",
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n\n"
        "class EchoPlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n\n"
        "    async def on_mention(self, note):\n"
        "        return self.handled('mention handled')\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    await bot.handlers.mention.handle(dict(_MENTION_NOTE))

    assert openai_server.calls == []
    assert misskey_server.calls["notes/create"][0]["text"] == "@alice\nmention handled"


async def test_auto_post_stops_at_daily_limit(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(write_config(bot={"auto_post": {"max_posts_per_day": 1}}))
    bot.runtime.running = True
    bot.handlers.auto_post.posts_today = 1

    await bot.handlers.auto_post.run()

    assert openai_server.calls == []
    assert "notes/create" not in misskey_server.calls


def test_environment_overrides_yaml_config(
    monkeypatch: pytest.MonkeyPatch,
    write_config: WriteConfig,
) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "model-from-env")

    config = write_config(openai={"model": "model-from-yaml"})

    assert config.get(ConfigKeys.OPENAI_MODEL) == "model-from-env"


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
    assert await bot.db.cleanup_response_limit_state(max_age_days=30) == 1
    assert await bot.db.get_response_limit_state("old-user") is None


async def test_failed_plugin_initialization_runs_cleanup(
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
) -> None:
    plugins_dir = make_plugin_dir(
        "failing",
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n\n"
        "class FailingPlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n\n"
        "    async def initialize(self):\n"
        "        return False\n\n"
        "    async def cleanup(self):\n"
        "        await self.context.storage.set('cleaned', 'yes')\n",
    )

    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    plugin = bot.plugin_manager.get_plugin("failing")
    assert plugin is not None
    assert bot.plugin_manager.get_plugin_info()[0]["enabled"] is False
    assert await bot.db.get_plugin_data("Failing", "cleaned") == "yes"


async def test_plugin_can_modify_auto_post_prompt(
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    plugins_dir = make_plugin_dir(
        "prompt",
        "from twipsybot.plugin import AutoPostEvent, PLUGIN_API_VERSION, PluginBase\n\n\n"
        "class PromptPlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n\n"
        "    async def on_auto_post(self, event: AutoPostEvent):\n"
        "        return {'prompt': '今日主题：测试。'}\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
    bot.runtime.running = True

    await bot.handlers.auto_post.run()

    prompt = openai_server.calls[0]["messages"][-1]["content"]
    assert prompt.endswith("今日主题：测试。写一条随笔")
    assert misskey_server.calls["notes/create"][0]["text"] == DEFAULT_AI_REPLY


async def test_incompatible_plugin_api_is_rejected(
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
) -> None:
    plugins_dir = make_plugin_dir(
        "future",
        "from twipsybot.plugin import PluginBase\n\n\n"
        "class FuturePlugin(PluginBase):\n"
        "    api_version = 2\n",
    )

    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    assert bot.plugin_manager.get_plugin("future") is None


async def test_bot_ignores_its_own_events(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(write_config())
    own_user = {"id": "bot-id", "username": "testbot"}

    await bot.handlers.chat.handle({**_CHAT_MESSAGE, "user": own_user})
    await bot.handlers.mention.handle({**_MENTION_NOTE, "user": own_user})

    assert openai_server.calls == []
    assert "notes/create" not in misskey_server.calls
    assert "chat/messages/create-to-user" not in misskey_server.calls


def test_legacy_pid_file_is_readable(tmp_path: Path) -> None:
    pid_file = tmp_path / "twipsybot.pid"
    pid_file.write_text(str(os.getpid()), encoding="utf-8")

    assert _read_pid_record(pid_file) == (os.getpid(), None)
    process = _get_bot_process(pid_file)
    assert process is not None
    assert process.pid == os.getpid()


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


async def test_responses_timeout_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    import twipsybot.clients.openai.openai_api as module

    responses = AsyncMock(
        side_effect=[
            APITimeoutError(request=Request("POST", "https://example.com/responses")),
            SimpleNamespace(output_text="retried"),
        ]
    )
    monkeypatch.setattr(module, "make_responses_request", responses)
    monkeypatch.setattr(OpenAIAPI._call_api.retry, "wait", lambda retry_state: 0)
    api = OpenAIAPI("test", api_mode="auto")

    assert await api.generate_text("hello") == "retried"
    assert responses.await_count == 2
    await api.close()
