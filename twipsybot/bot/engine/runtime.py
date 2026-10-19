import asyncio
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import Any


class BotRuntime:
    def __init__(self):
        self.startup_time = datetime.now(UTC)
        self.running = False
        self.tasks: dict[str, asyncio.Task[Any]] = {}
        self._background: set[asyncio.Task[Any]] = set()

    def add_task(self, name: str, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        if name in self.tasks and not self.tasks[name].done():
            self.tasks[name].cancel()
        task = asyncio.create_task(coro)
        self.tasks[name] = task
        return task

    def spawn(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return task

    async def cleanup_tasks(self) -> None:
        tasks = [*self.tasks.values(), *self._background]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()
        self._background.clear()
