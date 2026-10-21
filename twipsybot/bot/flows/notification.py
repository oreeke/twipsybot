from typing import TYPE_CHECKING

from loguru import logger

from ...clients.misskey.models import NotificationEvent

if TYPE_CHECKING:
    from ..engine.core import Neuro


class NotificationHandler:
    def __init__(self, bot: "Neuro"):
        self.bot = bot

    async def handle(self, event: NotificationEvent) -> None:
        try:
            await self.bot.plugin_manager.call_plugin_hook("on_notification", event)
        except Exception:
            logger.exception("Error handling notification event")
