from __future__ import annotations

from support.types import MakeBot, WriteConfig

from twipsybot.shared.keys import ConfigKeys
from twipsybot.shared.settings import read_settings


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
    await bot.db._execute_write(
        "UPDATE response_limit_state SET updated_at = '2000-01-01 00:00:00'"
    )
    assert await bot.db.cleanup_response_limit_state() == 0
    assert await bot.db.get_response_limit_state("old-user") is not None
    assert await bot.db.cleanup_response_limit_state(max_age_days=30) == 1
    assert await bot.db.get_response_limit_state("old-user") is None


async def test_database_manager_can_reinitialize_after_close(
    make_bot: MakeBot, write_config: WriteConfig
) -> None:
    bot = await make_bot(write_config())
    await bot.db.close()

    await bot.db.initialize()
    await bot.db.set_plugin_data("test", "key", "value")

    assert await bot.db.get_plugin_data("test", "key") == "value"


async def test_legacy_permanent_turn_block_migrates_to_blacklist(
    make_bot: MakeBot, write_config: WriteConfig
) -> None:
    bot = await make_bot(write_config())
    await bot.db.set_response_limit_state(
        user_id="legacy-user",
        last_reply_ts=1.0,
        turns=3,
        blocked_until_ts=-1,
    )

    blocked = await bot.limits.get_response_block_reply(
        user_id="legacy-user", handle=None
    )

    assert blocked == (False, "")
    assert bot.config.get(ConfigKeys.REPLY_BLACKLIST) == ["legacy-user"]
    assert read_settings(bot.config.settings_path)["reply"]["blacklist"] == [
        "legacy-user"
    ]
    assert await bot.db.get_response_limit_state("legacy-user") == (1.0, 0, None)
