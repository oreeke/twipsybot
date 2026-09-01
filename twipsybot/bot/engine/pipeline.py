import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from loguru import logger

from ...shared.locks import KeyedAsyncLock, actor_key
from .limits import ResponseLimiter

__all__ = ("AIResponse", "CommandResult", "ResponsePipeline")


@dataclass(frozen=True, slots=True)
class AIResponse:
    text: str
    file_id: str | None = None


@dataclass(frozen=True, slots=True)
class CommandResult:
    response: AIResponse | None = None
    execute: Callable[[], Awaitable[AIResponse]] | None = None


class ResponsePipeline:
    def __init__(self, *, limits: ResponseLimiter):
        self._limits = limits
        self._actor_locks = KeyedAsyncLock()

    async def run_admin_message(
        self,
        *,
        actor_id: str | None,
        actor_name: str | None,
        message_call: Callable[[], Awaitable[str | None]],
        send_reply: Callable[[str, str | None], Awaitable[None]],
        log_sent: Callable[[str], None],
    ) -> bool:
        async with self._actor_locks.hold(actor_key(actor_id, actor_name)):
            response = await message_call()
            if not response:
                return False
            await send_reply(response, None)
            log_sent(response)
            return True

    async def run_admin_command(
        self,
        *,
        actor_id: str | None,
        actor_name: str | None,
        command_call: Callable[[], CommandResult | None],
        send_reply: Callable[[str, str | None], Awaitable[None]],
        log_sent: Callable[[str], None],
    ) -> bool:
        async with self._actor_locks.hold(actor_key(actor_id, actor_name)):
            result = command_call()
            if result is None:
                return False
            response = await result.execute() if result.execute else result.response
            if response is not None:
                await send_reply(response.text, response.file_id)
                log_sent(response.text)
            return True

    async def apply_handled_plugin_result(
        self,
        result: Any,
        *,
        kind: str,
        user_id: str | None,
        send_reply: Callable[[str, str | None], Awaitable[None]],
        log_sent: Callable[[str], None],
        after_sent: Callable[[str], Any] | None = None,
    ) -> bool:
        if not (isinstance(result, dict) and result.get("handled")):
            return False
        logger.debug(f"{kind} handled by plugin: {result.get('plugin_name')}")
        response = result.get("response")
        if not response:
            return True
        await send_reply(response, None)
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
        send_reply: Callable[[str, str | None], Awaitable[None]],
        plugin_call: Callable[[], Awaitable[list[Any]]],
        plugin_kind: str,
        plugin_log_sent: Callable[[str], None],
        plugin_after_sent: Callable[[str], Any] | None = None,
        ai_generate: Callable[[], Awaitable[str | AIResponse | None]],
        ai_log_sent: Callable[[str], None],
        ai_after_sent: Callable[[str], Any] | None = None,
    ) -> None:
        async with self._actor_locks.hold(actor_key(actor_id, actor_name)):
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
            response = AIResponse(reply) if isinstance(reply, str) else reply
            await send_reply(response.text, response.file_id)
            ai_log_sent(response.text)
            if user_id:
                await self._limits.record_response(user_id, count_turn=True)
            if ai_after_sent is not None:
                maybe = ai_after_sent(response.text)
                if inspect.isawaitable(maybe):
                    await maybe
