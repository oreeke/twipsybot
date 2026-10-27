from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

from support.events import message
from support.fakes import FakeMisskeyServer, FakeOpenAIServer
from support.flows import (
    CHAT_MESSAGE,
    IMAGE_OPTIONS,
    mock_image_backend,
    settle,
)
from support.plugins import T2X_SOURCE
from support.types import MakeBot, MakePluginDir, WriteConfig


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


async def test_post_tool_publishes_note_without_counting(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    openai_server.set_tool_call(
        "note_publish", text=" 夏夜微风正好 ", visibility="home", local_only=True
    )
    openai_server.set_reply("已发布")
    bot = await make_bot(write_config(bot={"admins": ["user-2"]}))

    await bot.chat.handle(message({**CHAT_MESSAGE, "text": "发一条关于夏夜的帖子"}))

    first, second = openai_server.calls
    assert [t["function"]["name"] for t in first["tools"]] == ["note_publish"]
    assert second["messages"][-1]["content"] == "Note published."
    note = misskey_server.calls["notes/create"][0]
    assert (note["text"], note["visibility"], note["localOnly"]) == (
        "夏夜微风正好",
        "home",
        True,
    )
    reply = misskey_server.calls["chat/messages/create-to-user"][0]
    assert reply["text"] == "已发布"
    assert bot.auto_post.posts_today == 0


async def test_post_tool_uses_configured_defaults(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    openai_server.set_tool_call(
        "note_publish", text="你好", visibility=None, local_only=None
    )
    bot = await make_bot(
        write_config(
            bot={"admins": ["user-2"]},
            autopost={"visibility": "followers", "local_only": True},
        )
    )

    await bot.chat.handle(message({**CHAT_MESSAGE, "text": "发帖"}))

    note = misskey_server.calls["notes/create"][0]
    assert (note["visibility"], note["localOnly"]) == ("followers", True)


async def test_post_tool_rejects_empty_text(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    openai_server.set_tool_call(
        "note_publish", text="  ", visibility=None, local_only=None
    )
    bot = await make_bot(write_config(bot={"admins": ["user-2"]}))

    await bot.chat.handle(message({**CHAT_MESSAGE, "text": "发帖"}))

    assert "notes/create" not in misskey_server.calls
    assert openai_server.calls[1]["messages"][-1]["content"].startswith("Error")


async def test_post_tool_is_hidden_from_non_admins(
    make_bot: MakeBot,
    write_config: WriteConfig,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(write_config())

    await bot.chat.handle(message({**CHAT_MESSAGE, "text": "发一条帖子"}))

    assert "tools" not in openai_server.calls[0]


async def test_admin_tools_are_hidden_from_non_admins(
    make_bot: MakeBot,
    write_config: WriteConfig,
    openai_server: FakeOpenAIServer,
    t2x_dir: Path,
) -> None:
    bot = await make_bot(write_config(), plugins_dir=t2x_dir)
    bot.openai.generate_image = AsyncMock()

    await bot.chat.handle(message({**CHAT_MESSAGE, "text": "/draw 一只猫"}))

    bot.openai.generate_image.assert_not_awaited()
    assert "tools" not in openai_server.calls[0]
    assert "The user requested the tool" not in str(openai_server.calls[0]["messages"])


async def test_slash_command_forces_admin_tool_call(
    make_bot: MakeBot,
    write_config: WriteConfig,
    openai_server: FakeOpenAIServer,
    t2x_dir: Path,
) -> None:
    openai_server.set_tool_call("t2x_draw", prompt="一只猫")
    bot = await make_bot(write_config(bot={"admins": ["user-2"]}), plugins_dir=t2x_dir)
    mock_image_backend(bot, "file-1")

    await bot.chat.handle(message({**CHAT_MESSAGE, "text": "/draw 一只猫"}))
    await settle(bot)

    bot.openai.generate_image.assert_awaited_once_with("一只猫", **IMAGE_OPTIONS)
    sent = openai_server.calls[0]["messages"][-1]["content"]
    assert sent == "The user requested the tool t2x_draw; call it directly.\n\n一只猫"


async def test_image_tool_without_model_reports_failure_to_model(
    make_bot: MakeBot,
    write_config: WriteConfig,
    openai_server: FakeOpenAIServer,
    make_plugin_dir: MakePluginDir,
) -> None:
    openai_server.set_tool_call("t2x_draw", prompt="一只猫")
    plugins_dir = make_plugin_dir("t2x", T2X_SOURCE)
    bot = await make_bot(
        write_config(bot={"admins": ["user-2"]}), plugins_dir=plugins_dir
    )
    bot.openai.generate_image = AsyncMock()

    await bot.chat.handle(message({**CHAT_MESSAGE, "text": "画一只猫"}))
    await settle(bot)

    bot.openai.generate_image.assert_not_awaited()
    assert (
        openai_server.calls[1]["messages"][-1]["content"]
        == "Image generation is not configured and cannot be used."
    )
