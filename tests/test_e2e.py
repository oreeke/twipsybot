from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from conftest import (
    DEFAULT_AI_REPLY,
    FakeMisskeyServer,
    FakeOpenAIServer,
    MakeBot,
    WriteConfig,
)

from twipsybot import Config
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
