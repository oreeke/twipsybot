from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from support.events import message
from support.fakes import FakeMisskeyServer, FakeOpenAIServer
from support.flows import (
    CHAT_MESSAGE,
    IMAGE_OPTIONS,
    mock_image_backend,
    settle,
    tool_names,
)
from support.plugins import T2X_SOURCE
from support.types import MakeBot, MakePluginDir, WriteConfig

from twipsybot import Neuro


async def say(bot: Neuro, text: str) -> None:
    await bot.chat.handle(message({**CHAT_MESSAGE, "text": text}))


async def test_admin_command_takes_priority_over_plugins(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
    echo_plugin_dir: Path,
) -> None:
    bot = await make_bot(
        write_config(bot={"admins": ["user-2"]}), plugins_dir=echo_plugin_dir
    )

    await say(bot, "^help")

    assert openai_server.calls == []
    [reply] = misskey_server.calls["chat/messages/create-to-user"]
    assert reply["text"].startswith("可用命令:")


async def test_admin_command_is_ignored_in_room_chat(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(
        write_config(bot={"admins": ["user-2"]}, reply={"chat": False})
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
    admin_bot: Neuro,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    openai_server.set_tool_call(
        "note_publish", text=" 夏夜微风正好 ", visibility="home", local_only=True
    )
    openai_server.set_reply("已发布")

    await say(admin_bot, "发一条关于夏夜的帖子")

    first, second = openai_server.calls
    assert tool_names(first) == [
        "note_cancel",
        "note_pending",
        "note_publish",
        "time_now",
    ]
    assert second["messages"][-1]["content"] == "Note published."
    [note] = misskey_server.calls["notes/create"]
    assert (note["text"], note["visibility"], note["localOnly"]) == (
        "夏夜微风正好",
        "home",
        True,
    )
    [reply] = misskey_server.calls["chat/messages/create-to-user"]
    assert reply["text"] == "已发布"
    assert admin_bot.auto_post.posts_today == 0


async def test_post_tool_falls_back_to_configured_visibility(
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

    await say(bot, "发帖")

    note = misskey_server.calls["notes/create"][0]
    assert (note["visibility"], note["localOnly"]) == ("followers", True)


@pytest.mark.parametrize(
    "arguments",
    [
        {"text": "  "},
        {"text": "你好", "at": "08:00"},
        {"text": "你好", "at": "25:99"},
        {"text": "你好", "at": "2000-01-01 08:00"},
    ],
    ids=["empty-text", "time-only", "invalid-time", "past-time"],
)
async def test_post_tool_rejects_invalid_arguments(
    arguments: dict[str, Any],
    admin_bot: Neuro,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    openai_server.set_tool_call("note_publish", **arguments)

    await say(admin_bot, "发帖")

    assert "notes/create" not in misskey_server.calls
    assert openai_server.calls[1]["messages"][-1]["content"].startswith("Error")


async def test_post_tool_schedules_note_and_cancels_it(
    admin_bot: Neuro,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    openai_server.set_tool_call(
        "note_publish",
        text="夏夜观星",
        visibility="home",
        local_only=True,
        at="2099-07-01 08:00",
    )
    admin_bot.runtime.running = True
    admin_bot.scheduler.start()
    try:
        await say(admin_bot, "7月1日8点发帖")

        assert "notes/create" not in misskey_server.calls
        [job] = admin_bot.scheduler.get_jobs()
        assert job.next_run_time.strftime("%Y-%m-%d %H:%M") == "2099-07-01 08:00"
        assert job.id in openai_server.calls[1]["messages"][-1]["content"]
        event = message(CHAT_MESSAGE)
        assert job.id in await admin_bot.auto_post.pending(event)

        await job.func(*job.args)
        note = misskey_server.calls["notes/create"][0]
        assert (note["text"], note["visibility"], note["localOnly"]) == (
            "夏夜观星",
            "home",
            True,
        )

        assert (await admin_bot.auto_post.cancel(event, "missing")).startswith("Error")
        cancelled = await admin_bot.auto_post.cancel(event, job.id)
        assert cancelled == "Scheduled note cancelled."
        assert await admin_bot.auto_post.pending(event) == "No scheduled notes."
    finally:
        admin_bot.scheduler.shutdown(wait=False)


@pytest.mark.parametrize("running", [False, True])
async def test_scheduled_note_is_skipped_or_failure_is_contained(
    running: bool, admin_bot: Neuro, misskey_server: FakeMisskeyServer
) -> None:
    admin_bot.runtime.running = running
    admin_bot.scheduler.start()
    try:
        await admin_bot.auto_post.publish(
            message(CHAT_MESSAGE), "夏夜观星", at="2099-07-01 08:00"
        )
        [job] = admin_bot.scheduler.get_jobs()
        admin_bot.misskey.create_note = AsyncMock(side_effect=RuntimeError("down"))

        await job.func(*job.args)

        assert admin_bot.misskey.create_note.await_count == int(running)
        assert "notes/create" not in misskey_server.calls
    finally:
        admin_bot.scheduler.shutdown(wait=False)


async def test_time_tool_is_available_to_everyone(
    make_bot: MakeBot, write_config: WriteConfig, openai_server: FakeOpenAIServer
) -> None:
    openai_server.set_tool_call("time_now")
    bot = await make_bot(write_config())

    await say(bot, "现在几点")

    first, second = openai_server.calls
    assert tool_names(first) == ["time_now"]
    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2} \w{3} \d{2}:\d{2} [+-]\d{4}",
        second["messages"][-1]["content"],
    )


async def test_admin_tools_and_slash_commands_are_unavailable_to_non_admins(
    make_bot: MakeBot,
    write_config: WriteConfig,
    openai_server: FakeOpenAIServer,
    t2x_dir: Path,
) -> None:
    bot = await make_bot(write_config(), plugins_dir=t2x_dir)
    bot.openai.generate_image = AsyncMock()

    await say(bot, "/draw 一只猫")

    bot.openai.generate_image.assert_not_awaited()
    assert tool_names(openai_server.calls[0]) == ["time_now"]
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

    await say(bot, "/draw 一只猫")
    await settle(bot)

    bot.openai.generate_image.assert_awaited_once_with("一只猫", **IMAGE_OPTIONS)
    sent = openai_server.calls[0]["messages"][-1]["content"]
    assert sent == "The user requested the tool t2x_draw; call it directly.\n\n一只猫"


async def test_image_tool_without_model_reports_failure_to_model(
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
    openai_server: FakeOpenAIServer,
) -> None:
    openai_server.set_tool_call("t2x_draw", prompt="一只猫")
    bot = await make_bot(
        write_config(bot={"admins": ["user-2"]}),
        plugins_dir=make_plugin_dir("t2x", T2X_SOURCE),
    )
    bot.openai.generate_image = AsyncMock()

    await say(bot, "画一只猫")
    await settle(bot)

    bot.openai.generate_image.assert_not_awaited()
    assert (
        openai_server.calls[1]["messages"][-1]["content"]
        == "Image generation is not configured and cannot be used."
    )
