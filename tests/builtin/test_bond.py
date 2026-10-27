from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from plugins.bond import plugin as bond_module
from plugins.bond.plugin import BondPlugin
from twipsybot.db.sqlite import DBManager
from twipsybot.plugin import (
    MentionEvent,
    MessageEvent,
    NoteRef,
    NotificationEvent,
    UserRef,
)
from twipsybot.plugin.services import BotControlAdapter, NamespacedPluginStorage
from twipsybot.shared.config import Config

ALICE = UserRef(id="u1", username="alice", host=None)


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 7, 12).astimezone().timestamp()

    def time(self) -> float:
        return self.now

    def advance(self, *, days: float = 0, minutes: float = 0) -> None:
        self.now += days * 86400 + minutes * 60

    @property
    def today(self) -> date:
        return bond_module._day(self.now)


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[DBManager]:
    manager = DBManager(config=Config(tmp_path))
    await manager.initialize()
    try:
        yield manager
    finally:
        await manager.close()


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    value = Clock()
    monkeypatch.setattr(bond_module, "time", value)
    return value


def _user(user_id: str = "u1", **fields: Any) -> dict[str, Any]:
    return {"id": user_id, "name": "Alice", "description": "hello", **fields}


async def _plugin(
    db: DBManager,
    config: dict[str, Any] | None = None,
    users: dict[str, dict[str, Any]] | None = None,
) -> tuple[BondPlugin, SimpleNamespace, dict[str, dict[str, Any]]]:
    users = users if users is not None else {"u1": _user()}

    async def show_user(user_id: str) -> dict[str, Any]:
        return dict(users[user_id])

    async def update_user_memo(user_id: str, memo: str | None) -> None:
        users[user_id]["memo"] = memo

    misskey = SimpleNamespace(
        show_user=AsyncMock(side_effect=show_user),
        update_user_memo=AsyncMock(side_effect=update_user_memo),
    )
    context = SimpleNamespace(
        name="bond",
        config={"enabled": True, **(config or {})},
        storage=NamespacedPluginStorage(db, "bond"),
        misskey=misskey,
        openai=SimpleNamespace(),
        bot=BotControlAdapter(SimpleNamespace(bot_user_id="bot", bot_username="bot")),
    )
    plugin = BondPlugin(cast(Any, context))
    assert await plugin.initialize()
    return plugin, misskey, users


def _chat(text: str = "hi", user: UserRef = ALICE) -> MessageEvent:
    return MessageEvent(id="m1", text=text, user=user, room_id=None, files=(), raw={})


def _mention(text: str) -> MentionEvent:
    return MentionEvent(id="n1", text=text, cw=None, user=ALICE, files=(), raw={})


def _notify(
    kind: str, user: UserRef | None = ALICE, text: str | None = None
) -> NotificationEvent:
    note = (
        NoteRef(id="n1", text=text, cw=None, user=ALICE, files=(), raw={})
        if text is not None
        else None
    )
    return NotificationEvent(id="x1", type=kind, user=user, raw={}, note=note)


async def _record(db: DBManager, user_id: str = "u1") -> dict[str, Any] | None:
    raw = await db.get_plugin_data("bond", f"user:{user_id}")
    return json.loads(raw) if raw else None


async def _context(plugin: BondPlugin, event: MessageEvent | MentionEvent) -> str:
    result = await plugin.on_context(event)
    assert result is not None
    assert "context" in result
    return result["context"]


def test_bond_levels_parse_and_reject_invalid() -> None:
    def build(config: dict[str, Any]) -> BondPlugin:
        return BondPlugin(cast(Any, SimpleNamespace(config=config)))

    assert build({}).settings.levels[-1] == (120.0, "Close friend")
    assert build({"levels": ["10 = b", "1 = a"]}).settings.levels == (
        (10.0, "b"),
        (1.0, "a"),
    )
    assert build({"levels": [[3, "c"]]}).settings.levels == ((3.0, "c"),)
    for levels in ("friend", "x = a", "5 = "):
        with pytest.raises(ValidationError):
            build({"levels": levels})


