from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from ...clients.misskey.models import MentionEvent, NoteRef, parse_note
from ...shared.logs import format_log_text
from ..engine.pipeline import (
    Augment,
    Reply,
    Source,
    Toolbox,
    replied,
    trim_history,
)

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
        return event.user.id == self.bot.me.id

    @staticmethod
    def _format_mention_reply(mention: MentionContext, text: str) -> str:
        if mention.explicit_mention and mention.username:
            return f"@{mention.username}\n{text}"
        return text

    def _is_reply_to_bot(self, event: MentionEvent) -> bool:
        return bool(event.reply and event.reply.user.id == self.bot.me.id)

    def _should_handle(self, event: MentionEvent, reply_to_bot: bool) -> bool:
        if event.kind == "reply":
            return reply_to_bot
        return not reply_to_bot and self.bot.me.id in event.mentions

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
        self, notes: Iterable[NoteRef], user_id: str | None, tools: Toolbox
    ) -> None:
        trusted = {self.bot.me.id, user_id}
        if any(n.body and n.user.id not in trusted for n in notes):
            tools.taint()

    async def _build_mention_prompt(
        self,
        text: str,
        event: MentionEvent,
        tools: Toolbox,
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
        if author_id == self.bot.me.id:
            return {"role": "assistant", "content": text}
        if author_id != user_id:
            text = f"@{note.user.handle}: {text}"
        return {"role": "user", "content": text}

    def _thread_notes(self, chain: list[NoteRef], user_id: str | None) -> list[NoteRef]:
        parent, *rest = chain
        if not rest:
            return [parent]
        *middle, root = rest
        participants = {self.bot.me.id, user_id} - {None}
        return [
            root,
            *(n for n in reversed(middle) if n.user.id in participants),
            parent,
        ]

    async def _thread_history(
        self, event: MentionEvent, user_id: str | None, tools: Toolbox
    ) -> list[dict[str, str]]:
        reply = self.bot.config.settings.reply
        limit, budget = reply.memory, reply.ctx_tokens
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
        return trim_history([m for _, m in selected], budget)

    async def handle(self, event: MentionEvent) -> None:
        if not self.bot.config.settings.reply.mention:
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
        display = mention.username or "unknown"

        async def deliver(reply: Reply, source: Source) -> None:
            text = self._format_mention_reply(mention, reply.text)
            await self.bot.misskey.create_note(
                text=text,
                visibility=mention.reply_visibility,
                reply_id=event.id,
                file_ids=list(reply.file_ids) or None,
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
        return await self.bot.generate_reply(
            history, augment.content(prompt), augment.tools
        )
