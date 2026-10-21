from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, call

import pytest
from support.events import message
from support.types import MakeBot, WriteConfig

from twipsybot.bot.admin import handlers
from twipsybot.shared.errors import (
    APIBadRequestError,
    APIConnectionError,
    APINotFoundError,
    APIPermissionError,
    APIRateLimitError,
)


def _cleanable_note(note_id: str, created_at: str) -> dict[str, Any]:
    return {
        "id": note_id,
        "createdAt": created_at,
        "replyId": None,
        "renoteId": None,
        "channelId": None,
        "mentions": [],
        "repliesCount": 0,
        "renoteCount": 0,
        "reactionCount": 0,
        "reactions": {},
        "clippedCount": 0,
        "poll": None,
    }


async def test_admin_clean_posts_preview_uses_requested_format(
    make_bot: MakeBot, write_config: WriteConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = await make_bot(write_config(bot={"admins": ["user-2"]}))
    notes = [
        _cleanable_note("new-note", "2026-01-02T00:00:00Z"),
        _cleanable_note("old-note", "2025-01-02T00:00:00Z"),
    ]
    monkeypatch.setattr(
        bot.misskey, "get_current_user", AsyncMock(return_value={"pinnedNotes": []})
    )
    monkeypatch.setattr(bot.misskey, "get_user_notes", AsyncMock(return_value=notes))

    response = await bot.admin.on_message(
        message(
            {
                "id": "message-1",
                "text": "^clean posts 30",
                "user": {"id": "user-2", "username": "bob"},
            }
        )
    )

    assert response is not None
    assert "发现 2 条超过 30 天未被互动的帖子" in response
    assert "最旧：2025-01-02 · old-note" in response
    assert "最新：2026-01-02 · new-note" in response
    assert "使用 ^clean posts 30 -y 确认删除" in response
    assert "危险：删除后无法恢复" in response


async def test_admin_clean_posts_rechecks_and_deletes_oldest_first(
    make_bot: MakeBot, write_config: WriteConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = await make_bot(write_config(bot={"admins": ["user-2"]}))
    new_note = _cleanable_note("new-note", "2026-01-02T00:00:00Z")
    old_note = _cleanable_note("old-note", "2025-01-02T00:00:00Z")
    delete_note = AsyncMock(return_value={})
    monkeypatch.setattr(
        bot.misskey, "get_current_user", AsyncMock(return_value={"pinnedNotes": []})
    )
    monkeypatch.setattr(
        bot.misskey, "get_user_notes", AsyncMock(return_value=[new_note, old_note])
    )
    monkeypatch.setattr(
        bot.misskey, "get_note", AsyncMock(side_effect=[old_note, new_note])
    )
    monkeypatch.setattr(bot.misskey, "delete_note", delete_note)
    monkeypatch.setattr("twipsybot.bot.admin.handlers.asyncio.sleep", AsyncMock())

    response = await bot.admin.on_message(
        message(
            {
                "id": "message-1",
                "text": "^clean posts 30 -y",
                "user": {"id": "user-2", "username": "bob"},
            }
        )
    )

    assert response is not None
    assert "已删除 2 条超过 30 天未被互动的帖子 · 跳过 0 条" in response
    assert delete_note.await_args_list == [call("old-note"), call("new-note")]


async def test_admin_clean_posts_skips_note_interacted_with_before_delete(
    make_bot: MakeBot, write_config: WriteConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = await make_bot(write_config(bot={"admins": ["user-2"]}))
    note = _cleanable_note("note-1", "2025-01-02T00:00:00Z")
    interacted = {**note, "reactionCount": 1, "reactions": {"👍": 1}}
    delete_note = AsyncMock(return_value={})
    monkeypatch.setattr(
        bot.misskey, "get_current_user", AsyncMock(return_value={"pinnedNotes": []})
    )
    monkeypatch.setattr(bot.misskey, "get_user_notes", AsyncMock(return_value=[note]))
    monkeypatch.setattr(bot.misskey, "get_note", AsyncMock(return_value=interacted))
    monkeypatch.setattr(bot.misskey, "delete_note", delete_note)

    response = await bot.admin.on_message(
        message(
            {
                "id": "message-1",
                "text": "^clean posts 30 -y",
                "user": {"id": "user-2", "username": "bob"},
            }
        )
    )

    assert response is not None
    assert "已删除 0 条超过 30 天未被互动的帖子 · 跳过 1 条" in response
    delete_note.assert_not_awaited()


async def test_admin_clean_posts_limits_each_batch_to_300(
    make_bot: MakeBot, write_config: WriteConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = await make_bot(write_config(bot={"admins": ["user-2"]}))
    notes = [
        _cleanable_note(f"note-{index}", "2025-01-02T00:00:00Z") for index in range(301)
    ]
    monkeypatch.setattr(
        bot.misskey, "get_current_user", AsyncMock(return_value={"pinnedNotes": []})
    )
    monkeypatch.setattr(bot.misskey, "get_user_notes", AsyncMock(return_value=notes))
    monkeypatch.setattr(bot.misskey, "get_note", AsyncMock(side_effect=notes))
    delete_note = AsyncMock(return_value={})
    monkeypatch.setattr(bot.misskey, "delete_note", delete_note)
    monkeypatch.setattr("twipsybot.bot.admin.handlers.asyncio.sleep", AsyncMock())

    response = await bot.admin.on_message(
        message(
            {
                "id": "message-1",
                "text": "^clean posts 30 -y",
                "user": {"id": "user-2", "username": "bob"},
            }
        )
    )

    assert response is not None
    assert "已删除 300 条" in response
    assert "未处理 1 条" in response
    assert delete_note.await_count == 300


@pytest.mark.parametrize(
    "error",
    (APIRateLimitError("rate limited"), APIConnectionError("disconnected")),
    ids=("rate-limit", "connection"),
)
async def test_admin_clean_posts_stops_and_reports_transient_error(
    make_bot: MakeBot,
    write_config: WriteConfig,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    bot = await make_bot(write_config(bot={"admins": ["user-2"]}))
    notes = [
        _cleanable_note(f"note-{index}", "2025-01-02T00:00:00Z") for index in range(3)
    ]
    monkeypatch.setattr(
        bot.misskey, "get_current_user", AsyncMock(return_value={"pinnedNotes": []})
    )
    monkeypatch.setattr(bot.misskey, "get_user_notes", AsyncMock(return_value=notes))
    monkeypatch.setattr(bot.misskey, "get_note", AsyncMock(side_effect=notes))
    monkeypatch.setattr(
        bot.misskey,
        "delete_note",
        AsyncMock(side_effect=[{}, error]),
    )
    monkeypatch.setattr("twipsybot.bot.admin.handlers.asyncio.sleep", AsyncMock())

    response = await bot.admin.on_message(
        message(
            {
                "id": "message-1",
                "text": "^clean posts 30 -y",
                "user": {"id": "user-2", "username": "bob"},
            }
        )
    )

    assert response is not None
    assert "已删除 1 条" in response
    assert "跳过 0 条" in response
    assert "未处理 2 条" in response


async def test_admin_clean_posts_skips_note_deleted_during_recheck(
    make_bot: MakeBot, write_config: WriteConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = await make_bot(write_config(bot={"admins": ["user-2"]}))
    notes = [
        _cleanable_note(f"note-{index}", "2025-01-02T00:00:00Z") for index in range(2)
    ]
    monkeypatch.setattr(
        bot.misskey, "get_current_user", AsyncMock(return_value={"pinnedNotes": []})
    )
    monkeypatch.setattr(bot.misskey, "get_user_notes", AsyncMock(return_value=notes))
    monkeypatch.setattr(bot.misskey, "get_note", AsyncMock(side_effect=notes))
    monkeypatch.setattr(
        bot.misskey,
        "delete_note",
        AsyncMock(side_effect=[APIBadRequestError("missing"), {}]),
    )
    monkeypatch.setattr("twipsybot.bot.admin.handlers.asyncio.sleep", AsyncMock())

    response = await bot.admin.on_message(
        message(
            {
                "id": "message-1",
                "text": "^clean posts 30 -y",
                "user": {"id": "user-2", "username": "bob"},
            }
        )
    )

    assert response is not None
    assert "已删除 1 条" in response
    assert "跳过 1 条" in response
    assert "未处理 0 条" in response


async def test_admin_clean_posts_only_skips_missing_resources(
    make_bot: MakeBot, write_config: WriteConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = await make_bot(write_config(bot={"admins": ["user-2"]}))
    note = _cleanable_note("note-1", "2025-01-02T00:00:00Z")
    cutoff = datetime.now(UTC)

    monkeypatch.setattr(
        bot.misskey, "get_note", AsyncMock(side_effect=APINotFoundError("missing"))
    )

    assert await handlers.delete_clean_posts(bot, [note], cutoff, set()) == (0, 1, 0)

    bot.misskey.get_note = AsyncMock(side_effect=APIPermissionError("denied"))
    with pytest.raises(APIPermissionError, match="denied"):
        await handlers.delete_clean_posts(bot, [note], cutoff, set())


@pytest.mark.parametrize("confirmation", ("yes", "-Y", "--yes"))
async def test_admin_clean_posts_only_accepts_exact_y_confirmation(
    make_bot: MakeBot,
    write_config: WriteConfig,
    monkeypatch: pytest.MonkeyPatch,
    confirmation: str,
) -> None:
    bot = await make_bot(write_config(bot={"admins": ["user-2"]}))
    get_user_notes = AsyncMock()
    monkeypatch.setattr(bot.misskey, "get_user_notes", get_user_notes)

    response = await bot.admin.on_message(
        message(
            {
                "id": "message-1",
                "text": f"^clean posts 30 {confirmation}",
                "user": {"id": "user-2", "username": "bob"},
            }
        )
    )

    assert response is not None
    assert "用法: ^clean posts <天数> [-y]" in response
    get_user_notes.assert_not_awaited()
