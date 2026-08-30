from typing import Any

from loguru import logger

from twipsybot.plugin import (
    PLUGIN_API_VERSION,
    PluginBase,
    TimelineNoteEvent,
)


class RadarPlugin(PluginBase):
    api_version = PLUGIN_API_VERSION
    description = "雷达插件：主动与天线发现的帖子互动（反应、回复、转发、引用）"

    DEFAULT_REPLY_AI_PROMPT = (
        "根据帖子内容写一句自然回复，不要复述原文，不要加引号，不超过30字：\n{content}"
    )
    DEFAULT_QUOTE_AI_PROMPT = (
        "根据帖子内容写一句简短感想，不要复述原文，不要加引号，不超过30字：\n{content}"
    )

    def __init__(self, context):
        super().__init__(context)
        config = self.context.config
        self.reaction = self._normalize_str(config.get("reaction"))
        self.reply_enabled = self._parse_bool(config.get("reply"), False)
        self.reply_text = self._normalize_str(config.get("reply_text"))
        self.reply_ai = self._parse_bool(config.get("reply_ai"), False)
        self.reply_ai_prompt = self._normalize_str(config.get("reply_ai_prompt"))
        self.reply_local_only = self._parse_bool(config.get("reply_local_only"), False)
        self.quote_enabled = self._parse_bool(config.get("quote"), False)
        self.quote_text = self._normalize_str(config.get("quote_text"))
        self.quote_ai = self._parse_bool(config.get("quote_ai"), False)
        self.quote_ai_prompt = self._normalize_str(config.get("quote_ai_prompt"))
        self.quote_visibility = self._normalize_visibility(
            config.get("quote_visibility")
        )
        self.quote_local_only = self._parse_bool(config.get("quote_local_only"), False)
        self.renote_enabled = self._parse_bool(config.get("renote"), False)
        self.renote_visibility = self._normalize_visibility(
            config.get("renote_visibility")
        )
        self.renote_local_only = self._parse_bool(
            config.get("renote_local_only"), False
        )

    async def initialize(self) -> bool:
        selectors = self.context.bot.load_antenna_selectors()
        self._log_plugin_action(
            "initialized", f"Antenna: {', '.join(selectors) or '(empty)'}"
        )
        return True

    @staticmethod
    def _normalize_str(value: Any) -> str | None:
        if value is None or isinstance(value, bool):
            return None
        if not isinstance(value, str):
            value = str(value)
        s = value.strip()
        return s or None

    def _normalize_visibility(self, value: Any) -> str | None:
        s = self._normalize_str(value)
        if not s:
            return None
        v = s.lower()
        if v in {"public", "home", "followers"}:
            return v
        return None

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
        if not self.reaction or note_data.get("myReaction"):
            return
        try:
            await self.context.misskey.create_reaction(note_id, self.reaction)
            self._log_plugin_action("reacted", f"{note_id} {self.reaction} [{channel}]")
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
        if not self.reply_enabled:
            return
        text = await self._build_action_text(
            note_data,
            text=self.reply_text,
            ai_enabled=self.reply_ai,
            ai_prompt=self.reply_ai_prompt,
            default_prompt=self.DEFAULT_REPLY_AI_PROMPT,
            action="reply",
        )
        if not text:
            return
        try:
            await self.context.misskey.create_note(
                text=text, reply_id=note_id, local_only=self.reply_local_only
            )
            self._log_plugin_action("replied", f"{note_id} [{channel}]")
        except Exception as e:
            logger.error(f"Radar reply failed: {e!r}")

    async def _maybe_quote(
        self, note_data: dict[str, Any], note_id: str, channel: str
    ) -> bool:
        if not self.quote_enabled:
            return False
        text = await self._build_action_text(
            note_data,
            text=self.quote_text,
            ai_enabled=self.quote_ai,
            ai_prompt=self.quote_ai_prompt,
            default_prompt=self.DEFAULT_QUOTE_AI_PROMPT,
            action="quote",
        )
        if not text:
            return False
        try:
            await self.context.misskey.create_renote(
                note_id,
                visibility=self.quote_visibility,
                text=text,
                local_only=self.quote_local_only,
            )
            self._log_plugin_action(
                "quoted", f"{note_id} {self.quote_visibility or ''} [{channel}]"
            )
            return True
        except Exception as e:
            logger.error(f"Radar quote failed: {e!r}")
            return False

    async def _maybe_renote(self, note_id: str, channel: str) -> None:
        if not self.renote_enabled:
            return
        try:
            await self.context.misskey.create_renote(
                note_id,
                visibility=self.renote_visibility,
                local_only=self.renote_local_only,
            )
            self._log_plugin_action(
                "renoted", f"{note_id} {self.renote_visibility or ''} [{channel}]"
            )
        except Exception as e:
            logger.error(f"Radar renote failed: {e!r}")

    async def _act(self, note_data: dict[str, Any], note_id: str, channel: str) -> None:
        await self._maybe_react(note_data, note_id, channel)
        await self._maybe_reply(note_data, note_id, channel)
        did_quote = await self._maybe_quote(note_data, note_id, channel)
        if not did_quote:
            await self._maybe_renote(note_id, channel)
