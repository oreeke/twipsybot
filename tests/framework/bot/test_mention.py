from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from support.events import mention, message
from support.fakes import DEFAULT_AI_REPLY, FakeMisskeyServer, FakeOpenAIServer
from support.flows import (
    CHAT_MESSAGE,
    IMAGE_OPTIONS,
    MENTION_NOTE,
    PNG,
    mock_image_backend,
    offers_effect_tools,
    reply_event,
    settle,
    thread_chain,
    thread_note,
)
from support.plugins import CONTEXT_PLUGIN
from support.types import MakeBot, MakePluginDir, WriteConfig

from twipsybot import Neuro
from twipsybot.clients.misskey.models import MentionEvent
from twipsybot.shared.errors import APIBadRequestError

type MakeHookPlugin = Callable[[str, str], Path]

BOT_USER = {"id": "bot-id", "username": "testbot"}


def reply_to_bot(
    text: str = "继续说说",
    *,
    host: str | None = None,
    origin_user: dict[str, str] = BOT_USER,
) -> MentionEvent:
    user = {"id": "user-1", "username": "alice", **({"host": host} if host else {})}
    note = {
        "id": "reply-1",
        "text": text,
        "user": user,
        "reply": {"id": "origin-1", "text": "机器人原帖", "user": origin_user},
    }
    return mention(note, "reply")


def messages_of(call: dict[str, Any]) -> list[dict[str, Any]]:
    return [m for m in call["messages"] if m["role"] != "system"]


