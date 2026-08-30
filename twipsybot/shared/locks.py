import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field


@dataclass(slots=True)
class _Entry:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


def actor_key(user_id: str | None, username: str | None) -> str | None:
    if user_id:
        return f"id:{user_id}"
    if username:
        return f"name:{username}"
    return None


class KeyedAsyncLock:
    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}

    @asynccontextmanager
    async def hold(self, key: str | None) -> AsyncIterator[None]:
        if key is None:
            yield
            return
        entry = self._entries.setdefault(key, _Entry())
        entry.users += 1
        try:
            async with entry.lock:
                yield
        finally:
            entry.users -= 1
            if entry.users == 0 and self._entries.get(key) is entry:
                self._entries.pop(key)
