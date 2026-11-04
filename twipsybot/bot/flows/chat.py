import time
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

from cachetools import TTLCache
from loguru import logger

from ...clients.misskey.models import MessageEvent, parse_message
from ...shared.logs import format_log_text
from ..engine.pipeline import Augment, Deliver, Reply, Source, replied, trim_history

if TYPE_CHECKING:
    from ..engine.core import Neuro

_CACHE_MAX_USERS = 1000
_CACHE_TTL = 3600


def _ends_with(history: list[dict[str, str]], role: str, content: str) -> bool:
    return bool(history) and history[-1] == {"role": role, "content": content}


@dataclass(slots=True)
class _ChatContext:
    text: str
    user_id: str
    username: str
    handle: str | None
    room_id: str | None
    conversation_id: str
    actor_id: str
    room_label: str | None

    @property
    def user_content(self) -> str:
        return f"{self.username}: {self.text}" if self.room_id else self.text


class ChatHandler:
    def __init__(self, bot: "Neuro"):
        self.bot = bot
        self._histories: TTLCache[str, list[dict[str, str]]] = TTLCache(
            maxsize=_CACHE_MAX_USERS,
            ttl=_CACHE_TTL,
            timer=time.monotonic,
        )

    async def _history(
        self,
        conversation_id: str,
        *,
        limit: int,
        user_id: str | None = None,
        room_id: str | None = None,
    ) -> list[dict[str, str]]:
        if (cached := self._histories.get(conversation_id)) is not None:
            return self._trim_history(list(cached), limit)
        history = await self._fetch_history(
            user_id=user_id, room_id=room_id, limit=limit
        )
        trimmed = self._trim_history(history, limit)
        self._histories[conversation_id] = trimmed
        return list(trimmed)

    @staticmethod
    def _trim_history(
        history: list[dict[str, str]], limit_value: int
    ) -> list[dict[str, str]]:
        return history[-limit_value:] if limit_value > 0 else []

    def _append_turn(
        self,
        conversation_id: str,
        user_text: str,
        assistant_text: str,
        limit: int,
    ) -> None:
        history = list(self._histories.get(conversation_id) or [])
        if user_text and not _ends_with(history, "user", user_text):
            history.append({"role": "user", "content": user_text})
        if assistant_text and not _ends_with(history, "assistant", assistant_text):
            history.append({"role": "assistant", "content": assistant_text})
        self._histories[conversation_id] = self._trim_history(history, limit)

    async def handle(self, message: MessageEvent) -> None:
        if not self.bot.config.settings.reply.chat and not message.text.startswith("^"):
            return
        if message.user.id == self.bot.me.id:
            return
        if self.bot.limits.is_ignored_bot(message.user):
            logger.debug(f"Ignoring chat from bot account @{message.user.handle}")
            return
        await self._process(message)

    @staticmethod
    def _log_incoming_chat(ctx: _ChatContext) -> None:
        prefix = f"Room {ctx.room_label} " if ctx.room_label else ""
        body = format_log_text(ctx.text) if ctx.text else "(no text; has media)"
        logger.info(f"Chat received from {prefix}@{ctx.username}: {body}")

    async def _process(self, message: MessageEvent) -> None:
        if not (ctx := self._parse_chat_context(message)):
            return
        self._log_incoming_chat(ctx)
        limit = self.bot.config.settings.reply.memory
        deliver = partial(self._deliver, ctx, limit)
        async with self.bot.pipeline.hold(ctx.actor_id, ctx.username):
            if await self._run_command(message, ctx, deliver):
                return
            if self.bot.limits.is_response_blacklisted_user(
                user_id=ctx.user_id, handle=ctx.handle
            ):
                return
            await self.bot.pipeline.respond(
                user_id=ctx.user_id,
                handle=ctx.handle,
                hook="on_message",
                event=message,
                generate=partial(self._generate_ai_reply, ctx, limit),
                deliver=deliver,
            )

    async def _run_command(
        self, message: MessageEvent, ctx: _ChatContext, deliver: Deliver
    ) -> bool:
        if ctx.text.startswith("^"):
            if not ctx.room_id and (text := await self.bot.admin.on_message(message)):
                await deliver(Reply(text), "admin")
                logger.debug("Chat handled by Admin")
            return True
        return False

    async def _deliver(
        self, ctx: _ChatContext, limit: int, reply: Reply, source: Source
    ) -> None:
        text = self._format_chat_reply_text(
            room_id=ctx.room_id, handle=ctx.handle, text=reply.text
        )
        send = (
            partial(self.bot.misskey.send_room_message, ctx.room_id)
            if ctx.room_id
            else partial(self.bot.misskey.send_message, ctx.user_id)
        )
        first, *rest = reply.file_ids or (None,)
        await send(text, first)
        for file_id in rest:
            await send("", file_id)
        logger.info(f"{replied(source)} to @{ctx.username}: {format_log_text(text)}")
        if source in ("plugin", "ai") and ctx.text:
            self._append_turn(ctx.conversation_id, ctx.user_content, reply.text, limit)

    def _parse_chat_context(self, message: MessageEvent) -> _ChatContext | None:
        user = message.user
        if not user.id:
            logger.debug("Chat missing required info: user_id is empty")
            return None
        if not message.text and not message.files:
            logger.debug("Chat missing required info: empty text and no media")
            return None
        if message.room_id and not self.bot.me.mentioned_in(message.text):
            logger.debug(
                f"Room chat from @{user.username} does not mention the bot; skipping"
            )
            return None
        return _ChatContext(
            text=message.text,
            user_id=user.id,
            username=user.username,
            handle=user.known_handle,
            room_id=message.room_id,
            conversation_id=f"room:{message.room_id}" if message.room_id else user.id,
            actor_id=message.room_id or user.id,
            room_label=message.room_name or message.room_id,
        )

    @staticmethod
    def _format_chat_reply_text(
        *, room_id: str | None, handle: str | None, text: str
    ) -> str:
        if room_id and handle:
            mention = handle if handle.startswith("@") else f"@{handle}"
            stripped = text.lstrip()
            if not stripped.startswith(mention):
                return f"{mention}\n{text}"
        return text

    @staticmethod
    def _has_other_speakers(ctx: _ChatContext, history: list[dict[str, str]]) -> bool:
        own = f"{ctx.username}: "
        return any(
            m["role"] == "user" and not m["content"].startswith(own) for m in history
        )

    async def _generate_ai_reply(
        self, ctx: _ChatContext, limit: int, augment: Augment
    ) -> str | None:
        if not ctx.text and not augment.media:
            return None
        user_content = ctx.user_content
        token_budget = self.bot.config.settings.reply.ctx_tokens
        history = (
            await self._history(
                ctx.conversation_id,
                limit=limit,
                user_id=ctx.user_id,
                room_id=ctx.room_id,
            )
            if limit > 0 and token_budget > 0
            else []
        )
        if _ends_with(history, "user", user_content):
            history.pop()
        history = trim_history(history, token_budget)
        if ctx.room_id and self._has_other_speakers(ctx, history):
            augment.tools.taint()
        return await self.bot.generate_reply(
            history,
            augment.content(augment.apply(user_content, ctx.text)),
            augment.tools,
        )

    async def _fetch_history(
        self,
        *,
        user_id: str | None = None,
        room_id: str | None = None,
        limit: int,
    ) -> list[dict[str, str]]:
        try:
            if room_id:
                return await self._get_room_chat_history(room_id, limit)
            if user_id:
                return await self._get_user_chat_history(user_id, limit)
            return []
        except Exception:
            logger.exception("Error getting chat history")
            return []

    async def _get_room_chat_history(
        self, room_id: str, limit: int
    ) -> list[dict[str, str]]:
        messages = await self.bot.misskey.get_room_messages(room_id, limit=limit)
        history: list[dict[str, str]] = []
        for raw in reversed(messages):
            if not (msg := parse_message(raw)):
                continue
            if msg.user.id == self.bot.me.id:
                history.append({"role": "assistant", "content": msg.text})
            else:
                history.append(
                    {"role": "user", "content": f"{msg.user.username}: {msg.text}"}
                )
        return history

    async def _get_user_chat_history(
        self, user_id: str, limit: int
    ) -> list[dict[str, str]]:
        messages = await self.bot.misskey.get_messages(user_id, limit=limit)
        history: list[dict[str, str]] = []
        for raw in reversed(messages):
            if msg := parse_message(raw):
                role = "user" if msg.user.id == user_id else "assistant"
                history.append({"role": role, "content": msg.text})
        return history