async def test_mention_triggers_ai_reply(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(write_config())

    await bot.mention.handle(mention(MENTION_NOTE))

    assert len(openai_server.calls) == 1
    [note] = misskey_server.calls["notes/create"]
    assert note["text"] == f"@alice\n{DEFAULT_AI_REPLY}"
    assert note["replyId"] == "note-mention-1"


async def test_remote_mention_uses_federated_handle(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
) -> None:
    bot = await make_bot(write_config())
    remote_user = {"id": "remote-user-1", "username": "alice", "host": "remote.example"}

    await bot.mention.handle(mention({**MENTION_NOTE, "user": remote_user}))

    note = misskey_server.calls["notes/create"][0]
    assert note["text"] == f"@alice@remote.example\n{DEFAULT_AI_REPLY}"


async def test_specified_mention_replies_with_specified_visibility(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
) -> None:
    bot = await make_bot(write_config())

    await bot.mention.handle(mention({**MENTION_NOTE, "visibility": "specified"}))

    assert misskey_server.calls["notes/create"][0]["visibility"] == "specified"


async def test_mention_truncates_generated_reply_after_adding_mention(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    openai_server.set_reply("x" * 3000)
    bot = await make_bot(write_config())

    await bot.mention.handle(mention(MENTION_NOTE))

    text = misskey_server.calls["notes/create"][0]["text"]
    assert len(text) == 3000
    assert text.startswith("@alice\n")
    assert text.endswith("…")


async def test_mention_skips_generation_when_source_note_is_missing(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = await make_bot(write_config())
    get_note = AsyncMock(side_effect=APIBadRequestError("NO_SUCH_NOTE: No such note."))
    monkeypatch.setattr(bot.misskey, "get_note", get_note)

    await bot.mention.handle(mention(MENTION_NOTE))

    get_note.assert_awaited_once_with("note-mention-1")
    assert openai_server.calls == []
    assert "notes/create" not in misskey_server.calls


@pytest.mark.parametrize(
    "event",
    [
        mention({**MENTION_NOTE, "mentions": ["other-id"]}),
        reply_to_bot(origin_user={"id": "other-id", "username": "testbot"}),
    ],
    ids=["mentions-other-account", "reply-to-same-username-other-id"],
)
async def test_events_not_addressed_to_bot_are_ignored(
    event: MentionEvent,
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(write_config())

    await bot.mention.handle(event)

    assert openai_server.calls == []
    assert "notes/create" not in misskey_server.calls


async def test_bot_ignores_its_own_events(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(write_config())

    await bot.chat.handle(message({**CHAT_MESSAGE, "user": BOT_USER}))
    await bot.mention.handle(mention({**MENTION_NOTE, "user": BOT_USER}))

    assert openai_server.calls == []
    assert "notes/create" not in misskey_server.calls
    assert "chat/messages/create-to-user" not in misskey_server.calls


@pytest.mark.parametrize(
    ("event", "ack", "text"),
    [
        (
            mention({**MENTION_NOTE, "text": "@testbot 画一只猫"}),
            "@alice\n好的",
            "@alice\n",
        ),
        (reply_to_bot("画一只猫"), "好的", None),
    ],
    ids=["mention", "reply-to-bot"],
)
async def test_tool_call_attaches_generated_image(
    event: MentionEvent,
    ack: str,
    text: str | None,
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
    t2x_dir: Path,
) -> None:
    openai_server.set_tool_call("t2x_draw", prompt="一只猫")
    openai_server.set_reply("好的")
    bot = await make_bot(write_config(bot={"admins": ["user-1"]}), plugins_dir=t2x_dir)
    mock_image_backend(bot, "file-1")

    await bot.mention.handle(event)
    await settle(bot)

    bot.openai.generate_image.assert_awaited_once_with("一只猫", **IMAGE_OPTIONS)
    bot.misskey.drive.upload_bytes.assert_awaited_once_with(
        PNG, name="generated.png", content_type="image/png"
    )
    first, note = misskey_server.calls["notes/create"]
    assert first["text"] == ack
    assert note.get("text") == text
    assert note["fileIds"] == ["file-1"]


@pytest.mark.parametrize(
    ("author", "tools_offered"), [("user-9", False), ("user-1", True)]
)
async def test_foreign_parent_note_disables_effect_tools(
    author: str,
    tools_offered: bool,
    make_bot: MakeBot,
    write_config: WriteConfig,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(write_config(bot={"admins": ["user-1"]}))
    note = {
        **MENTION_NOTE,
        "text": "@testbot 总结一下",
        "replyId": "parent-1",
        "reply": {
            "id": "parent-1",
            "text": "原帖",
            "user": {"id": author, "username": "someone"},
        },
    }

    await bot.mention.handle(mention(note))

    assert offers_effect_tools(openai_server.calls[0]) is tools_offered


async def test_foreign_thread_root_disables_effect_tools(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    misskey_server.set_response("notes/conversation", lambda payload: thread_chain())
    bot = await make_bot(write_config(bot={"admins": ["user-1"]}))

    await bot.mention.handle(reply_event())

    assert not offers_effect_tools(openai_server.calls[0])


async def test_mention_context_hook_augments_prompt(
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(
        write_config(),
        plugins_dir=make_plugin_dir("context_test", body=CONTEXT_PLUGIN),
    )

    await bot.mention.handle(mention({**MENTION_NOTE, "text": "@testbot /web 你好"}))

    assert openai_server.calls[0]["messages"][-1]["content"] == "CTX\n\n@testbot 你好"


async def test_mention_with_replied_image_is_handled_by_mllm_plugin(
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
        config="enabled: true\nuse_thumbnail: false\n",
    )
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
    bot.misskey.drive.fetch_bytes = AsyncMock(return_value=b"image")
    image = {"id": "file-1", "type": "image/png", "url": "https://example.com/i.png"}
    note = {
        **MENTION_NOTE,
        "text": "@testbot 这是什么",
        "replyId": "note-0",
        "reply": {
            "id": "note-0",
            "text": "看这张图",
            "user": {"id": "user-2", "username": "bob"},
            "files": [image],
        },
    }

    await bot.mention.handle(mention(note))

    bot.misskey.drive.fetch_bytes.assert_awaited_once()
    user_content = openai_server.calls[0]["messages"][-1]["content"]
    assert user_content[0] == {
        "type": "text",
        "text": "@testbot 这是什么\n\nReplying to:\n看这张图",
    }
    assert user_content[1]["type"] == "image_url"
    assert misskey_server.calls["notes/create"][0]["replyId"] == "note-mention-1"


async def test_reply_uses_conversation_with_root_and_participants_only(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    misskey_server.set_response("notes/conversation", lambda payload: thread_chain())
    bot = await make_bot(write_config(reply={"memory": 3}))

    await bot.mention.handle(reply_event())

    assert misskey_server.calls["notes/conversation"] == [{"noteId": "u2", "limit": 3}]
    assert messages_of(openai_server.calls[0]) == [
        {"role": "user", "content": "@bob: 主帖"},
        {"role": "user", "content": "@testbot 问题"},
        {"role": "assistant", "content": "bot reply"},
        {"role": "user", "content": "继续"},
    ]


async def test_conversation_skips_hidden_notes_and_handles_single_parent(
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    hidden = {**thread_note("b1", "bot-id", "testbot", ""), "isHidden": True}
    chains = iter(
        [
            [hidden, thread_note("p1", "user-2", "bob", "主帖")],
            [thread_note("p1", "user-2", "bob", "主帖")],
        ]
    )
    misskey_server.set_response("notes/conversation", lambda payload: next(chains))
    bot = await make_bot(write_config())

    await bot.mention.handle(reply_event())
    await bot.mention.handle(reply_event())

    for call in openai_server.calls:
        assert messages_of(call) == [
            {"role": "user", "content": "@bob: 主帖"},
            {"role": "user", "content": "继续"},
        ]


@pytest.mark.parametrize("memory", [0, 10], ids=["disabled", "lookup-fails"])
async def test_reply_falls_back_to_parent_prompt_without_conversation(
    memory: int,
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = await make_bot(write_config(reply={"memory": memory}))
    lookup = AsyncMock(side_effect=APIBadRequestError("NO_SUCH_NOTE"))
    monkeypatch.setattr(bot.misskey, "get_note_conversation", lookup)

    await bot.mention.handle(reply_event())

    assert lookup.await_count == int(memory > 0)
    assert openai_server.calls[0]["messages"][-1]["content"] == "bot reply\n\n继续"


@pytest.mark.parametrize("host", [None, "remote.example"])
async def test_reply_to_bot_triggers_ai_reply(
    host: str | None,
    make_bot: MakeBot,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(write_config())

    await bot.mention.handle(reply_to_bot(host=host))

    assert openai_server.calls[0]["messages"][-1]["content"] == "机器人原帖\n\n继续说说"
    [reply] = misskey_server.calls["notes/create"]
    assert reply["replyId"] == "reply-1"
    assert reply["text"] == DEFAULT_AI_REPLY


@pytest.mark.parametrize("inline", [True, False])
async def test_mention_in_reply_to_third_party_includes_parent(
    inline: bool,
    make_bot: MakeBot,
    write_config: WriteConfig,
    openai_server: FakeOpenAIServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = await make_bot(write_config())
    parent = {
        "id": "parent-1",
        "text": "第三方原帖",
        "user": {"id": "user-9", "username": "carol"},
    }
    get_note = AsyncMock(side_effect=lambda note_id: parent | {"id": note_id})
    monkeypatch.setattr(bot.misskey, "get_note", get_note)
    note = {
        **MENTION_NOTE,
        "text": "@testbot 翻译一下",
        "replyId": "parent-1",
        **({"reply": parent} if inline else {}),
    }

    await bot.mention.handle(mention(note))

    prompt = openai_server.calls[0]["messages"][-1]["content"]
    assert prompt == "@testbot 翻译一下\n\nReplying to:\n第三方原帖"
    assert get_note.await_count == (1 if inline else 2)


async def test_mention_ignores_unavailable_parent(
    make_bot: MakeBot,
    write_config: WriteConfig,
    openai_server: FakeOpenAIServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = await make_bot(write_config())

    async def get_note(note_id: str) -> dict[str, Any]:
        if note_id == "parent-1":
            raise APIBadRequestError("NO_SUCH_NOTE: No such note.")
        return {"visibility": "public"}

    monkeypatch.setattr(bot.misskey, "get_note", get_note)

    await bot.mention.handle(mention({**MENTION_NOTE, "replyId": "parent-1"}))

    assert openai_server.calls[0]["messages"][-1]["content"] == "@testbot 你好"


@pytest.mark.parametrize(
    ("event", "result", "text", "file_ids"),
    [
        (mention(MENTION_NOTE), "self.handled('hi')", "@alice\nhi", None),
        (reply_to_bot(), "self.handled('hi')", "hi", None),
        (
            mention(MENTION_NOTE),
            "self.handled('hi', file_ids=['a'])",
            "@alice\nhi",
            ["a"],
        ),
        (
            mention(MENTION_NOTE),
            "self.handled('hi', file_ids=['a', 'b'])",
            "@alice\nhi",
            ["a", "b"],
        ),
    ],
    ids=["mention", "reply-to-bot-no-handle", "one-file", "many-files"],
)
async def test_plugin_can_take_over_mention(
    event: MentionEvent,
    result: str,
    text: str,
    file_ids: list[str] | None,
    make_bot: MakeBot,
    make_hook_plugin: MakeHookPlugin,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
    openai_server: FakeOpenAIServer,
) -> None:
    bot = await make_bot(
        write_config(), plugins_dir=make_hook_plugin("on_mention", result)
    )

    await bot.mention.handle(event)

    assert openai_server.calls == []
    [note] = misskey_server.calls["notes/create"]
    assert note["text"] == text
    assert note.get("fileIds") == file_ids


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
    bot = cast(Any, object.__new__(Neuro))
    bot.bot_username = "testbot"
    bot.misskey = SimpleNamespace(instance_url="https://example.com")

    assert bot.is_bot_mentioned(text) is expected