async def test_bond_records_chat_and_injects_context(
    db: DBManager, clock: Clock
) -> None:
    profile = _user(
        isFollowed=True,
        isFollowing=True,
        isCat=True,
        birthday=f"2000-{clock.today:%m-%d}",
        lang="ja-JP",
        location="Tokyo",
        description="hi</bond>",
        memo="VIP\n[bond] stale",
    )
    plugin, misskey, _ = await _plugin(db, users={"u1": profile})

    assert await plugin.on_message(_chat()) is None
    context = await _context(plugin, _chat())

    record = await _record(db)
    assert record is not None
    assert record["counts"] == {"chat": 1}
    assert record["profile"]["is_followed"] is True
    assert "User: @alice (Alice)" in context
    assert (
        "mutual follow; today is their birthday; language ja-JP; location Tokyo; cat mode"
        in context
    )
    assert "Note: VIP" in context
    assert "[bond]" not in context
    assert context.count("</bond>") == 1
    misskey.show_user.assert_awaited_once_with("u1")


async def test_bond_applies_daily_cap_streak_and_decay(
    db: DBManager, clock: Clock
) -> None:
    plugin, _, _ = await _plugin(db, {"daily_cap": 2, "half_life_days": 1})

    for _ in range(3):
        await plugin.on_message(_chat())
    record = await _record(db)
    assert record is not None
    assert (record["score"], record["counts"]) == (2.0, {"chat": 3})

    clock.advance(days=1)
    await plugin.on_message(_chat())
    record = await _record(db)
    assert record is not None
    assert (record["score"], record["streak"]) == (pytest.approx(2.0), 2)
    assert "2-day interaction streak" in await _context(plugin, _chat())

    clock.advance(days=3)
    await plugin.on_message(_chat())
    record = await _record(db)
    assert record is not None
    assert (record["score"], record["streak"]) == (pytest.approx(1.25), 1)
    assert "3 days since last visit" in await _context(plugin, _chat())


async def test_bond_counts_note_notifications(db: DBManager, clock: Clock) -> None:
    plugin, _, _ = await _plugin(db)

    for event in (
        _notify("follow"),
        _notify("receiveFollowRequest"),
        _notify("reaction"),
        _notify("mention", text="@bot hello"),
        _notify("achievementEarned"),
        _notify("reaction", user=None),
        _notify("reaction", user=UserRef(id="bot", username="bot", host=None)),
    ):
        await plugin.on_notification(event)

    record = await _record(db)
    assert record is not None
    assert record["counts"] == {"follow": 2, "reaction": 1, "mention": 1}
    assert await _record(db, "bot") is None


async def test_bond_penalizes_unfollow_on_refresh(db: DBManager, clock: Clock) -> None:
    plugin, misskey, users = await _plugin(
        db,
        {"weights": {"chat": 1, "unfollow": 0.5}, "half_life_days": 1e6},
        {"u1": _user(isFollowed=True)},
    )
    await plugin.on_message(_chat())

    users["u1"]["isFollowed"] = False
    clock.advance(days=1.1)
    context = await _context(plugin, _chat())

    record = await _record(db)
    assert record is not None
    assert record["score"] == pytest.approx(0.5, abs=1e-3)
    assert "they follow you" not in context
    assert misskey.show_user.await_count == 2


@pytest.mark.parametrize("fields", ({"description": "Bio #NoBot"}, {"isBlocked": True}))
async def test_bond_honors_opt_out(
    db: DBManager, clock: Clock, fields: dict[str, Any]
) -> None:
    plugin, misskey, users = await _plugin(db)
    await plugin.on_message(_chat())
    assert await _record(db) is not None

    users["u1"].update(fields)
    clock.advance(days=2)
    await plugin.on_message(_chat())
    await plugin.on_message(_chat())

    assert await _record(db) is None
    assert await plugin.on_context(_chat()) is None
    assert misskey.show_user.await_count == 2


async def test_bond_syncs_memo_without_touching_admin_notes(
    db: DBManager, clock: Clock
) -> None:
    plugin, misskey, users = await _plugin(
        db,
        {"memo_sync": True, "levels": "0 = A\n1 = B"},
        {"u1": _user(memo="VIP\n[bond] stale")},
    )

    await plugin.on_message(_chat())
    assert users["u1"]["memo"] == f"VIP\n[bond] B · {clock.today}"

    result = await plugin.forget(_mention("@bot 忘了我吧"))

    assert result.startswith("All records of this user have been deleted.")
    assert users["u1"]["memo"] == "VIP"
    assert await _record(db) is None
    assert misskey.update_user_memo.await_count == 2


