from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

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


async def test_streaming_awaits_wrapped_async_handler(
    make_bot: MakeBot, write_config: WriteConfig
) -> None:
    bot = await make_bot(write_config())
    received = []

    async def handler(data):
        received.append(data)

    bot.streaming.event_handlers["note"] = [lambda data: handler(data)]

    await bot.streaming._call_handlers("note", {"id": "note-1"})

    assert received == [{"id": "note-1"}]


async def test_mention_triggers_ai_reply(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(write_config())

    await bot.mention.handle(dict(_MENTION_NOTE))

    assert len(openai_server.calls) == 1
    notes = misskey_server.calls["notes/create"]
    assert len(notes) == 1
    assert notes[0]["text"] == f"@alice\n{DEFAULT_AI_REPLY}"
    assert notes[0]["replyId"] == "note-mention-1"


async def test_mention_generates_and_attaches_image(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(
        write_config(
            openai={"image_model": "gpt-image-1"},
            bot={"admin": {"allowed_users": ["user-1"]}},
        )
    )
    bot.openai.generate_image = AsyncMock(return_value=b"\x89PNG\r\n\x1a\nimage")
    bot.misskey.drive.upload_bytes = AsyncMock(return_value={"id": "file-1"})

    await bot.mention.handle({**_MENTION_NOTE, "text": "@testbot /img 一只猫"})

    bot.openai.generate_image.assert_awaited_once_with("一只猫")
    bot.misskey.drive.upload_bytes.assert_awaited_once_with(
        b"\x89PNG\r\n\x1a\nimage",
        name="generated.png",
        content_type="image/png",
    )
    assert openai_server.calls == []
    note = misskey_server.calls["notes/create"][0]
    assert note["text"] == "@alice\n图片生成完成"
    assert note["fileIds"] == ["file-1"]


async def test_mention_does_not_publish_manual_post(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(write_config())

    await bot.mention.handle({**_MENTION_NOTE, "text": "@testbot /post 夏夜的风"})

    assert openai_server.calls == []
    assert "notes/create" not in misskey_server.calls
    assert bot.auto_post.posts_today == 0


async def test_mention_text_without_bot_id_is_ignored(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(write_config())

    await bot.mention.handle({**_MENTION_NOTE, "mentions": ["other-id"]})

    assert openai_server.calls == []
    assert "notes/create" not in misskey_server.calls


async def test_chat_message_plugin_takeover(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
    echo_plugin_dir: Path,
) -> None:
    bot = await make_bot(write_config(), plugins_dir=echo_plugin_dir)

    await bot.chat.handle(dict(_CHAT_MESSAGE))

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
        write_config(bot={"admin": {"allowed_users": ["user-2"]}}),
        plugins_dir=echo_plugin_dir,
    )

    await bot.chat.handle({**_CHAT_MESSAGE, "text": "^help"})

    assert openai_server.calls == []
    reply = misskey_server.calls["chat/messages/create-to-user"][0]
    assert reply["text"].startswith("可用命令:\n```\n")
    assert "^model - 查看/切换模型 (用法: ^model [模型名]|reset)" in reply["text"]
    assert (
        "\n\n/img - 生成图片 (用法: /img <一只小狗在月球上弹吉他>)\n" in reply["text"]
    )
    assert (
        "/post - 指示 AI 现在就发一篇帖子 "
        "(用法: /post <你更喜欢夏天还是冬天？200 字以内>)" in reply["text"]
    )


async def test_admin_command_is_ignored_in_room_chat(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(
        write_config(
            bot={
                "admin": {"allowed_users": ["user-2"]},
                "response": {"chat": False},
            }
        )
    )
    message = {
        **_CHAT_MESSAGE,
        "text": "^help @testbot",
        "toRoomId": "room-1",
        "toRoom": {"id": "room-1", "name": "测试房间"},
    }

    await bot.chat.handle(message)

    assert openai_server.calls == []
    assert "chat/messages/create-to-room" not in misskey_server.calls


async def test_auto_post_publishes_note(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    openai_server.set_reply("今天天气不错")
    bot = await make_bot(write_config())
    bot.runtime.running = True

    await bot.auto_post.run()

    notes = misskey_server.calls["notes/create"]
    assert len(notes) == 1
    assert notes[0]["text"] == "今天天气不错"
    assert notes[0]["visibility"] == "public"
    assert bot.auto_post.posts_today == 1
    assert await bot.db.get_auto_post_state() == (bot.auto_post._today(), 1)


async def test_auto_post_restores_counter_and_resets_stale_date(
    make_bot: MakeBot, write_config: WriteConfig
) -> None:
    bot = await make_bot(write_config())
    await bot.db.set_auto_post_state(bot.auto_post._today(), 3)

    await bot.auto_post.start()
    assert bot.auto_post.posts_today == 3

    await bot.db.set_auto_post_state("2000-01-01", 7)
    await bot.auto_post.start()
    assert bot.auto_post.posts_today == 0
    assert await bot.db.get_auto_post_state() == (bot.auto_post._today(), 0)


async def test_auto_post_counter_self_corrects_after_date_change(
    make_bot: MakeBot, write_config: WriteConfig
) -> None:
    bot = await make_bot(write_config())
    bot.auto_post._post_date = "2000-01-01"
    bot.auto_post.posts_today = 7

    assert await bot.auto_post.check_post_counter(1) is True
    assert bot.auto_post.posts_today == 0
    assert await bot.db.get_auto_post_state() == (bot.auto_post._today(), 0)


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

    await bot.auto_post.run()

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

    await bot.auto_post.run()

    prompt = openai_server.calls[0]["messages"][-1]["content"]
    assert prompt == "[1] high 写一条随笔"


async def test_blacklisted_user_is_ignored(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(write_config(blacklist=["alice"]))

    await bot.mention.handle(dict(_MENTION_NOTE))

    assert openai_server.calls == []
    assert "notes/create" not in misskey_server.calls


async def test_permanent_turn_limit_uses_manageable_blacklist(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(
        write_config(
            bot={
                "admin": {"allowed_users": ["admin-id"]},
                "response": {"max_turns": 1, "max_turns_release": -1},
            }
        )
    )

    await bot.chat.handle(dict(_CHAT_MESSAGE))
    await bot.chat.handle({**_CHAT_MESSAGE, "id": "msg-2"})
    await bot.chat.handle({**_CHAT_MESSAGE, "id": "msg-3"})

    assert bot.config.get("bot.response.blacklist") == ["user-2"]
    assert (
        await bot.db.get_plugin_data("Admin", "bot.response.blacklist") == '["user-2"]'
    )
    assert len(openai_server.calls) == 1

    await bot.chat.handle(
        {
            "id": "admin-msg",
            "text": "^blacklist del user-2",
            "user": {"id": "admin-id", "username": "admin"},
        }
    )
    await bot.chat.handle({**_CHAT_MESSAGE, "id": "msg-4"})

    assert bot.config.get("bot.response.blacklist") == []
    assert len(openai_server.calls) == 2
    replies = misskey_server.calls["chat/messages/create-to-user"]
    assert [reply["text"] for reply in replies] == [
        DEFAULT_AI_REPLY,
        "我要回家了...",
        "查看/修改黑名单:\n```\n已更新 blacklist\n\n(空)\n```",
        DEFAULT_AI_REPLY,
    ]


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

    await bot.chat.handle(dict(_CHAT_MESSAGE))

    assert [message["content"] for message in openai_server.calls[0]["messages"]] == [
        "你是测试机器人",
        "上次问题",
        "上次回答",
        "你好，机器人",
    ]
    reply = misskey_server.calls["chat/messages/create-to-user"][0]
    assert reply == {"i": "test-token", "toUserId": "user-2", "text": DEFAULT_AI_REPLY}


async def test_chat_generates_and_attaches_image(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(
        write_config(
            openai={"image_model": "gpt-image-1"},
            bot={"admin": {"allowed_users": ["user-2"]}},
        )
    )
    bot.openai.generate_image = AsyncMock(return_value=b"\x89PNG\r\n\x1a\nimage")
    bot.misskey.drive.upload_bytes = AsyncMock(return_value={"id": "file-2"})

    await bot.chat.handle({**_CHAT_MESSAGE, "text": "/img sunrise over mountains"})

    bot.openai.generate_image.assert_awaited_once_with("sunrise over mountains")
    assert openai_server.calls == []
    reply = misskey_server.calls["chat/messages/create-to-user"][0]
    assert reply["text"] == "图片生成完成"
    assert reply["fileId"] == "file-2"


async def test_chat_can_publish_manual_post_without_counting(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    openai_server.set_reply("夏夜微风正好")
    bot = await make_bot(write_config(bot={"admin": {"allowed_users": ["user-2"]}}))

    await bot.chat.handle({**_CHAT_MESSAGE, "text": "/post 夏夜的风"})

    prompt = openai_server.calls[0]["messages"][-1]["content"]
    assert prompt.endswith("夏夜的风")
    assert "写一条随笔" not in prompt
    assert misskey_server.calls["notes/create"][0]["text"] == "夏夜微风正好"
    reply = misskey_server.calls["chat/messages/create-to-user"][0]
    assert reply["text"] == "发帖完成"
    assert bot.auto_post.posts_today == 0
    assert await bot.db.get_auto_post_state() == (bot.auto_post._today(), 0)


async def test_chat_rejects_unauthorized_manual_post(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(write_config())

    await bot.chat.handle({**_CHAT_MESSAGE, "text": "/post 夏夜的风"})

    assert openai_server.calls == []
    assert "notes/create" not in misskey_server.calls
    reply = misskey_server.calls["chat/messages/create-to-user"][0]
    assert reply["text"] == "您没有权限使用命令。"


async def test_slash_command_authentication_precedes_plugins(
    make_bot: MakeBot,
    write_config: WriteConfig,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(write_config())
    bot.plugin_manager.call_plugin_hook = AsyncMock()

    await bot.chat.handle({**_CHAT_MESSAGE, "text": "/post 夏夜的风"})

    bot.plugin_manager.call_plugin_hook.assert_not_awaited()
    assert openai_server.calls == []


async def test_admin_and_slash_commands_share_response_limits(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
) -> None:
    bot = await make_bot(
        write_config(
            openai={"image_model": "gpt-image-1"},
            bot={
                "admin": {"allowed_users": ["user-2"]},
                "response": {
                    "rate_limit": "1h",
                    "rate_limit_reply": "请求太频繁",
                },
            },
        )
    )
    bot.openai.generate_image = AsyncMock()
    await bot.limits.record_response("user-2", count_turn=False)

    await bot.chat.handle({**_CHAT_MESSAGE, "text": "^help"})
    await bot.chat.handle({**_CHAT_MESSAGE, "id": "msg-2", "text": "/img 一只猫"})

    bot.openai.generate_image.assert_not_awaited()
    replies = misskey_server.calls["chat/messages/create-to-user"]
    assert [reply["text"] for reply in replies] == ["请求太频繁", "请求太频繁"]


async def test_disabled_image_generation_replies_with_failure(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(write_config(bot={"admin": {"allowed_users": ["user-2"]}}))

    await bot.chat.handle({**_CHAT_MESSAGE, "text": "/img 一只猫"})

    assert openai_server.calls == []
    reply = misskey_server.calls["chat/messages/create-to-user"][0]
    assert reply["text"] == "图片生成失败，请稍后再试。"
    assert "fileId" not in reply


async def test_chat_rejects_unauthorized_image_generation(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(write_config(openai={"image_model": "gpt-image-1"}))
    bot.openai.generate_image = AsyncMock()

    await bot.chat.handle({**_CHAT_MESSAGE, "text": "/img 一只猫"})

    bot.openai.generate_image.assert_not_awaited()
    assert openai_server.calls == []
    reply = misskey_server.calls["chat/messages/create-to-user"][0]
    assert reply["text"] == "您没有权限使用命令。"


async def test_image_send_failure_does_not_record_response(
    make_bot: MakeBot, write_config: WriteConfig
) -> None:
    bot = await make_bot(
        write_config(
            openai={"image_model": "gpt-image-1"},
            bot={"admin": {"allowed_users": ["user-2"]}},
        )
    )
    bot.openai.generate_image = AsyncMock(return_value=b"\x89PNG\r\n\x1a\nimage")
    bot.misskey.drive.upload_bytes = AsyncMock(return_value={"id": "file-2"})
    bot.misskey.send_message = AsyncMock(side_effect=RuntimeError("send failed"))
    bot.limits.record_response = AsyncMock()

    await bot.chat.handle({**_CHAT_MESSAGE, "text": "/img 一只猫"})

    bot.misskey.send_message.assert_awaited_once_with(
        "user-2", "图片生成完成", "file-2"
    )
    bot.limits.record_response.assert_not_awaited()


async def test_image_record_failure_does_not_send_failure_reply(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
) -> None:
    bot = await make_bot(
        write_config(
            openai={"image_model": "gpt-image-1"},
            bot={"admin": {"allowed_users": ["user-2"]}},
        )
    )
    bot.openai.generate_image = AsyncMock(return_value=b"\x89PNG\r\n\x1a\nimage")
    bot.misskey.drive.upload_bytes = AsyncMock(return_value={"id": "file-2"})
    bot.limits.record_response = AsyncMock(side_effect=RuntimeError("database failed"))

    await bot.chat.handle({**_CHAT_MESSAGE, "text": "/img 一只猫"})

    replies = misskey_server.calls["chat/messages/create-to-user"]
    assert len(replies) == 1
    assert replies[0]["fileId"] == "file-2"


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

    await bot.chat.handle(room_message)
    assert openai_server.calls == []

    await bot.chat.handle({**room_message, "id": "msg-2", "text": "@testbot 你好"})

    reply = misskey_server.calls["chat/messages/create-to-room"][0]
    assert reply["toRoomId"] == "room-1"
    assert reply["text"] == f"@bob\n{DEFAULT_AI_REPLY}"


async def test_room_chat_does_not_publish_manual_post(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(write_config())
    message = {
        **_CHAT_MESSAGE,
        "text": "@testbot /post 夏夜的风",
        "toRoomId": "room-1",
        "toRoom": {"id": "room-1", "name": "测试房间"},
    }

    await bot.chat.handle(message)

    assert openai_server.calls == []
    assert "notes/create" not in misskey_server.calls
    assert "chat/messages/create-to-room" not in misskey_server.calls
    assert bot.auto_post.posts_today == 0


async def test_room_chat_generates_and_attaches_image(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
) -> None:
    bot = await make_bot(
        write_config(
            openai={"image_model": "gpt-image-1"},
            bot={"admin": {"allowed_users": ["user-2"]}},
        )
    )
    bot.openai.generate_image = AsyncMock(return_value=b"\x89PNG\r\n\x1a\nimage")
    bot.misskey.drive.upload_bytes = AsyncMock(return_value={"id": "file-3"})
    message = {
        **_CHAT_MESSAGE,
        "text": "@testbot /img 一只猫",
        "toRoomId": "room-1",
        "toRoom": {"id": "room-1", "name": "测试房间"},
    }

    await bot.chat.handle(message)

    reply = misskey_server.calls["chat/messages/create-to-room"][0]
    assert reply["toRoomId"] == "room-1"
    assert reply["text"] == "@bob\n图片生成完成"
    assert reply["fileId"] == "file-3"


async def test_room_chat_does_not_match_longer_username(
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
        "text": "@testbot2 你好",
    }

    await bot.chat.handle(room_message)

    assert openai_server.calls == []
    assert "chat/messages/create-to-room" not in misskey_server.calls


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

    await bot.mention.handle(event)

    prompt = openai_server.calls[0]["messages"][-1]["content"]
    assert prompt == "机器人原帖\n\n继续说说"
    assert misskey_server.calls["notes/create"][0]["replyId"] == "reply-1"


async def test_reply_to_same_username_with_other_id_is_ignored(
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
                "text": "同名用户原帖",
                "user": {"id": "other-id", "username": "testbot"},
            },
        },
    }

    await bot.mention.handle(event)

    assert openai_server.calls == []
    assert "notes/create" not in misskey_server.calls


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

    await bot.chat.handle(dict(_CHAT_MESSAGE))
    await bot.chat.handle({**_CHAT_MESSAGE, "id": "msg-2"})

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

    await bot.mention.handle(dict(_MENTION_NOTE))

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
    bot.auto_post.posts_today = 1

    await bot.auto_post.run()

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

    await bot.auto_post.run()

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

    await bot.chat.handle({**_CHAT_MESSAGE, "user": own_user})
    await bot.mention.handle({**_MENTION_NOTE, "user": own_user})

    assert openai_server.calls == []
    assert "notes/create" not in misskey_server.calls
    assert "chat/messages/create-to-user" not in misskey_server.calls
