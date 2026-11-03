import asyncio
from typing import Any

from loguru import logger

from ...shared.config import Settings, needs_restart
from ...shared.errors import ConfigurationError
from ...shared.logs import set_log_level
from ...shared.settings import patch_settings

_POLL_SECONDS = 2.0
_POST_SCHEDULE_KEYS = frozenset(
    {"autopost.mode", "autopost.interval", "autopost.times"}
)
_AI_KEYS = frozenset({"bot.model", "bot.api_mode"})


def _changed_keys(old: dict[str, Any], new: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for section in old.keys() | new.keys():
        a, b = old.get(section), new.get(section)
        if isinstance(a, dict) and isinstance(b, dict):
            keys.update(
                f"{section}.{k}" for k in a.keys() | b.keys() if a.get(k) != b.get(k)
            )
        elif a != b:
            keys.add(section)
    return keys


class SettingsReloader:
    def __init__(self, bot: Any):
        self._bot = bot
        self._config = bot.config
        self._lock = asyncio.Lock()

    async def watch(self) -> None:
        while True:
            await asyncio.sleep(_POLL_SECONDS)
            if self._config.stat() != self._config.fingerprint:
                try:
                    await self.reload()
                except Exception:
                    logger.exception("Settings reload crashed")

    async def update(self, changes: dict[str, Any]) -> dict[str, str]:
        async with self._lock:
            await asyncio.to_thread(
                patch_settings, self._config.settings_path, changes, Settings
            )
            return await self._reload()

    async def reload(self) -> dict[str, str]:
        async with self._lock:
            return await self._reload()

    async def _reload(self) -> dict[str, str]:
        old = self._config.snapshot()
        try:
            await asyncio.to_thread(self._config.load)
        except ConfigurationError as e:
            self._config.fingerprint = self._config.stat()
            logger.error(f"Settings rejected; keeping current: {e}")
            return {}
        if not (changed := _changed_keys(old, self._config.snapshot())):
            return {}
        await self._apply(changed)
        statuses = {
            name: await self._bot.plugin_manager.reload_plugin(name)
            for name in sorted(
                k.split(".", 1)[1] for k in changed if k.startswith("plugins.")
            )
        }
        logger.info(f"Settings applied: {', '.join(sorted(changed))}")
        if restart := sorted(k for k in changed if needs_restart(k)):
            logger.warning(f"Restart required for: {', '.join(restart)}")
        return statuses

    async def _apply(self, changed: set[str]) -> None:
        bot, settings = self._bot, self._config.settings
        if changed & _AI_KEYS:
            bot.openai.update(model=settings.bot.model, api_mode=settings.bot.api_mode)
        if "system.log_level" in changed:
            set_log_level(settings.system.log_level)
        if "system.dump_events" in changed:
            bot.streaming.log_dump_events = settings.system.dump_events
        if "system.allow_nets" in changed:
            for fetcher in (bot.fetcher, bot.misskey.fetcher):
                fetcher.set_nets(settings.system.allow_nets)
        if changed & _POST_SCHEDULE_KEYS:
            bot.auto_post.apply_schedule()
        if any(k.startswith("timeline.") for k in changed):
            try:
                await bot.connect.sync_timelines()
            except Exception as e:
                logger.warning(f"Timeline sync failed: {e}")
