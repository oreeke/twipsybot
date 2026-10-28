import asyncio
import re
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from typing import TYPE_CHECKING, Any, Literal

from loguru import logger
from pydantic import ValidationError

from ...plugin.base import ToolEntry, tool, tool_specs
from ...plugin.contracts import HandledResult
from ...plugin.manager import valid_handled
from ...shared.locks import KeyedAsyncLock, actor_key
from .limits import ResponseLimiter

if TYPE_CHECKING:
    from ...plugin.manager import PluginManager
    from .runtime import BotRuntime

_TOOL_TIMEOUT_SECONDS = 60.0
_MAX_TOOL_CHARS = 8000
_TOOL_COMMAND = re.compile(
    r"^(?P<head>(?:@\S+\s+)*)/(?P<name>\w+)(?:\s+(?P<rest>.*))?$", re.DOTALL
)


@dataclass(frozen=True, slots=True)
class Reply:
    text: str
    file_id: str | None = None


class Toolbox:
    def __init__(self, event: Any, entries: dict[str, ToolEntry]):
        self._event = event
        self._entries = entries
        self._tainted = False
        self.file_id: str | None = None
        self.followups: list[Callable[[], Awaitable[HandledResult]]] = []

    @property
    def specs(self) -> list[dict[str, Any]]:
        return [
            {
                "name": name,
                "description": entry.spec.description,
                "parameters": entry.spec.schema,
            }
            for name, entry in self._entries.items()
            if not (self._tainted and entry.spec.effect)
        ]

    def taint(self) -> None:
        self._tainted = True

    def command(self, text: str) -> tuple[str, str] | None:
        if not (match := _TOOL_COMMAND.match(text)):
            return None
        for name, entry in self._entries.items():
            if match["name"] in {name, entry.spec.name}:
                return name, f"{match['head']}{match['rest'] or ''}"
        return None

    async def run(self, name: str, arguments: str) -> str:
        if (entry := self._entries.get(name)) is None:
            return f"Error: unknown tool {name}"
        if self._tainted and entry.spec.effect:
            return "Error: unavailable because untrusted content was read this turn"
        try:
            params = entry.spec.params.model_validate_json(arguments or "{}")
        except ValidationError as e:
            return "Error: invalid arguments: " + "; ".join(
                f"{'.'.join(map(str, err['loc']))} {err['msg']}" for err in e.errors()
            )
        try:
            async with asyncio.timeout(_TOOL_TIMEOUT_SECONDS):
                result = await entry.call(deepcopy(self._event), dict(params))
        except TimeoutError:
            logger.warning(f"Tool timeout: source={entry.label} tool={entry.spec.name}")
            return "Error: tool timed out"
        except Exception as e:
            logger.exception(
                f"Tool failed: source={entry.label} tool={entry.spec.name}: {e}"
            )
            return "Error: tool execution failed"
        self._tainted = self._tainted or entry.spec.external
        return self._finish(entry, result)

    def _finish(self, entry: ToolEntry, result: Any) -> str:
        if isinstance(result, dict) and valid_handled(result):
            self.file_id = self.file_id or result.get("file_id")
            if followup := result.get("followup"):
                self.followups.append(followup)
            result = result["response"]
        if not isinstance(result, str):
            logger.warning(
                f"Ignoring invalid tool result: source={entry.label} "
                f"tool={entry.spec.name}"
            )
            return "Error: tool returned an invalid result"
        return result[:_MAX_TOOL_CHARS]


@dataclass(frozen=True, slots=True)
class Augment:
    tools: Toolbox
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


Source = Literal["admin", "limit", "plugin", "ai"]
Deliver = Callable[[Reply, Source], Awaitable[None]]


def replied(source: Source) -> str:
    return {"admin": "Admin replied", "plugin": "Plugin replied"}.get(source, "Replied")


class ClockTools:
    @tool("Get the current local date, time and timezone of the bot host.")
    async def now(self, event: Any) -> str:
        return f"{datetime.now().astimezone():%Y-%m-%d %a %H:%M %z}"


class ResponsePipeline:
    def __init__(
        self,
        *,
        limits: ResponseLimiter,
        plugins: "PluginManager",
        runtime: "BotRuntime",
        is_admin: Callable[[Any], bool],
        is_follower: Callable[[str | None], Awaitable[bool]],
    ):
        self._limits = limits
        self._plugins = plugins
        self._runtime = runtime
        self._is_admin = is_admin
        self._is_follower = is_follower
        self._actor_locks = KeyedAsyncLock()
        self._host_tools: list[ToolEntry] = []

    def add_tools(self, label: str, owner: object) -> None:
        self._host_tools += [
            ToolEntry(label, spec, partial(self._call_host_tool, owner, spec.name))
            for spec in tool_specs(owner)
        ]

    @staticmethod
    async def _call_host_tool(
        owner: object, name: str, event: Any, arguments: dict[str, Any]
    ) -> Any:
        return await getattr(owner, name)(event, **arguments)

    async def toolbox(self, event: Any) -> Toolbox:
        candidates = [*self._host_tools, *self._plugins.plugin_tools()]
        levels = {"all"}
        if self._is_admin(event.user):
            levels |= {"followers", "admin"}
        elif any(e.spec.access == "followers" for e in candidates) and (
            await self._is_follower(event.user.id)
        ):
            levels.add("followers")
        entries = {
            f"{entry.label}_{entry.spec.name}": entry
            for entry in candidates
            if entry.spec.access in levels
        }
        return Toolbox(event, entries)

    def hold(
        self, actor_id: str | None, actor_name: str | None
    ) -> AbstractAsyncContextManager[None]:
        return self._actor_locks.hold(actor_key(actor_id, actor_name))

    async def _augment(self, event: Any) -> Augment:
        results = await self._plugins.call_plugin_hook("on_context", event)
        contexts = [r["context"] for _, r in results if "context" in r]
        text = next((r["text"] for _, r in results if "text" in r), None)
        tools = await self.toolbox(event)
        if text is None and (command := tools.command(event.text)):
            name, text = command
            contexts.append(f"The user requested the tool {name}; call it directly.")
        return Augment(
            tools,
            "\n\n".join(contexts),
            text,
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
        followups: list[Callable[[], Awaitable[Any]]] = []
        if results := await self._plugins.call_plugin_hook(hook, event):
            name, result = results[0]
            logger.debug(f"{hook} handled by plugin: {name}")
            text, source = result["response"], "plugin"
            file_id = result.get("file_id")
            followups = [f] if (f := result.get("followup")) else []
        else:
            augment = await self._augment(event)
            text, source = await generate(augment), "ai"
            file_id, followups = augment.tools.file_id, augment.tools.followups
        if not text:
            return
        await deliver(Reply(text, file_id), source)
        for followup in followups:
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
