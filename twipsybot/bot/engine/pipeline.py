from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from loguru import logger

from ...shared.locks import KeyedAsyncLock, actor_key
from .limits import ResponseLimiter

if TYPE_CHECKING:
    from ...plugin.manager import PluginManager
    from .runtime import BotRuntime


@dataclass(frozen=True, slots=True)
class Reply:
    text: str
    file_id: str | None = None


@dataclass(frozen=True, slots=True)
class Augment:
    context: str = ""
    text: str | None = None
    media: tuple[dict[str, Any], ...] = ()

    def rewrite(self, prompt: str, original: str) -> str:
        if self.text is None or not original:
            return prompt
        head, found, tail = prompt.rpartition(original)
        return f"{head}{self.text}{tail}" if found else prompt

    def wrap(self, prompt: str) -> str:
        return f"{self.context}\n\n{prompt}" if self.context else prompt

    def apply(self, prompt: str, original: str) -> str:
        return self.wrap(self.rewrite(prompt, original))

    def content(self, prompt: str) -> str | list[dict[str, Any]]:
        prompt = prompt.strip()
        if not self.media:
            return prompt
        text = [{"type": "input_text", "text": prompt}] if prompt else []
        return [*text, *self.media]


Source = Literal["admin", "command", "limit", "plugin", "ai"]
Deliver = Callable[[Reply, Source], Awaitable[None]]


def replied(source: Source) -> str:
    return {"admin": "Admin replied", "plugin": "Plugin replied"}.get(source, "Replied")


class ResponsePipeline:
    def __init__(
        self,
        *,
        limits: ResponseLimiter,
        plugins: "PluginManager",
        runtime: "BotRuntime",
    ):
        self._limits = limits
        self._plugins = plugins
        self._runtime = runtime
        self._actor_locks = KeyedAsyncLock()

    def hold(
        self, actor_id: str | None, actor_name: str | None
    ) -> AbstractAsyncContextManager[None]:
        return self._actor_locks.hold(actor_key(actor_id, actor_name))

    async def _augment(self, event: Any) -> Augment:
        results = await self._plugins.call_plugin_hook("on_context", event)
        return Augment(
            "\n\n".join(r["context"] for _, r in results if "context" in r),
            next((r["text"] for _, r in results if "text" in r), None),
            tuple(m for _, r in results for m in r.get("media", ())),
        )

    async def respond(
        self,
        *,
        user_id: str | None,
        handle: str | None,
        hook: str,
        event: Any,
        generate: Callable[[Augment], Awaitable[str | None]],
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
        file_id: str | None = None
        followup = None
        if results := await self._plugins.call_plugin_hook(hook, event):
            name, result = results[0]
            logger.debug(f"{hook} handled by plugin: {name}")
            text, source = result["response"], "plugin"
            file_id, followup = result.get("file_id"), result.get("followup")
        else:
            text, source = await generate(await self._augment(event)), "ai"
        if not text:
            return
        await deliver(Reply(text, file_id), source)
        if followup:
            self._runtime.spawn(self._deliver_followup(followup, deliver, source))
        if user_id:
            await self._limits.record_response(user_id, count_turn=True)

    @staticmethod
    async def _deliver_followup(
        followup: Callable[[], Awaitable[Any]], deliver: Deliver, source: Source
    ) -> None:
        try:
            result = await followup()
            await deliver(Reply(result["response"], result.get("file_id")), source)
        except Exception:
            logger.exception("Plugin followup failed")
