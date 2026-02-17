from typing import TYPE_CHECKING, Any

from ..flows.chat import ChatHandler
from ..flows.mention import MentionHandler
from ..flows.notification import NotificationHandler
from ..flows.post import AutoPostService

if TYPE_CHECKING:
    from .core import MisskeyBot


class BotHandlers:
    def __init__(self, bot: "MisskeyBot"):
        self.bot = bot
        self.mention = MentionHandler(bot)
        self.chat = ChatHandler(bot)
        self.notification = NotificationHandler(bot)
        self.auto_post = AutoPostService(bot)

    async def on_mention(self, note: dict[str, Any]) -> None:
        await self.mention.handle(note)

    async def on_message(self, message: dict[str, Any]) -> None:
        await self.chat.handle(message)

    async def on_notification(self, notification: dict[str, Any]) -> None:
        await self.notification.handle(notification)

    async def on_timeline_note(self, note: dict[str, Any]) -> None:
        await self.bot.plugin_manager.call_plugin_hook("on_timeline_note", note)

    async def on_auto_post(self) -> None:
        await self.auto_post.run()
