from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, call

import pytest
from support.types import MakeBot, WriteConfig

from twipsybot import Neuro
from twipsybot.bot.admin import handlers
from twipsybot.shared.errors import (
    APIBadRequestError,
    APIConnectionError,
    APINotFoundError,
    APIPermissionError,
    APIRateLimitError,
)

type RunCommand = Callable[[Neuro, str], Awaitable[str]]

OLD = "2025-01-02T00:00:00Z"


def cleanable_note(note_id: str, created_at: str = OLD) -> dict[str, Any]:
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


def stub_notes(
    bot: Neuro,
    monkeypatch: pytest.MonkeyPatch,
    notes: list[dict[str, Any]],
    *,
    rechecked: list[dict[str, Any]] | None = None,
    delete_effects: list[Any] | None = None,
) -> AsyncMock:
    misskey = bot.misskey
    delete_note = AsyncMock(side_effect=delete_effects, return_value={})
    monkeypatch.setattr(
        misskey, "get_current_user", AsyncMock(return_value={"pinnedNotes": []})
    )
    monkeypatch.setattr(misskey, "get_user_notes", AsyncMock(return_value=notes))
    monkeypatch.setattr(misskey, "get_note", AsyncMock(side_effect=rechecked or notes))
    monkeypatch.setattr(misskey, "delete_note", delete_note)
    monkeypatch.setattr("twipsybot.bot.admin.handlers.asyncio.sleep", AsyncMock())
    return delete_note


async def test_clean_posts_preview_summarizes_candidates_without_deleting(
    admin_bot: Neuro, run_command: RunCommand, monkeypatch: pytest.MonkeyPatch
) -> None:
    notes = [
        cleanable_note("new-note", "2026-01-02T00:00:00Z"),
        cleanable_note("old-note"),
    ]
    delete_note = stub_notes(admin_bot, monkeypatch, notes)

    response = await run_command(admin_bot, "^clean posts 30")

    assert "发现 2 条超过 30 天未被互动的帖子" in response
    assert "最旧：2025-01-02 · old-note" in response
    assert "最新：2026-01-02 · new-note" in response
    assert "^clean posts 30 -y" in response
    delete_note.assert_not_awaited()


async def test_clean_posts_rechecks_and_deletes_oldest_first(
    admin_bot: Neuro, run_command: RunCommand, monkeypatch: pytest.MonkeyPatch
) -> None:
    new_note = cleanable_note("new-note", "2026-01-02T00:00:00Z")
    old_note = cleanable_note("old-note")
    delete_note = stub_notes(
        admin_bot,
        monkeypatch,
        [new_note, old_note],
        rechecked=[old_note, new_note],
    )

    response = await run_command(admin_bot, "^clean posts 30 -y")

    assert "已删除 2 条超过 30 天未被互动的帖子 · 跳过 0 条" in response
    assert delete_note.await_args_list == [call("old-note"), call("new-note")]


@pytest.mark.parametrize(
    ("count", "interacted", "delete_effects", "deletes", "expected"),
    [
        (1, True, None, 0, ["已删除 0 条", "跳过 1 条"]),
        (301, False, None, 300, ["已删除 300 条", "未处理 1 条"]),
        (
            3,
            False,
            [{}, APIRateLimitError("rate limited")],
            2,
            ["已删除 1 条", "跳过 0 条", "未处理 2 条"],
        ),
        (
            3,
            False,
            [{}, APIConnectionError("disconnected")],
            2,
            ["已删除 1 条", "跳过 0 条", "未处理 2 条"],
        ),
        (
            2,
            False,
            [APIBadRequestError("missing"), {}],
            2,
            ["已删除 1 条", "跳过 1 条", "未处理 0 条"],
        ),
    ],
    ids=[
        "interacted-since-preview",
        "batch-limit",
        "rate-limit",
        "connection",
        "deleted-meanwhile",
    ],
)
async def test_clean_posts_reports_skipped_and_unprocessed_notes(
    count: int,
    interacted: bool,
    delete_effects: list[Any] | None,
    deletes: int,
    expected: list[str],
    admin_bot: Neuro,
    run_command: RunCommand,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notes = [cleanable_note(f"note-{index}") for index in range(count)]
    rechecked = (
        [{**notes[0], "reactionCount": 1, "reactions": {"👍": 1}}]
        if interacted
        else None
    )
    delete_note = stub_notes(
        admin_bot,
        monkeypatch,
        notes,
        rechecked=rechecked,
        delete_effects=delete_effects,
    )

    response = await run_command(admin_bot, "^clean posts 30 -y")

    assert all(text in response for text in expected)
    assert delete_note.await_count == deletes


async def test_clean_posts_only_skips_missing_resources(
    make_bot: MakeBot, write_config: WriteConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = await make_bot(write_config())
    note = cleanable_note("note-1")
    cutoff = datetime.now(UTC)
    monkeypatch.setattr(
        bot.misskey, "get_note", AsyncMock(side_effect=APINotFoundError("missing"))
    )

    assert await handlers.delete_clean_posts(bot, [note], cutoff, set()) == (0, 1, 0)

    monkeypatch.setattr(
        bot.misskey, "get_note", AsyncMock(side_effect=APIPermissionError("denied"))
    )
    with pytest.raises(APIPermissionError, match="denied"):
        await handlers.delete_clean_posts(bot, [note], cutoff, set())


@pytest.mark.parametrize("confirmation", ["yes", "-Y", "--yes"])
async def test_clean_posts_only_accepts_exact_y_confirmation(
    confirmation: str,
    admin_bot: Neuro,
    run_command: RunCommand,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_user_notes = AsyncMock()
    monkeypatch.setattr(admin_bot.misskey, "get_user_notes", get_user_notes)

    response = await run_command(admin_bot, f"^clean posts 30 {confirmation}")

    assert "用法: ^clean posts <天数> [-y]" in response
    get_user_notes.assert_not_awaited()
