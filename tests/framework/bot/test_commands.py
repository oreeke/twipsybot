from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from support.events import mention, message
from support.fakes import FakeMisskeyServer, FakeOpenAIServer
from support.flows import (
    CHAT_MESSAGE,
    IMAGE_OPTIONS,
    MENTION_NOTE,
    mock_image_backend,
    settle,
)
from support.plugins import T2X_SOURCE
from support.types import MakeBot, MakePluginDir, WriteConfig

from twipsybot.bot.flows.post import AutoPostService
from twipsybot.clients.misskey.models import MentionEvent, MessageEvent


async def test_admin_command_takes_priority_over_plugins(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
    echo_plugin_dir: Path,
) -> None:
    bot = await make_bot(
        write_config(bot={"admins": ["user-2"]}),
        plugins_dir=echo_plugin_dir,
    )

    await bot.chat.handle(message({**CHAT_MESSAGE, "text": "^help"}))

    assert openai_server.calls == []
    reply = misskey_server.calls["chat/messages/create-to-user"][0]
    assert reply["text"].startswith("可用命令:\n```\n")
    assert "^model - 查看/切换模型 (用法: ^model [模型名]|reset)" in reply["text"]
    assert (
        "/post - 手动发帖 "
        "(用法: /post [-p|-h|-f] [-l] <发一篇关于夏天的帖子，150 字>)" in reply["text"]
    )


