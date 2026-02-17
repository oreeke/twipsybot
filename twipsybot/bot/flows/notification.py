import asyncio
from typing import TYPE_CHECKING, Any

from loguru import logger

from ...shared.config_keys import ConfigKeys
from ...shared.utils import maybe_log_event_dump

if TYPE_CHECKING:
    from ..infra.core import MisskeyBot


class NotificationHandler:
    def __init__(self, bot: "MisskeyBot"):
        self.bot = bot

    async def handle(self, notification: dict[str, Any]) -> None:
        maybe_log_event_dump(
            bool(self.bot.config.get(ConfigKeys.LOG_DUMP_EVENTS)),
            kind="Notification",
            payload=notification,
        )
        try:
            await self.bot.plugin_manager.call_plugin_hook(
                "on_notification", notification
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Error handling notification event")
