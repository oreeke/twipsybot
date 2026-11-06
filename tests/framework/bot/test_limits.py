from __future__ import annotations

import pytest
from support.events import mention, message
from support.fakes import DEFAULT_AI_REPLY, FakeMisskeyServer, FakeOpenAIServer
from support.flows import CHAT_MESSAGE, MENTION_NOTE
from support.types import MakeBot, WriteConfig

from twipsybot.shared.settings import read_settings


async def test_blacklisted_user_is_ignored(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(write_config(blacklist=["alice"]))

    await bot.mention.handle(mention(dict(MENTION_NOTE)))

    assert openai_server.calls == []
    assert "notes/create" not in misskey_server.calls


async def test_bot_accounts_are_ignored_unless_enabled(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    robot = {"id": "robot-id", "username": "robot", "isBot": True}
    bot = await make_bot(write_config())

    await bot.chat.handle(message({**CHAT_MESSAGE, "user": robot}))
    await bot.mention.handle(mention({**MENTION_NOTE, "user": robot}))

    assert openai_server.calls == []
    assert "notes/create" not in misskey_server.calls
    assert "chat/messages/create-to-user" not in misskey_server.calls

    bot = await make_bot(write_config(reply={"ignore_bots": False}))
    await bot.chat.handle(message({**CHAT_MESSAGE, "user": robot}))

    assert len(openai_server.calls) == 1


async def test_permanent_turn_limit_uses_manageable_blacklist(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(
        write_config(
            bot={"admins": ["admin-id"]},
            reply={"max_turns": 1, "turns_release": -1, "max_turns_msg": ""},
        )
    )

    await bot.chat.handle(message(dict(CHAT_MESSAGE)))
    await bot.chat.handle(message({**CHAT_MESSAGE, "id": "msg-2"}))
    await bot.chat.handle(message({**CHAT_MESSAGE, "id": "msg-3"}))

    assert bot.config.settings.reply.blacklist == ["user-2"]
    assert read_settings(bot.config.settings_path)["reply"]["blacklist"] == ["user-2"]
    assert len(openai_server.calls) == 1

    await bot.chat.handle(
        message(
            {
                "id": "admin-msg",
                "text": "^blacklist del user-2",
                "user": {"id": "admin-id", "username": "admin"},
            }
        )
    )
    await bot.chat.handle(message({**CHAT_MESSAGE, "id": "msg-4"}))

    assert bot.config.settings.reply.blacklist == []
    assert len(openai_server.calls) == 2
    replies = misskey_server.calls["chat/messages/create-to-user"]
    assert len(replies) == 3
    assert replies[0]["text"] == replies[2]["text"] == DEFAULT_AI_REPLY


@pytest.mark.parametrize(
    ("limit_msg", "expected"),
    [("请稍后再试", [DEFAULT_AI_REPLY, "请稍后再试"]), ("", [DEFAULT_AI_REPLY])],
    ids=["with-message", "silent"],
)
async def test_rate_limit_blocks_second_ai_call(
    limit_msg: str,
    expected: list[str],
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(
        write_config(reply={"rate_limit": "1h", "rate_limit_msg": limit_msg})
    )

    await bot.chat.handle(message(dict(CHAT_MESSAGE)))
    await bot.chat.handle(message({**CHAT_MESSAGE, "id": "msg-2"}))

    assert len(openai_server.calls) == 1
    replies = misskey_server.calls["chat/messages/create-to-user"]
    assert [reply["text"] for reply in replies] == expected


async def test_admin_commands_bypass_response_limits(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
) -> None:
    bot = await make_bot(
        write_config(
            bot={"admins": ["user-2"]},
            reply={
                "blacklist": ["user-2"],
                "rate_limit": "1h",
                "rate_limit_msg": "请求太频繁",
            },
        )
    )
    await bot.limits.record_response("user-2", count_turn=False)

    await bot.chat.handle(message({**CHAT_MESSAGE, "text": "^help"}))

    [reply] = misskey_server.calls["chat/messages/create-to-user"]
    assert reply["text"].startswith("可用命令")


async def test_admins_bypass_response_limits(
    make_bot: MakeBot, write_config: WriteConfig, openai_server: FakeOpenAIServer
) -> None:
    bot = await make_bot(
        write_config(
            bot={"admins": ["user-2"]}, reply={"rate_limit": "1h", "max_turns": 1}
        )
    )

    for index in range(3):
        await bot.chat.handle(message({**CHAT_MESSAGE, "id": f"msg-{index}"}))

    assert len(openai_server.calls) == 3


async def test_responses_are_recorded_only_when_a_limit_is_enabled(
    make_bot: MakeBot, write_config: WriteConfig
) -> None:
    bot = await make_bot(write_config())

    await bot.limits.record_response("user-9", count_turn=True)
    assert await bot.db.get_response_limit_state("user-9") is None

    await bot.settings.update({"reply.max_turns": 5})
    await bot.limits.record_response("user-9", count_turn=True)
    assert (await bot.db.get_response_limit_state("user-9"))[1] == 1
