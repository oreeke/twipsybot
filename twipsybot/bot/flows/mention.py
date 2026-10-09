from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from loguru import logger

from ...clients.misskey.payloads import (
    extract_note_text,
    extract_user_handle,
    extract_user_id,
    extract_username,
    normalize_payload,
)
from ...shared.config_keys import ConfigKeys
from ...shared.logs import format_log_text, maybe_log_event_dump
from ..engine.pipeline import Augment, Reply, Source, replied

if TYPE_CHECKING:
    from ..engine.core import Neuro


@dataclass(slots=True)
class MentionContext:
    mention_id: str | None
    text: str
    user_id: str | None
    username: str | None
    explicit_mention: bool
    reply_visibility: str | None


class MentionHandler:
    def __init__(self, bot: "Neuro"):
        self.bot = bot

    def _is_self_mention(self, mention: MentionContext) -> bool:
        return bool(
            self.bot.bot_user_id
            and mention.user_id
            and mention.user_id == self.bot.bot_user_id
        )

    @staticmethod
    def _format_mention_reply(mention: MentionContext, text: str) -> str:
        if mention.explicit_mention and mention.username:
            return f"@{mention.username}\n{text}"
        return text

    def _should_handle_note(
        self,
        *,
        note_type: str | None,
        is_reply_event: bool,
        reply_to_bot: bool,
        note_data: dict[str, Any],
    ) -> bool:
        if note_type == "mention" and reply_to_bot:
            return False
        if is_reply_event:
            return reply_to_bot
        return self._mentions_bot(note_data)

    def _is_reply_to_bot(self, note_data: dict[str, Any]) -> bool:
        replied = note_data.get("reply")
        if not isinstance(replied, dict):
            return False
        replied_user_id = extract_user_id(replied)
        return bool(self.bot.bot_user_id and replied_user_id == self.bot.bot_user_id)

    def _parse_reply_text(self, note_data: dict[str, Any]) -> str:
        parts: list[str] = []
        if t := extract_note_text(note_data.get("reply"), include_cw=True):
            parts.append(t)
        if t := extract_note_text(note_data, include_cw=True):
            parts.append(t)
        return "\n\n".join(parts).strip()

    async def _related_note_text(
        self, note_data: dict[str, Any], key: str, label: str
    ) -> str:
        if isinstance(inline := note_data.get(key), dict):
            return extract_note_text(inline, include_cw=True)
        if not isinstance((note_id := note_data.get(f"{key}Id")), str) or not note_id:
            return ""
        try:
            related = await self.bot.misskey.get_note(note_id)
        except Exception as e:
            logger.debug(f"Failed to fetch {label} note: {note_id} - {e}")
            return ""
        return extract_note_text(related, include_cw=True)

    async def _build_mention_prompt(self, text: str, note: dict[str, Any]) -> str:
        note_data = normalize_payload(note)
        parts = [text.strip()]
        if note_data:
            if quoted := await self._related_note_text(note_data, "renote", "quoted"):
                parts.append(f"Quote:\n{quoted}")
            if not self._is_reply_to_bot(note_data) and (
                parent := await self._related_note_text(note_data, "reply", "parent")
            ):
                parts.append(f"Replying to:\n{parent}")
        return "\n\n".join(p for p in parts if p)

    async def handle(self, note: dict[str, Any]) -> None:
        if not self.bot.config.get(ConfigKeys.REPLY_MENTION):
            return
        mention = self._parse(note)
        if not mention.mention_id or self._is_self_mention(mention):
            return
        if mention.user_id and self.bot.limits.is_response_blacklisted_user(
            user_id=mention.user_id, handle=mention.username
        ):
            return
        try:
            display = mention.username or "unknown"
            try:
                await self.bot.misskey.get_note(mention.mention_id)
            except Exception as e:
                logger.warning(
                    f"Skipping mention because source note is unavailable: "
                    f"{mention.mention_id} - {e}"
                )
                return

            async def deliver(reply: Reply, source: Source) -> None:
                text = self._format_mention_reply(mention, reply.text)
                await self.bot.misskey.create_note(
                    text=text,
                    visibility=mention.reply_visibility,
                    reply_id=mention.mention_id,
                    file_ids=[reply.file_id] if reply.file_id else None,
                )
                logger.info(f"{replied(source)} to @{display}: {format_log_text(text)}")

            logger.info(
                f"Mention received from @{display}: {format_log_text(mention.text)}"
            )
            async with self.bot.pipeline.hold(mention.user_id, mention.username):
                if (
                    reply := await self.bot.admin.handle_slash_command(
                        extract_note_text(normalize_payload(note), include_cw=True)
                        or mention.text,
                        user_id=mention.user_id,
                        username=display,
                        handle=mention.username,
                        private=False,
                    )
                ) is not None:
                    if reply.text:
                        await deliver(reply, "command")
                    return
                await self.bot.pipeline.respond(
                    user_id=mention.user_id,
                    handle=mention.username,
                    hook="on_mention",
                    event=note,
                    generate=lambda augment: self._generate_ai_reply(
                        mention, note, augment
                    ),
                    deliver=deliver,
                )
        except Exception:
            logger.exception("Error handling mention")

    def _parse(self, note: dict[str, Any]) -> MentionContext:
        try:
            maybe_log_event_dump(
                bool(self.bot.config.get(ConfigKeys.SYSTEM_DUMP_EVENTS)),
                kind="Mention",
                payload=note,
            )
            note_data = normalize_payload(note)
            if not note_data:
                return MentionContext(None, "", None, None, False, None)
            note_type = note.get("type")
            is_reply_event = note_type == "reply"
            note_id = (
                note_data.get("id") if isinstance(note_data.get("id"), str) else None
            )
            user_id = extract_user_id(note_data)
            username = extract_user_handle(note_data)
            if is_reply_event:
                text = self._parse_reply_text(note_data)
            else:
                text = extract_note_text(note_data, include_cw=True)
            reply_to_bot = self._is_reply_to_bot(note_data)
            should_handle = self._should_handle_note(
                note_type=note_type,
                is_reply_event=is_reply_event,
                reply_to_bot=reply_to_bot,
                note_data=note_data,
            )
            if not should_handle:
                if not is_reply_event and not (note_type == "mention" and reply_to_bot):
                    display = username or extract_username(note_data)
                    logger.debug(
                        f"Mention from @{display} does not mention the bot; skipping"
                    )
                note_id = None
            return MentionContext(
                note_id,
                text,
                user_id,
                username,
                not is_reply_event,
                "specified" if note_data.get("visibility") == "specified" else None,
            )
        except Exception:
            logger.exception("Failed to parse message data")
            return MentionContext(None, "", None, None, False, None)

    def _mentions_bot(self, note_data: dict[str, Any]) -> bool:
        mentions = note_data.get("mentions")
        if not self.bot.bot_user_id or not isinstance(mentions, list):
            return False
        return self.bot.bot_user_id in mentions

    async def _generate_ai_reply(
        self, mention: MentionContext, note: dict[str, Any], augment: Augment
    ) -> str:
        original = extract_note_text(normalize_payload(note), include_cw=False)
        text = augment.rewrite(mention.text, original)
        prompt = augment.wrap(await self._build_mention_prompt(text, note))
        return await self.bot.openai.generate_text(
            prompt, self.bot.system_prompt, **self.bot.ai_config
        )