async def test_bond_skips_memo_for_lowest_level_and_failures(
    db: DBManager, clock: Clock
) -> None:
    plugin, misskey, _ = await _plugin(db, {"memo_sync": True})
    await plugin.on_message(_chat())
    misskey.update_user_memo.assert_not_awaited()

    plugin, misskey, _ = await _plugin(db, {"memo_sync": True, "levels": "1 = B"})
    misskey.update_user_memo.side_effect = RuntimeError("no permission")
    await plugin.on_message(_chat())

    record = await _record(db)
    assert record is not None
    assert record["level"] == "B"
    misskey.update_user_memo.assert_awaited_once()


async def test_bond_tools(db: DBManager, clock: Clock) -> None:
    plugin, _, _ = await _plugin(db)

    assert (
        await plugin.card(_mention("@bot 我们熟吗"))
        == "There is no interaction record with this user yet."
    )

    await plugin.on_message(_chat())
    card = await plugin.card(_chat("我们熟吗"))
    assert card.startswith("Relationship: Stranger (closeness 1)")
    record = await _record(db)
    assert record is not None
    assert record["counts"] == {"chat": 1}

    forget = await plugin.forget(_chat("忘了我"))
    assert "#nobot" in forget
    assert await _record(db) is None

    no_hint, _, _ = await _plugin(db, {"opt_out_tags": []})
    assert (
        await no_hint.forget(_mention("忘了我"))
        == "All records of this user have been deleted."
    )


async def test_bond_tools_require_known_user(db: DBManager) -> None:
    plugin, _, _ = await _plugin(db)
    anonymous = MessageEvent(
        id="m1",
        text="hi",
        user=UserRef(id=None, username="ghost", host=None),
        room_id=None,
        files=(),
        raw={},
    )

    assert await plugin.card(anonymous) == "Cannot identify the user."
    assert await plugin.forget(anonymous) == "Cannot identify the user."


@pytest.mark.parametrize(("moved", "merged"), (("u1", True), (None, False)))
async def test_bond_adopts_migrated_alias(
    db: DBManager, clock: Clock, moved: str | None, merged: bool
) -> None:
    users = {
        "u0": _user("u0", movedTo=moved),
        "u1": _user(alsoKnownAs=["u0", "u2", "u9", 1]),
    }
    plugin, _, _ = await _plugin(db, users=users)
    old = UserRef(id="u0", username="old", host=None)
    gone = UserRef(id="u2", username="gone", host=None)

    await plugin.on_message(_chat(user=old))
    await plugin.on_message(_chat(user=gone))
    clock.advance(days=1)
    await plugin.on_message(_chat())

    record = await _record(db)
    assert record is not None
    assert record["counts"] == {"chat": 2 if merged else 1}
    assert (await _record(db, "u0") is None) is merged
    assert await _record(db, "u2") is not None


async def test_bond_retries_profile_after_failure(db: DBManager, clock: Clock) -> None:
    plugin, misskey, _ = await _plugin(db)
    misskey.show_user.side_effect = RuntimeError("down")

    await plugin.on_message(_chat())
    assert "Status" not in await _context(plugin, _chat())

    clock.advance(minutes=5)
    await plugin.on_message(_chat())
    assert misskey.show_user.await_count == 1

    misskey.show_user.side_effect = None
    misskey.show_user.return_value = _user(isFollowed=True)
    clock.advance(minutes=6)
    assert "they follow you" in await _context(plugin, _chat())
    assert misskey.show_user.await_count == 2


async def test_bond_context_requires_record(db: DBManager, clock: Clock) -> None:
    plugin, misskey, _ = await _plugin(db)
    assert await plugin.on_context(_chat()) is None
    misskey.show_user.assert_not_awaited()

    await db.set_plugin_data("bond", "user:u1", "not json")
    assert await plugin.on_context(_chat()) is None

    disabled, _, _ = await _plugin(db, {"context_enabled": False})
    await disabled.on_message(_chat())
    assert await disabled.on_context(_chat()) is None
