from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from collections.abc import Coroutine
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from .bot import MisskeyBot

__all__ = ("BotRuntime",)


class BotRuntime:
    def __init__(
        self,
        bot: MisskeyBot,
        loop: asyncio.AbstractEventLoop | None = None,
    ):
        self.bot = bot
        if loop is not None:
            self.loop = loop
        else:
            try:
                self.loop = asyncio.get_running_loop()
            except RuntimeError:
                self.loop = asyncio.get_event_loop()
        self.startup_time = datetime.now(timezone.utc)
        self.running = False
        self.tasks: dict[str, asyncio.Task[Any]] = {}
        self.posts_today = 0
        self.last_auto_post_time = self.startup_time

    def add_task(self, name: str, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        if name in self.tasks and not self.tasks[name].done():
            self.tasks[name].cancel()
        task = self.loop.create_task(coro)
        self.tasks[name] = task
        return task

    def cancel_task(self, name: str) -> bool:
        if name in self.tasks and not self.tasks[name].done():
            self.tasks[name].cancel()
            return True
        return False

    def cancel_all_tasks(self) -> None:
        for task in self.tasks.values():
            if not task.done():
                task.cancel()
        self.tasks.clear()

    async def cleanup_tasks(self) -> None:
        for task in self.tasks.values():
            if not task.done():
                task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        self.tasks.clear()

    def post_count(self) -> None:
        self.posts_today += 1
        self.last_auto_post_time = datetime.now(timezone.utc)

    def check_post_counter(self, max_posts: int) -> bool:
        if self.posts_today >= max_posts:
            logger.debug(f"Daily post limit reached ({max_posts}); skipping auto-post")
            return False
        return True

    def reset_daily_counters(self) -> None:
        self.posts_today = 0
        logger.debug("Post counter reset")
