from typing import TYPE_CHECKING, Any

from loguru import logger

from ...shared.config_keys import ConfigKeys
from ...shared.logs import maybe_log_event_dump

if TYPE_CHECKING:
    from ..engine.core import Neuro


class NotificationHandler:
    def __init__(self, bot: "Neuro"):
        self.bot = bot

    async def handle(self, notification: dict[str, Any]) -> None:
        maybe_log_event_dump(
            bool(self.bot.config.get(ConfigKeys.SYSTEM_DUMP_EVENTS)),
            kind="Notification",
            payload=notification,
        )
        try:
            await self.bot.plugin_manager.call_plugin_hook(
                "on_notification", notification
            )
        except Exception:
            logger.exception("Error handling notification event")
