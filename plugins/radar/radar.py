from typing import Any, Literal

from loguru import logger
from pydantic import Field

from twipsybot.plugin import (
    PLUGIN_API_VERSION,
    PluginBase,
    PluginConfig,
    TimelineNoteEvent,
)


class _Config(PluginConfig):
    reaction: str | None = None
    reply_enabled: bool = Field(False, validation_alias="reply")
    reply_text: str | None = None
    reply_ai: bool = False
    reply_ai_prompt: str | None = None
    reply_local_only: bool = False
    quote_enabled: bool = Field(False, validation_alias="quote")
    quote_text: str | None = None
    quote_ai: bool = False
    quote_ai_prompt: str | None = None
    quote_visibility: Literal["public", "home", "followers"] | None = None
    quote_local_only: bool = False
    renote_enabled: bool = Field(False, validation_alias="renote")
    renote_visibility: Literal["public", "home", "followers"] | None = None
    renote_local_only: bool = False


class RadarPlugin(PluginBase):
    api_version = PLUGIN_API_VERSION
    config_class = _Config
    settings: _Config
    description = "主动与天线发现的帖子互动（反应、回复、转发、引用）"

    DEFAULT_REPLY_AI_PROMPT = (
        "根据帖子内容写一句自然回复，不要复述原文，不要加引号，不超过30字：\n{content}"
    )
    DEFAULT_QUOTE_AI_PROMPT = (
        "根据帖子内容写一句简短感想，不要复述原文，不要加引号，不超过30字：\n{content}"
    )

    async def initialize(self) -> bool:
        selectors = self.context.bot.load_antenna_selectors()
        self._log_plugin_action(
            "initialized", f"Antenna: {', '.join(selectors) or '(empty)'}"
        )
        return True

    def _effective_text(self, note: dict[str, Any]) -> str:
        parts: list[str] = []
        for k in ("cw", "text"):
            v = note.get(k)
            if isinstance(v, str) and v.strip():
                parts.append(v.strip())
        renote = note.get("renote")
        if isinstance(renote, dict):
            parts.append(self._effective_text(renote))
        return "\n".join(p for p in parts if p).strip()

    def _should_skip_self(self, event: TimelineNoteEvent) -> bool:
        bot_id = self.context.bot.user_id
        if bot_id and event.user.id:
            return event.user.id == bot_id
        if event.user.host:
            return False
        bot_name = self.context.bot.username
        if not isinstance(bot_name, str) or not bot_name:
            return False
        return bot_name.lower() == event.user.username.lower()

    @staticmethod
    def _format_reply_text(template: str, note: dict[str, Any]) -> str:
        if "{username}" not in template:
            return template
        user = note.get("user")
        username = user.get("username") if isinstance(user, dict) else None
        return template.replace(
            "{username}", username if isinstance(username, str) else "unknown"
        )

    async def _generate_ai(
        self, note: dict[str, Any], prompt_template: str
    ) -> str | None:
        if not (content := self._effective_text(note)):
            return None
        prompt = prompt_template.format(content=content)
        reply = await self.context.openai.generate_text(
            prompt,
            self.context.openai.system_prompt or None,
            max_tokens=self.context.openai.max_tokens,
            temperature=self.context.openai.temperature,
        )
        return reply.strip() or None

    async def on_timeline_note(self, event: TimelineNoteEvent) -> None:
        if event.channel != "antenna" or not event.id:
            return None
        if self._should_skip_self(event):
            return None
        try:
            async with self.context.bot.actor_lock(event.user.id, event.user.handle):
                await self._act(dict(event.raw), event.id, event.channel)
        except Exception as e:
            logger.error(f"Radar interaction failed: {e!r}")

    async def _maybe_react(
        self, note_data: dict[str, Any], note_id: str, channel: str
    ) -> None:
        if not self.settings.reaction or note_data.get("myReaction"):
            return
        try:
            await self.context.misskey.create_reaction(note_id, self.settings.reaction)
            self._log_plugin_action(
                "reacted", f"{note_id} {self.settings.reaction} [{channel}]"
            )
        except Exception as e:
            logger.error(f"Radar reaction failed: {e!r}")

    async def _build_action_text(
        self,
        note_data: dict[str, Any],
        *,
        text: str | None,
        ai_enabled: bool,
        ai_prompt: str | None,
        default_prompt: str,
        action: str,
    ) -> str | None:
        if text:
            text = self._format_reply_text(text, note_data).strip()
            if text:
                return text
        if not ai_enabled:
            return None
        try:
            return await self._generate_ai(note_data, ai_prompt or default_prompt)
        except Exception as e:
            logger.error(f"Radar AI {action} failed: {e!r}")
            return None

    async def _maybe_reply(
        self, note_data: dict[str, Any], note_id: str, channel: str
    ) -> None:
        if not self.settings.reply_enabled:
            return
        text = await self._build_action_text(
            note_data,
            text=self.settings.reply_text,
            ai_enabled=self.settings.reply_ai,
            ai_prompt=self.settings.reply_ai_prompt,
            default_prompt=self.DEFAULT_REPLY_AI_PROMPT,
            action="reply",
        )
        if not text:
            return
        try:
            await self.context.misskey.create_note(
                text=text, reply_id=note_id, local_only=self.settings.reply_local_only
            )
            self._log_plugin_action("replied", f"{note_id} [{channel}]")
        except Exception as e:
            logger.error(f"Radar reply failed: {e!r}")

    async def _maybe_quote(
        self, note_data: dict[str, Any], note_id: str, channel: str
    ) -> bool:
        if not self.settings.quote_enabled:
            return False
        text = await self._build_action_text(
            note_data,
            text=self.settings.quote_text,
            ai_enabled=self.settings.quote_ai,
            ai_prompt=self.settings.quote_ai_prompt,
            default_prompt=self.DEFAULT_QUOTE_AI_PROMPT,
            action="quote",
        )
        if not text:
            return False
        try:
            await self.context.misskey.create_renote(
                note_id,
                visibility=self.settings.quote_visibility,
                text=text,
                local_only=self.settings.quote_local_only,
            )
            self._log_plugin_action(
                "quoted",
                f"{note_id} {self.settings.quote_visibility or ''} [{channel}]",
            )
            return True
        except Exception as e:
            logger.error(f"Radar quote failed: {e!r}")
            return False

    async def _maybe_renote(self, note_id: str, channel: str) -> None:
        if not self.settings.renote_enabled:
            return
        try:
            await self.context.misskey.create_renote(
                note_id,
                visibility=self.settings.renote_visibility,
                local_only=self.settings.renote_local_only,
            )
            self._log_plugin_action(
                "renoted",
                f"{note_id} {self.settings.renote_visibility or ''} [{channel}]",
            )
        except Exception as e:
            logger.error(f"Radar renote failed: {e!r}")

    async def _act(self, note_data: dict[str, Any], note_id: str, channel: str) -> None:
        await self._maybe_react(note_data, note_id, channel)
        await self._maybe_reply(note_data, note_id, channel)
        did_quote = await self._maybe_quote(note_data, note_id, channel)
        if not did_quote:
            await self._maybe_renote(note_id, channel)