async def test_admin_command_is_ignored_in_room_chat(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(
        write_config(
            bot={"admins": ["user-2"]},
            reply={"chat": False},
        )
    )
    raw = {
        **CHAT_MESSAGE,
        "text": "^help @testbot",
        "toRoomId": "room-1",
        "toRoom": {"id": "room-1", "name": "测试房间"},
    }

    await bot.chat.handle(message(raw))

    assert openai_server.calls == []
    assert "chat/messages/create-to-room" not in misskey_server.calls


async def test_chat_can_publish_manual_post_without_counting(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    openai_server.set_reply("夏夜微风正好")
    bot = await make_bot(write_config(bot={"admins": ["user-2"]}))

    await bot.chat.handle(message({**CHAT_MESSAGE, "text": "/post 夏夜的风"}))

    prompt = openai_server.calls[0]["messages"][-1]["content"]
    assert prompt.endswith("夏夜的风")
    assert "写一条随笔" not in prompt
    assert misskey_server.calls["notes/create"][0]["text"] == "夏夜微风正好"
    reply = misskey_server.calls["chat/messages/create-to-user"][0]
    assert reply["text"] == "发帖完成"
    assert bot.auto_post.posts_today == 0
    assert await bot.db.get_auto_post_state() == (bot.auto_post._today(), 0)


@pytest.mark.parametrize(
    ("command", "auto_post", "visibility", "local_only"),
    [
        ("/post -p -l 夏夜的风", {}, "public", True),
        ("/post -h 夏夜的风", {"local_only": True}, "home", False),
        ("/post -f 夏夜的风", {}, "followers", False),
        ("/post -l 夏夜的风", {"visibility": "home"}, "home", True),
        (
            "/post 夏夜的风",
            {"visibility": "followers", "local_only": True},
            "followers",
            True,
        ),
    ],
)
async def test_chat_manual_post_publish_options(
    command: str,
    auto_post: dict[str, object],
    visibility: str,
    local_only: bool,
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
) -> None:
    bot = await make_bot(
        write_config(
            bot={"admins": ["user-2"]},
            autopost=auto_post,
        )
    )

    await bot.chat.handle(message({**CHAT_MESSAGE, "text": command}))

    note = misskey_server.calls["notes/create"][0]
    assert note["visibility"] == visibility
    assert note.get("localOnly", False) is local_only


def test_manual_post_options_preserve_prompt_whitespace() -> None:
    prompt = "第一段\n\n第二段  保留空格"

    assert AutoPostService._parse_manual_options(prompt) == (prompt, None, None)
    assert AutoPostService._parse_manual_options(f"-h -l {prompt}") == (
        prompt,
        "home",
        True,
    )


async def test_chat_rejects_unauthorized_post_command(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(write_config())

    await bot.chat.handle(message({**CHAT_MESSAGE, "text": "/post 夏夜的风"}))

    assert openai_server.calls == []
    assert "notes/create" not in misskey_server.calls
    reply = misskey_server.calls["chat/messages/create-to-user"][0]
    assert reply["text"] == "您没有权限使用命令。"


async def test_chat_rejects_unauthorized_image_command(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
    t2x_dir: Path,
) -> None:
    bot = await make_bot(write_config(), plugins_dir=t2x_dir)
    bot.openai.generate_image = AsyncMock()

    await bot.chat.handle(message({**CHAT_MESSAGE, "text": "/img 一只猫"}))

    bot.openai.generate_image.assert_not_awaited()
    assert openai_server.calls == []
    reply = misskey_server.calls["chat/messages/create-to-user"][0]
    assert reply["text"] == "您没有权限使用命令。"


async def test_slash_command_authentication_precedes_plugins(
    make_bot: MakeBot,
    write_config: WriteConfig,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(write_config())
    bot.plugin_manager.call_plugin_hook = AsyncMock()

    await bot.chat.handle(message({**CHAT_MESSAGE, "text": "/post 夏夜的风"}))

    bot.plugin_manager.call_plugin_hook.assert_not_awaited()
    assert openai_server.calls == []


@pytest.mark.parametrize(
    ("handler", "event"),
    [
        ("chat", message({**CHAT_MESSAGE, "text": "/web 你好"})),
        ("chat", message({**CHAT_MESSAGE, "text": "/bond"})),
        ("mention", mention({**MENTION_NOTE, "text": "@testbot /forget"})),
    ],
    ids=("chat-plugin", "chat-bare", "mention"),
)
async def test_slash_commands_ignore_non_followers(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
    handler: str,
    event: MessageEvent | MentionEvent,
) -> None:
    misskey_server.set_response("users/show", lambda payload: {"isFollowed": False})
    bot = await make_bot(write_config())
    bot.plugin_manager.call_plugin_hook = AsyncMock()

    await getattr(bot, handler).handle(event)

    bot.plugin_manager.call_plugin_hook.assert_not_awaited()
    assert openai_server.calls == []
    assert "notes/create" not in misskey_server.calls
    assert "chat/messages/create-to-user" not in misskey_server.calls


async def test_slash_follower_gate_exempts_admins(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
    t2x_dir: Path,
) -> None:
    misskey_server.set_response("users/show", lambda payload: {"isFollowed": False})
    bot = await make_bot(write_config(bot={"admins": ["user-2"]}), plugins_dir=t2x_dir)
    mock_image_backend(bot, "file-1")

    await bot.chat.handle(message({**CHAT_MESSAGE, "text": "/img 一只猫"}))
    await settle(bot)

    bot.openai.generate_image.assert_awaited_once_with("一只猫", **IMAGE_OPTIONS)
    assert "users/show" not in misskey_server.calls


async def test_slash_follower_check_is_cached_and_fails_closed(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(write_config())

    await bot.chat.handle(message(dict(CHAT_MESSAGE)))
    assert "users/show" not in misskey_server.calls

    for message_id, text in (("msg-2", "/web 你好"), ("msg-3", "/bond")):
        await bot.chat.handle(message({**CHAT_MESSAGE, "id": message_id, "text": text}))
    assert misskey_server.calls["users/show"] == [{"userId": "user-2"}]
    assert len(openai_server.calls) == 3

    bot.admin._followers.clear()
    bot.misskey.show_user = AsyncMock(side_effect=RuntimeError("down"))
    await bot.chat.handle(message({**CHAT_MESSAGE, "id": "msg-4", "text": "/web 你好"}))

    assert len(openai_server.calls) == 3


async def test_enabled_image_plugin_without_model_replies_with_failure(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
    make_plugin_dir: MakePluginDir,
) -> None:
    plugins_dir = make_plugin_dir("t2x", T2X_SOURCE)
    bot = await make_bot(
        write_config(bot={"admins": ["user-2"]}), plugins_dir=plugins_dir
    )

    await bot.chat.handle(message({**CHAT_MESSAGE, "text": "/img 一只猫"}))

    assert openai_server.calls == []
    reply = misskey_server.calls["chat/messages/create-to-user"][0]
    assert reply["text"] == "图片生成失败，请稍后再试。"
    assert "fileId" not in reply
