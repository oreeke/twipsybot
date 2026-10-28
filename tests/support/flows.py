from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

from support.events import mention
from twipsybot.clients.misskey.models import MentionEvent

MENTION_NOTE = {
    "id": "note-mention-1",
    "text": "@testbot 你好",
    "user": {"id": "user-1", "username": "alice"},
    "mentions": ["bot-id"],
}
CHAT_MESSAGE = {
    "id": "msg-1",
    "text": "你好，机器人",
    "user": {"id": "user-2", "username": "bob"},
}
PNG = b"\x89PNG\r\n\x1a\nimage"
IMAGE_OPTIONS = {
    "model": "gpt-image-1",
    "size": None,
    "quality": None,
    "base_url": None,
    "api_key": None,
}


def mock_image_backend(bot: Any, file_id: str) -> None:
    bot.openai.generate_image = AsyncMock(return_value=PNG)
    bot.misskey.drive.upload_bytes = AsyncMock(return_value={"id": file_id})


def tool_names(call: dict[str, Any]) -> list[str]:
    return [t["function"]["name"] for t in call.get("tools", [])]


def offers_effect_tools(call: dict[str, Any]) -> bool:
    return "note_publish" in tool_names(call)


async def settle(bot: Any) -> None:
    await asyncio.gather(*bot.runtime._background)


def thread_note(note_id: str, user_id: str, username: str, text: str) -> dict[str, Any]:
    return {
        "id": note_id,
        "text": text,
        "user": {"id": user_id, "username": username},
    }


def thread_chain() -> list[dict[str, Any]]:
    return [
        thread_note("b1", "bot-id", "testbot", "bot reply"),
        thread_note("c1", "user-3", "carol", "noise"),
        thread_note("u1", "user-1", "alice", "@testbot 问题"),
        thread_note("p1", "user-2", "bob", "主帖"),
    ]


def reply_event() -> MentionEvent:
    note = {
        **thread_note("u2", "user-1", "alice", "继续"),
        "replyId": "b1",
        "reply": thread_note("b1", "bot-id", "testbot", "bot reply"),
    }
    return mention(note, "reply")
