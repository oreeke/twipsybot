from __future__ import annotations

import sqlite3
from contextlib import closing

from support.types import MakeBot, WriteConfig


async def test_database_persists_updates_and_cleans_expired_state(
    make_bot: MakeBot,
    write_config: WriteConfig,
) -> None:
    bot = await make_bot(write_config())

    await bot.db.set_plugin_data("test", "key", "first")
    await bot.db.set_plugin_data("test", "key", "second")
    assert await bot.db.get_plugin_data("test", "key") == "second"

    await bot.db.set_response_limit_state(
        user_id="old-user",
        last_reply_ts=1.0,
        turns=2,
        blocked_until_ts=None,
    )
    with closing(sqlite3.connect(bot.db.db_path)) as conn:
        conn.execute(
            "UPDATE response_limit_state SET updated_at = '2000-01-01 00:00:00'"
        )
        conn.commit()
    await bot.db.maintain(-1)
    assert await bot.db.get_response_limit_state("old-user") is not None
    await bot.db.maintain(30)
    assert await bot.db.get_response_limit_state("old-user") is None


async def test_database_manager_can_reinitialize_after_close(
    make_bot: MakeBot, write_config: WriteConfig
) -> None:
    bot = await make_bot(write_config())
    await bot.db.close()

    await bot.db.initialize()
    await bot.db.set_plugin_data("test", "key", "value")

    assert await bot.db.get_plugin_data("test", "key") == "value"
