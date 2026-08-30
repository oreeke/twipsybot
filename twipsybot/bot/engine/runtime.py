import asyncio
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import Any

__all__ = ("BotRuntime",)


class BotRuntime:
    def __init__(self):
        self.startup_time = datetime.now(UTC)
        self.running = False
        self.tasks: dict[str, asyncio.Task[Any]] = {}

    def add_task(self, name: str, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        if name in self.tasks and not self.tasks[name].done():
            self.tasks[name].cancel()
        task = asyncio.create_task(coro)
        self.tasks[name] = task
        return task

    async def cleanup_tasks(self) -> None:
        for task in self.tasks.values():
            if not task.done():
                task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        self.tasks.clear()
