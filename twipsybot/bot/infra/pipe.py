import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from cachetools import TTLCache
from loguru import logger

from ...shared.constants import USER_LOCK_CACHE_MAX, USER_LOCK_TTL
from .limits import ResponseLimiter

__all__ = ("ResponsePipeline",)


class ResponsePipeline:
    def __init__(self, *, limits: ResponseLimiter):
        self._limits = limits
        self._user_locks: TTLCache[str, asyncio.Lock] = TTLCache(
            maxsize=USER_LOCK_CACHE_MAX, ttl=USER_LOCK_TTL
        )

    @staticmethod
    def _actor_key(user_id: str | None, username: str | None) -> str | None:
        if user_id:
            return f"id:{user_id}"
        if username:
            return f"name:{username}"
        return None

    def _get_actor_lock(
        self, user_id: str | None, username: str | None
    ) -> asyncio.Lock:
        key = self._actor_key(user_id, username)
        if not key:
            return asyncio.Lock()
        if key not in self._user_locks:
            self._user_locks[key] = asyncio.Lock()
        return self._user_locks[key]

    def lock_actor(self, user_id: str | None, username: str | None):
        return self._get_actor_lock(user_id, username)

    async def apply_handled_plugin_result(
        self,
        result: Any,
        *,
        kind: str,
        user_id: str | None,
        send_reply: Callable[[str], Awaitable[None]],
        log_sent: Callable[[str], None],
        after_sent: Callable[[str], Any] | None = None,
    ) -> bool:
        if not (isinstance(result, dict) and result.get("handled")):
            return False
        logger.debug(f"{kind} handled by plugin: {result.get('plugin_name')}")
        response = result.get("response")
        if not response:
            return True
        await send_reply(response)
        log_sent(response)
        if user_id:
            await self._limits.record_response(user_id, count_turn=True)
        if after_sent is not None:
            maybe = after_sent(response)
            if inspect.isawaitable(maybe):
                await maybe
        return True

    async def run_response_pipeline(
        self,
        *,
        actor_id: str | None,
        actor_name: str | None,
        user_id: str | None,
        handle: str | None,
        log_incoming: Callable[[], None],
        send_reply: Callable[[str], Awaitable[None]],
        plugin_call: Callable[[], Awaitable[list[Any]]],
        plugin_kind: str,
        plugin_log_sent: Callable[[str], None],
        plugin_after_sent: Callable[[str], Any] | None = None,
        ai_generate: Callable[[], Awaitable[str | None]],
        ai_log_sent: Callable[[str], None],
        ai_after_sent: Callable[[str], Any] | None = None,
    ) -> None:
        async with self.lock_actor(actor_id, actor_name):
            log_incoming()
            if user_id and await self._limits.maybe_send_blocked_reply(
                user_id=user_id, handle=handle, send_reply=send_reply
            ):
                return
            plugin_results = await plugin_call()
            for result in plugin_results:
                if await self.apply_handled_plugin_result(
                    result,
                    kind=plugin_kind,
                    user_id=user_id,
                    send_reply=send_reply,
                    log_sent=plugin_log_sent,
                    after_sent=plugin_after_sent,
                ):
                    return
            reply = await ai_generate()
            if not reply:
                return
            await send_reply(reply)
            ai_log_sent(reply)
            if user_id:
                await self._limits.record_response(user_id, count_turn=True)
            if ai_after_sent is not None:
                maybe = ai_after_sent(reply)
                if inspect.isawaitable(maybe):
                    await maybe
