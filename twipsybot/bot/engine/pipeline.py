from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from loguru import logger

from ...shared.locks import KeyedAsyncLock, actor_key
from .limits import ResponseLimiter

if TYPE_CHECKING:
    from ...plugin.manager import PluginManager

__all__ = ("Deliver", "Reply", "ResponsePipeline", "Source", "replied")


@dataclass(frozen=True, slots=True)
class Reply:
    text: str
    file_id: str | None = None


Source = Literal["admin", "command", "limit", "plugin", "ai"]
Deliver = Callable[[Reply, Source], Awaitable[None]]


def replied(source: Source) -> str:
    return {"admin": "Admin replied", "plugin": "Plugin replied"}.get(source, "Replied")


class ResponsePipeline:
    def __init__(self, *, limits: ResponseLimiter, plugins: "PluginManager"):
        self._limits = limits
        self._plugins = plugins
        self._actor_locks = KeyedAsyncLock()

    def hold(
        self, actor_id: str | None, actor_name: str | None
    ) -> AbstractAsyncContextManager[None]:
        return self._actor_locks.hold(actor_key(actor_id, actor_name))

    async def respond(
        self,
        *,
        user_id: str | None,
        handle: str | None,
        hook: str,
        event: Any,
        generate: Callable[[], Awaitable[str | None]],
        deliver: Deliver,
    ) -> None:
        if user_id:
            blocked, notice = await self._limits.get_response_block_reply(
                user_id=user_id, handle=handle
            )
            if blocked:
                if notice:
                    await deliver(Reply(notice), "limit")
                    await self._limits.record_response(user_id, count_turn=False)
                return
        source: Source
        if results := await self._plugins.call_plugin_hook(hook, event):
            name, result = results[0]
            logger.debug(f"{hook} handled by plugin: {name}")
            text, source = result["response"], "plugin"
        else:
            text, source = await generate(), "ai"
        if not text:
            return
        await deliver(Reply(text), source)
        if user_id:
            await self._limits.record_response(user_id, count_turn=True)
