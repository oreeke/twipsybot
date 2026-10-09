import asyncio
from typing import Any

from loguru import logger

from ...shared.config import Settings, needs_restart
from ...shared.config_keys import ConfigKeys
from ...shared.exceptions import ConfigurationError
from ...shared.logs import set_log_level
from ...shared.settings import patch_settings

__all__ = ("SettingsReloader",)

_POLL_SECONDS = 2.0
_POST_SCHEDULE_KEYS = frozenset(
    {
        ConfigKeys.POST_MODE,
        ConfigKeys.POST_INTERVAL,
        ConfigKeys.POST_TIMES,
    }
)
_AI_KEYS = frozenset(
    {
        ConfigKeys.BOT_MODEL,
        ConfigKeys.BOT_API_MODE,
        ConfigKeys.BOT_IMAGE_MODEL,
        ConfigKeys.BOT_IMAGE_SIZE,
        ConfigKeys.BOT_IMAGE_QUALITY,
    }
)


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
        try:
            old = await asyncio.to_thread(self._config.load)
        except ConfigurationError as e:
            self._config.fingerprint = self._config.stat()
            logger.error(f"Settings rejected; keeping current: {e}")
            return {}
        if not (changed := _changed_keys(old, self._config.data)):
            return {}
        self._apply(changed)
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

    def _apply(self, changed: set[str]) -> None:
        bot, get = self._bot, self._config.get
        if changed & _AI_KEYS:
            bot.openai.update(
                model=get(ConfigKeys.BOT_MODEL),
                api_mode=get(ConfigKeys.BOT_API_MODE),
                image_model=get(ConfigKeys.BOT_IMAGE_MODEL),
                image_size=get(ConfigKeys.BOT_IMAGE_SIZE),
                image_quality=get(ConfigKeys.BOT_IMAGE_QUALITY),
            )
        if ConfigKeys.SYSTEM_LOG_LEVEL in changed:
            set_log_level(get(ConfigKeys.SYSTEM_LOG_LEVEL))
        if ConfigKeys.SYSTEM_DUMP_EVENTS in changed:
            bot.streaming.log_dump_events = bool(get(ConfigKeys.SYSTEM_DUMP_EVENTS))
        if changed & _POST_SCHEDULE_KEYS:
            bot.auto_post.apply_schedule()
