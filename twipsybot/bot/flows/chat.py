import time
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any

from cachetools import TTLCache
from loguru import logger

from ...clients.misskey.models import MessageEvent, parse_message
from ...shared.keys import ConfigKeys
from ...shared.logs import format_log_text
from ..engine.pipeline import Augment, Deliver, Reply, Source, replied

if TYPE_CHECKING:
    from ..engine.core import Neuro

_CACHE_MAX_USERS = 1000
_CACHE_TTL = 3600


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

    async def get_or_load_history(
        self,
        conversation_id: str,
        *,
        limit: int,
        user_id: str | None = None,
        room_id: str | None = None,
    ) -> list[dict[str, str]]:
        if (cached := self._histories.get(conversation_id)) is not None:
            return self._trim_history(list(cached), limit)
        history = await self.get_chat_history(
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

    def append_turn(
        self,
        conversation_id: str,
        user_text: str,
        assistant_text: str,
        limit: int,
    ) -> None:
        history = list(self._histories.get(conversation_id) or [])
        last = next(reversed(history), None)
        if user_text and not (
            isinstance(last, dict)
            and last.get("role") == "user"
            and last.get("content") == user_text
        ):
            history.append({"role": "user", "content": user_text})
        last = next(reversed(history), None)
        if assistant_text and not (
            isinstance(last, dict)
            and last.get("role") == "assistant"
            and last.get("content") == assistant_text
        ):
            history.append({"role": "assistant", "content": assistant_text})
        self._histories[conversation_id] = self._trim_history(history, limit)

    async def handle(self, message: MessageEvent) -> None:
        chat_enabled = self.bot.config.get(ConfigKeys.REPLY_CHAT)
        admin_command = message.text.startswith("^")
        if not chat_enabled and not admin_command:
            return
        if self.bot.bot_user_id and message.user.id == self.bot.bot_user_id:
            return
        if self.bot.limits.is_ignored_bot(message.user):
            logger.debug(f"Ignoring chat from bot account @{message.user.handle}")
            return
        try:
            await self._process(message)
        except Exception as e:
            logger.error(f"Error handling chat: {e}")

    @staticmethod
    def _log_incoming_chat(ctx: _ChatContext) -> None:
        prefix = f"Room {ctx.room_label} " if ctx.room_label else ""
        body = format_log_text(ctx.text) if ctx.text else "(no text; has media)"
        logger.info(f"Chat received from {prefix}@{ctx.username}: {body}")

    async def _process(self, message: MessageEvent) -> None:
        if not (ctx := self._parse_chat_context(message)):
            return
        self._log_incoming_chat(ctx)
        limit: int = self.bot.config.get(ConfigKeys.REPLY_MEMORY)
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
        if ctx.room_id:
            await self.bot.misskey.send_room_message(ctx.room_id, text, reply.file_id)
        else:
            await self.bot.misskey.send_message(ctx.user_id, text, reply.file_id)
        logger.info(f"{replied(source)} to @{ctx.username}: {format_log_text(text)}")
        if source in ("plugin", "ai") and ctx.text:
            self.append_turn(ctx.conversation_id, ctx.user_content, reply.text, limit)

    def _parse_chat_context(self, message: MessageEvent) -> _ChatContext | None:
        user = message.user
        if not user.id:
            logger.debug("Chat missing required info: user_id is empty")
            return None
        if not message.text and not message.files:
            logger.debug("Chat missing required info: empty text and no media")
            return None
        if message.room_id and not self.bot.is_bot_mentioned(message.text):
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
        token_budget = self.bot.config.get(ConfigKeys.REPLY_CTX_TOKENS)
        history = (
            await self.get_or_load_history(
                ctx.conversation_id,
                limit=limit,
                user_id=ctx.user_id,
                room_id=ctx.room_id,
            )
            if limit > 0 and isinstance(token_budget, int) and token_budget > 0
            else []
        )
        last = next(reversed(history), None)
        if (
            isinstance(last, dict)
            and last.get("role") == "user"
            and last.get("content") == user_content
        ):
            history.pop()
        history = self.bot.openai.trim_chat_history(history, token_budget)
        if ctx.room_id and augment.tools and self._has_other_speakers(ctx, history):
            augment.tools.taint()
        messages: list[dict[str, Any]] = []
        if self.bot.system_prompt:
            messages.append({"role": "system", "content": self.bot.system_prompt})
        messages.extend(history)
        messages.append(
            {
                "role": "user",
                "content": augment.content(augment.apply(user_content, ctx.text)),
            }
        )
        return await self.bot.openai.generate_chat(
            messages, **self.bot.ai_config, tools=augment.tools
        )

    async def get_chat_history(
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
        bot_user_id = self.bot.bot_user_id
        history: list[dict[str, str]] = []
        for raw in reversed(messages):
            if not (msg := parse_message(raw)):
                continue
            is_assistant = bool(bot_user_id and msg.user.id == bot_user_id)
            content = msg.text if is_assistant else f"{msg.user.username}: {msg.text}"
            history.append(
                {"role": "assistant" if is_assistant else "user", "content": content}
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
