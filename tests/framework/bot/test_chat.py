from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from support.fakes import DEFAULT_AI_REPLY, FakeMisskeyServer, FakeOpenAIServer
from support.flows import CHAT_MESSAGE, IMAGE_OPTIONS, mock_image_backend, settle
from support.plugins import CONTEXT_PLUGIN
from support.types import MakeBot, MakePluginDir, WriteConfig


async def test_chat_message_plugin_takeover(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
    echo_plugin_dir: Path,
) -> None:
    bot = await make_bot(write_config(), plugins_dir=echo_plugin_dir)

    await bot.chat.handle(dict(CHAT_MESSAGE))

    assert openai_server.calls == []
    replies = misskey_server.calls["chat/messages/create-to-user"]
    assert len(replies) == 1
    assert replies[0]["toUserId"] == "user-2"
    assert replies[0]["text"] == "echo: plugin took over"


async def test_chat_context_hook_augments_prompt_but_not_history(
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(
        write_config(),
        plugins_dir=make_plugin_dir("context_test", body=CONTEXT_PLUGIN),
    )

    await bot.chat.handle({**CHAT_MESSAGE, "text": "/web 你好"})

    assert openai_server.calls[0]["messages"][-1]["content"] == "CTX\n\n你好"
    assert bot.chat._histories["user-2"][0] == {"role": "user", "content": "/web 你好"}


async def test_image_only_chat_is_handled_by_mllm_plugin(
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    plugins_dir = make_plugin_dir(
        "mllm_test",
        "from plugins.mllm.plugin import MLLMPlugin as BaseMLLMPlugin\n\n\n"
        "class MLLMPlugin(BaseMLLMPlugin):\n"
        "    pass\n\n"
        "plugin = MLLMPlugin\n",
        config=(
            "enabled: true\n"
            "use_thumbnail: false\n"
            "default_prompt: |-\n"
            "  请描述图片内容。\n"
        ),
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
    bot.misskey.drive.fetch_bytes = AsyncMock(return_value=b"image")
    message = {
        **CHAT_MESSAGE,
        "text": "",
        "fileId": "file-1",
        "file": {
            "id": "file-1",
            "type": "image/png",
            "url": "https://example.com/image.png",
        },
    }

    await bot.chat.handle(message)

    bot.misskey.drive.fetch_bytes.assert_awaited_once()
    user_content = openai_server.calls[0]["messages"][-1]["content"]
    assert user_content[0] == {"type": "text", "text": "请描述图片内容。"}
    assert user_content[1]["type"] == "image_url"
    assert user_content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    reply = misskey_server.calls["chat/messages/create-to-user"][0]
    assert reply["text"] == DEFAULT_AI_REPLY


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

    await bot.chat.handle(dict(CHAT_MESSAGE))

    assert [message["content"] for message in openai_server.calls[0]["messages"]] == [
        "你是测试机器人",
        "上次问题",
        "上次回答",
        "你好，机器人",
    ]
    reply = misskey_server.calls["chat/messages/create-to-user"][0]
    assert reply == {"toUserId": "user-2", "text": DEFAULT_AI_REPLY}


async def test_chat_history_uses_same_message_limit_after_cache_warms(
    make_bot: MakeBot,
    write_config: WriteConfig,
) -> None:
    bot = await make_bot(write_config(reply={"memory": 2}))
    bot.chat._histories["user-2"] = [
        {"role": "user", "content": "旧问题"},
        {"role": "assistant", "content": "旧回答"},
    ]

    bot.chat.append_turn("user-2", "新问题", "新回答", 2)

    assert bot.chat._histories["user-2"] == [
        {"role": "user", "content": "新问题"},
        {"role": "assistant", "content": "新回答"},
    ]


async def test_chat_trims_oldest_history_to_token_budget(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    misskey_server.set_response(
        "chat/messages/user-timeline",
        lambda payload: [
            {
                "text": "最近消息 <|endoftext|>",
                "fromUser": {"id": payload["userId"], "username": "bob"},
            },
            {
                "text": "很久以前的消息" * 20,
                "fromUser": {"id": payload["userId"], "username": "bob"},
            },
        ],
    )
    bot = await make_bot(write_config(reply={"ctx_tokens": 20}))

    await bot.chat.handle(dict(CHAT_MESSAGE))

    assert [message["content"] for message in openai_server.calls[0]["messages"]] == [
        "你是测试机器人",
        "最近消息 <|endoftext|>",
        "你好，机器人",
    ]


async def test_chat_with_disabled_history_skips_misskey_timeline(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
) -> None:
    bot = await make_bot(write_config(reply={"ctx_tokens": 0}))

    await bot.chat.handle(dict(CHAT_MESSAGE))

    assert misskey_server.calls.get("chat/messages/user-timeline", []) == []


async def test_chat_generates_and_attaches_image(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
    t2x_dir: Path,
) -> None:
    bot = await make_bot(write_config(bot={"admins": ["user-2"]}), plugins_dir=t2x_dir)
    mock_image_backend(bot, "file-2")

    await bot.chat.handle({**CHAT_MESSAGE, "text": "/img sunrise over mountains"})
    await settle(bot)

    bot.openai.generate_image.assert_awaited_once_with(
        "sunrise over mountains", **IMAGE_OPTIONS
    )
    assert openai_server.calls == []
    ack, reply = misskey_server.calls["chat/messages/create-to-user"]
    assert ack["text"] == "正在生成图片，请稍候…"
    assert reply["text"] == "图片生成完成"
    assert reply["fileId"] == "file-2"


async def test_plugin_followup_failure_is_logged_without_breaking_reply(
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
) -> None:
    plugins_dir = make_plugin_dir(
        "later",
        "from twipsybot.plugin import PLUGIN_API_VERSION, MessageEvent, PluginBase\n\n\n"
        "class LaterPlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n\n"
        "    async def on_message(self, event: MessageEvent):\n"
        "        async def later():\n"
        "            raise RuntimeError('boom')\n\n"
        "        return self.handled('working', followup=later)\n\n"
        "plugin = LaterPlugin\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    await bot.chat.handle(CHAT_MESSAGE)
    await settle(bot)

    [reply] = misskey_server.calls["chat/messages/create-to-user"]
    assert reply["text"] == "working"
    assert not bot.runtime._background


async def test_room_chat_requires_mention_and_replies_to_room(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(write_config())
    room_message = {
        **CHAT_MESSAGE,
        "toRoomId": "room-1",
        "toRoom": {"id": "room-1", "name": "测试房间"},
    }

    await bot.chat.handle(room_message)
    assert openai_server.calls == []

    await bot.chat.handle({**room_message, "id": "msg-2", "text": "@testbot 你好"})

    reply = misskey_server.calls["chat/messages/create-to-room"][0]
    assert reply["toRoomId"] == "room-1"
    assert reply["text"] == f"@bob\n{DEFAULT_AI_REPLY}"


async def test_room_chat_serializes_shared_history(
    make_bot: MakeBot,
    write_config: WriteConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = await make_bot(write_config(reply={"rate_limit": -1, "max_turns": -1}))
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    calls: list[list[dict[str, str]]] = []

    async def generate_chat(messages: list[dict[str, str]], **_: object) -> str:
        calls.append([dict(message) for message in messages])
        if len(calls) == 1:
            first_started.set()
            await release_first.wait()
            return "第一条回复"
        return "第二条回复"

    monkeypatch.setattr(bot.openai, "generate_chat", generate_chat)
    room = {"toRoomId": "room-1", "toRoom": {"id": "room-1", "name": "测试房间"}}
    first = asyncio.create_task(
        bot.chat.handle(
            {
                **CHAT_MESSAGE,
                **room,
                "text": "@testbot 第一条",
            }
        )
    )
    await first_started.wait()
    second = asyncio.create_task(
        bot.chat.handle(
            {
                **CHAT_MESSAGE,
                **room,
                "id": "msg-2",
                "text": "@testbot 第二条",
                "user": {"id": "user-3", "username": "alice"},
            }
        )
    )
    await asyncio.sleep(0)

    assert len(calls) == 1

    release_first.set()
    await asyncio.gather(first, second)

    assert [message["content"] for message in calls[1]] == [
        "你是测试机器人",
        "bob: @testbot 第一条",
        "第一条回复",
        "alice: @testbot 第二条",
    ]


async def test_room_chat_does_not_publish_manual_post(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(write_config())
    message = {
        **CHAT_MESSAGE,
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
    t2x_dir: Path,
) -> None:
    bot = await make_bot(write_config(bot={"admins": ["user-2"]}), plugins_dir=t2x_dir)
    mock_image_backend(bot, "file-3")
    message = {
        **CHAT_MESSAGE,
        "text": "@testbot /img 一只猫",
        "toRoomId": "room-1",
        "toRoom": {"id": "room-1", "name": "测试房间"},
    }

    await bot.chat.handle(message)
    await settle(bot)

    ack, reply = misskey_server.calls["chat/messages/create-to-room"]
    assert ack["text"] == "@bob\n正在生成图片，请稍候…"
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
        **CHAT_MESSAGE,
        "toRoomId": "room-1",
        "toRoom": {"id": "room-1", "name": "测试房间"},
        "text": "@testbot2 你好",
    }

    await bot.chat.handle(room_message)

    assert openai_server.calls == []
    assert "chat/messages/create-to-room" not in misskey_server.calls


async def test_plugin_chat_reply_can_attach_file(
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
) -> None:
    plugins_dir = make_plugin_dir(
        "echo",
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n\n"
        "class EchoPlugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n\n"
        "    async def on_message(self, event):\n"
        "        return self.handled('pic', 'f1')\n\n"
        "plugin = EchoPlugin\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    await bot.chat.handle(CHAT_MESSAGE)

    [message] = misskey_server.calls["chat/messages/create-to-user"]
    assert (message["text"], message["fileId"]) == ("pic", "f1")
