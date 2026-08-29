from __future__ import annotations

from pathlib import Path

from conftest import (
    DEFAULT_AI_REPLY,
    FakeMisskeyServer,
    FakeOpenAIServer,
    MakeBot,
    MakePluginDir,
    WriteConfig,
)

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


async def test_auto_post_preserves_zero_timestamp(
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
    openai_server: FakeOpenAIServer,
) -> None:
    plugins_dir = make_plugin_dir(
        "epoch",
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n"
        "class EpochPlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n"
        "    async def on_auto_post(self, event):\n"
        "        return {'prompt': 'epoch ', 'timestamp': 0}\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
    bot.runtime.running = True

    await bot.handlers.auto_post.run()

    prompt = openai_server.calls[0]["messages"][-1]["content"]
    assert prompt == "[0] epoch 写一条随笔"


async def test_auto_post_uses_highest_priority_prompt_and_timestamp(
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
    openai_server: FakeOpenAIServer,
) -> None:
    plugins_dir = make_plugin_dir(
        "high",
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n"
        "class HighPlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n"
        "    async def on_auto_post(self, event):\n"
        "        return {'prompt': 'high ', 'timestamp': 1}\n",
        config="enabled: true\npriority: 20\n",
    )
    make_plugin_dir(
        "low",
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n"
        "class LowPlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n"
        "    async def on_auto_post(self, event):\n"
        "        return {'prompt': 'low ', 'timestamp': 2}\n",
        config="enabled: true\npriority: 10\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
    bot.runtime.running = True

    await bot.handlers.auto_post.run()

    prompt = openai_server.calls[0]["messages"][-1]["content"]
    assert prompt == "[1] high 写一条随笔"


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
