from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from loguru import logger

from ...clients.misskey.models import MentionEvent, NoteRef, parse_note
from ...shared.keys import ConfigKeys
from ...shared.logs import format_log_text
from ..engine.pipeline import Augment, Reply, Source, Toolbox, replied

if TYPE_CHECKING:
    from ..engine.core import Neuro


@dataclass(slots=True)
class MentionContext:
    text: str
    user_id: str | None
    username: str | None
    explicit_mention: bool
    reply_visibility: str | None


class MentionHandler:
    def __init__(self, bot: "Neuro"):
        self.bot = bot

    def _is_self_mention(self, event: MentionEvent) -> bool:
        return bool(self.bot.bot_user_id and event.user.id == self.bot.bot_user_id)

    @staticmethod
    def _format_mention_reply(mention: MentionContext, text: str) -> str:
        if mention.explicit_mention and mention.username:
            return f"@{mention.username}\n{text}"
        return text

    def _is_reply_to_bot(self, event: MentionEvent) -> bool:
        return bool(
            self.bot.bot_user_id
            and event.reply
            and event.reply.user.id == self.bot.bot_user_id
        )

    def _should_handle(self, event: MentionEvent, reply_to_bot: bool) -> bool:
        if event.kind == "reply":
            return reply_to_bot
        if reply_to_bot:
            return False
        return bool(self.bot.bot_user_id and self.bot.bot_user_id in event.mentions)

    async def _related_note(
        self, inline: NoteRef | None, note_id: str | None, label: str
    ) -> NoteRef | None:
        if inline:
            return inline
        if not note_id:
            return None
        try:
            related = await self.bot.misskey.get_note(note_id)
        except Exception as e:
            logger.debug(f"Failed to fetch {label} note: {note_id} - {e}")
            return None
        return parse_note(related)

    def _taint_foreign(
        self, notes: Iterable[NoteRef], user_id: str | None, tools: Toolbox | None
    ) -> None:
        trusted = {self.bot.bot_user_id, user_id}
        if tools and any(n.body and n.user.id not in trusted for n in notes):
            tools.taint()

    async def _build_mention_prompt(
        self,
        text: str,
        event: MentionEvent,
        tools: Toolbox | None,
        *,
        include_parent: bool = True,
    ) -> str:
        parts = [text.strip()]
        related: list[NoteRef] = []
        if quoted := await self._related_note(event.renote, event.renote_id, "quoted"):
            related.append(quoted)
            parts.append(f"Quote:\n{quoted.body}")
        if (
            include_parent
            and not self._is_reply_to_bot(event)
            and (
                parent := await self._related_note(
                    event.reply, event.reply_id, "parent"
                )
            )
        ):
            related.append(parent)
            parts.append(f"Replying to:\n{parent.body}")
        self._taint_foreign(related, event.user.id, tools)
        return "\n\n".join(p for p in parts if p)

    def _thread_message(
        self, note: NoteRef, user_id: str | None
    ) -> dict[str, str] | None:
        text = note.body
        if not text or note.hidden:
            return None
        author_id = note.user.id
        if author_id is not None and author_id == self.bot.bot_user_id:
            return {"role": "assistant", "content": text}
        if author_id != user_id:
            text = f"@{note.user.handle}: {text}"
        return {"role": "user", "content": text}

    def _thread_notes(self, chain: list[NoteRef], user_id: str | None) -> list[NoteRef]:
        parent, *rest = chain
        if not rest:
            return [parent]
        *middle, root = rest
        participants = {self.bot.bot_user_id, user_id} - {None}
        return [
            root,
            *(n for n in reversed(middle) if n.user.id in participants),
            parent,
        ]

    async def _thread_history(
        self, event: MentionEvent, user_id: str | None, tools: Toolbox | None
    ) -> list[dict[str, str]]:
        limit = self.bot.config.get(ConfigKeys.REPLY_MEMORY)
        budget = self.bot.config.get(ConfigKeys.REPLY_CTX_TOKENS)
        if limit <= 0 or budget <= 0 or not event.reply_id:
            return []
        try:
            chain = await self.bot.misskey.get_note_conversation(event.id, limit=limit)
        except Exception as e:
            logger.debug(f"Failed to fetch conversation: {event.id} - {e}")
            return []
        if not (notes := [n for raw in chain if (n := parse_note(raw))]):
            return []
        selected = [
            (n, m)
            for n in self._thread_notes(notes, user_id)
            if (m := self._thread_message(n, user_id))
        ]
        self._taint_foreign((n for n, _ in selected), user_id, tools)
        return self.bot.openai.trim_chat_history([m for _, m in selected], budget)

    async def handle(self, event: MentionEvent) -> None:
        if not self.bot.config.get(ConfigKeys.REPLY_MENTION):
            return
        if self._is_self_mention(event):
            return
        if self.bot.limits.is_ignored_bot(event.user):
            logger.debug(f"Ignoring mention from bot account @{event.user.handle}")
            return
        if not (mention := self._parse(event)):
            return
        if mention.user_id and self.bot.limits.is_response_blacklisted_user(
            user_id=mention.user_id, handle=mention.username
        ):
            return
        try:
            display = mention.username or "unknown"
            try:
                await self.bot.misskey.get_note(event.id)
            except Exception as e:
                logger.warning(
                    f"Skipping mention because source note is unavailable: "
                    f"{event.id} - {e}"
                )
                return

            async def deliver(reply: Reply, source: Source) -> None:
                text = self._format_mention_reply(mention, reply.text)
                await self.bot.misskey.create_note(
                    text=text,
                    visibility=mention.reply_visibility,
                    reply_id=event.id,
                    file_ids=[reply.file_id] if reply.file_id else None,
                )
                logger.info(f"{replied(source)} to @{display}: {format_log_text(text)}")

            logger.info(
                f"Mention received from @{display}: {format_log_text(mention.text)}"
            )
            async with self.bot.pipeline.hold(mention.user_id, mention.username):
                await self.bot.pipeline.respond(
                    user_id=mention.user_id,
                    handle=mention.username,
                    hook="on_mention",
                    event=event,
                    generate=lambda augment: self._generate_ai_reply(
                        mention, event, augment
                    ),
                    deliver=deliver,
                )
        except Exception:
            logger.exception("Error handling mention")

    def _parse(self, event: MentionEvent) -> MentionContext | None:
        reply_to_bot = self._is_reply_to_bot(event)
        if not self._should_handle(event, reply_to_bot):
            if event.kind == "mention" and not reply_to_bot:
                logger.debug(
                    f"Mention from @{event.user.handle} does not mention the bot; "
                    "skipping"
                )
            return None
        if event.kind == "reply":
            text = "\n\n".join(
                p for p in (event.reply.body if event.reply else "", event.body) if p
            )
        else:
            text = event.body
        return MentionContext(
            text,
            event.user.id,
            event.user.known_handle,
            event.kind == "mention",
            "specified" if event.visibility == "specified" else None,
        )

    async def _generate_ai_reply(
        self, mention: MentionContext, event: MentionEvent, augment: Augment
    ) -> str:
        history = await self._thread_history(event, mention.user_id, augment.tools)
        text = augment.rewrite(event.body if history else mention.text, event.text)
        prompt = augment.wrap(
            await self._build_mention_prompt(
                text, event, augment.tools, include_parent=not history
            )
        )
        messages: list[dict[str, Any]] = []
        if self.bot.system_prompt:
            messages.append({"role": "system", "content": self.bot.system_prompt})
        messages.extend(history)
        messages.append({"role": "user", "content": augment.content(prompt)})
        return await self.bot.openai.generate_chat(
            messages, **self.bot.ai_config, tools=augment.tools
        )
