import time
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any

from cachetools import TTLCache
from loguru import logger

from ...clients.misskey.payloads import (
    extract_chat_text,
    extract_user_handle,
    extract_user_id,
    extract_username,
)
from ...shared.config_keys import ConfigKeys
from ...shared.constants import CHAT_CACHE_MAX_USERS, CHAT_CACHE_TTL
from ...shared.utils import format_log_text, maybe_log_event_dump
from ..engine.pipeline import Deliver, Reply, Source, replied

if TYPE_CHECKING:
    from ..engine.core import Neuro


@dataclass(slots=True)
class _ChatContext:
    text: str
    user_id: str
    username: str
    handle: str | None
    mention_to: str | None
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
            maxsize=CHAT_CACHE_MAX_USERS,
            ttl=CHAT_CACHE_TTL,
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

    async def handle(self, message: dict[str, Any]) -> None:
        chat_enabled = self.bot.config.get(ConfigKeys.REPLY_CHAT)
        admin_command = extract_chat_text(message).startswith("^")
        if not chat_enabled and not admin_command:
            return
        if not message.get("id"):
            logger.debug("Missing id; skipping")
            return
        if self.bot.bot_user_id and extract_user_id(message) == self.bot.bot_user_id:
            return
        maybe_log_event_dump(
            bool(self.bot.config.get(ConfigKeys.SYSTEM_DUMP_EVENTS)),
            kind="Chat",
            payload=message,
        )
        try:
            await self._process(message)
        except Exception as e:
            logger.error(f"Error handling chat: {e}")

    @staticmethod
    def _parse_room(message: dict[str, Any]) -> tuple[str | None, str | None]:
        to_room = message.get("toRoom")
        room_id = message.get("toRoomId")
        room_name = None
        if isinstance(to_room, dict):
            if not room_id:
                room_id = to_room.get("id")
            room_name = to_room.get("name")
        room_id = room_id if isinstance(room_id, str) and room_id else None
        room_name = room_name if isinstance(room_name, str) and room_name else None
        return room_id, room_name

    @staticmethod
    def _log_incoming_chat(ctx: _ChatContext) -> None:
        prefix = f"Room {ctx.room_label} " if ctx.room_label else ""
        body = format_log_text(ctx.text) if ctx.text else "(no text; has media)"
        logger.info(f"Chat received from {prefix}@{ctx.username}: {body}")

    async def _process(self, message: dict[str, Any]) -> None:
        if not (ctx := self._parse_chat_context(message)):
            return
        self._log_incoming_chat(ctx)
        limit: int = self.bot.config.get(ConfigKeys.REPLY_MEMORY)
        deliver = partial(self._deliver, ctx, limit)
        async with self.bot.pipeline.hold(ctx.actor_id, ctx.username):
            if await self._run_command(message, ctx, deliver):
                return
            handle = ctx.handle or ctx.mention_to
            if self.bot.limits.is_response_blacklisted_user(
                user_id=ctx.user_id, handle=handle
            ):
                return
            await self.bot.pipeline.respond(
                user_id=ctx.user_id,
                handle=handle,
                hook="on_message",
                event=message,
                generate=partial(self._generate_ai_reply, ctx, limit),
                deliver=deliver,
            )

    async def _run_command(
        self, message: dict[str, Any], ctx: _ChatContext, deliver: Deliver
    ) -> bool:
        if ctx.text.startswith("^"):
            if not ctx.room_id and (text := await self.bot.admin.on_message(message)):
                await deliver(Reply(text), "admin")
                logger.debug("Chat handled by Admin")
            return True
        reply = await self.bot.admin.handle_slash_command(
            ctx.text,
            user_id=ctx.user_id,
            username=ctx.username,
            handle=ctx.handle or ctx.mention_to,
            private=ctx.room_id is None,
        )
        if reply is None:
            return False
        if reply.text:
            await deliver(reply, "command")
        return True

    async def _deliver(
        self, ctx: _ChatContext, limit: int, reply: Reply, source: Source
    ) -> None:
        text = self._format_chat_reply_text(
            room_id=ctx.room_id, mention_to=ctx.mention_to, text=reply.text
        )
        if ctx.room_id:
            await self.bot.misskey.send_room_message(ctx.room_id, text, reply.file_id)
        else:
            await self.bot.misskey.send_message(ctx.user_id, text, reply.file_id)
        logger.info(f"{replied(source)} to @{ctx.username}: {format_log_text(text)}")
        if source in ("plugin", "ai") and ctx.text:
            self.append_turn(ctx.conversation_id, ctx.user_content, reply.text, limit)

    def _parse_chat_context(self, message: dict[str, Any]) -> _ChatContext | None:
        text = extract_chat_text(message)
        user_id = extract_user_id(message)
        if not isinstance(user_id, str) or not user_id:
            logger.debug("Chat missing required info: user_id is empty")
            return None
        username = extract_username(message)
        handle = extract_user_handle(message)
        mention_to = handle or (username if username != "unknown" else None)
        room_id, room_name = self._parse_room(message)
        if not text and not (message.get("fileId") or message.get("file")):
            logger.debug("Chat missing required info: empty text and no media")
            return None
        if room_id and not self.bot.is_bot_mentioned(text):
            logger.debug(
                f"Room chat from @{username} does not mention the bot; skipping"
            )
            return None
        conversation_id = f"room:{room_id}" if room_id else user_id
        actor_id = room_id or user_id
        room_label = room_name or room_id
        return _ChatContext(
            text=str(text),
            user_id=user_id,
            username=username,
            handle=handle,
            mention_to=mention_to,
            room_id=room_id,
            conversation_id=conversation_id,
            actor_id=actor_id,
            room_label=room_label,
        )

    @staticmethod
    def _format_chat_reply_text(
        *, room_id: str | None, mention_to: str | None, text: str
    ) -> str:
        if room_id and mention_to:
            mention = mention_to if mention_to.startswith("@") else f"@{mention_to}"
            stripped = text.lstrip()
            if not stripped.startswith(mention):
                return f"{mention}\n{text}"
        return text

    async def _generate_ai_reply(self, ctx: _ChatContext, limit: int) -> str | None:
        if not ctx.text:
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
        messages: list[dict[str, str]] = []
        if self.bot.system_prompt:
            messages.append({"role": "system", "content": self.bot.system_prompt})
        messages.extend(history)
        messages.append({"role": "user", "content": user_content})
        return await self.bot.openai.generate_chat(messages, **self.bot.ai_config)

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
        for msg in reversed(messages):
            sender_id = extract_user_id(msg)
            is_assistant = bool(
                bot_user_id and isinstance(sender_id, str) and sender_id == bot_user_id
            )
            content = extract_chat_text(msg)
            if not is_assistant:
                content = f"{extract_username(msg)}: {content}"
            history.append(
                {"role": "assistant" if is_assistant else "user", "content": content}
            )
        return history

    async def _get_user_chat_history(
        self, user_id: str, limit: int
    ) -> list[dict[str, str]]:
        messages = await self.bot.misskey.get_messages(user_id, limit=limit)
        history: list[dict[str, str]] = []
        for msg in reversed(messages):
            role = "user" if extract_user_id(msg) == user_id else "assistant"
            content = extract_chat_text(msg)
            history.append({"role": role, "content": content})
        return history
