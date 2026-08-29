from typing import Any

from loguru import logger

from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase, TimelineNoteEvent


class RadarPlugin(PluginBase):
    api_version = PLUGIN_API_VERSION
    description = "雷达插件：与天线推送的帖子互动（反应、回复、转发、引用）"

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
        self.skip_self = True

    async def initialize(self) -> bool:
        self._log_plugin_action("initialized", await self._format_antenna_sources())
        return True

    async def _format_antenna_sources(self) -> str:
        selectors = self.context.bot.load_antenna_selectors()
        if not selectors:
            return "Antenna: (empty)"
        id_to_name = await self._get_antenna_name_map()
        resolved_ids = await self._resolve_antenna_ids(selectors)
        return self._format_antenna_source_display(selectors, resolved_ids, id_to_name)

    @staticmethod
    def _dedupe(items: list[str]) -> list[str]:
        return list(dict.fromkeys(items))

    async def _get_antenna_name_map(self) -> dict[str, str]:
        antennas = await self.context.misskey.list_antennas()
        return {
            str(item["id"]): str(item["name"])
            for item in antennas
            if item.get("id") and item.get("name")
        }

    async def _resolve_antenna_ids(self, selectors: list[str]) -> list[str]:
        try:
            ids = await self.context.bot.resolve_antenna_ids(selectors)
        except Exception:
            return []
        ids = [v.strip() for v in ids if isinstance(v, str) and v.strip()]
        return self._dedupe(ids)

    def _format_antenna_source_display(
        self,
        selectors: list[str],
        resolved_ids: list[str],
        id_to_name: dict[str, str],
    ) -> str:
        if not resolved_ids:
            return f"Antenna: {', '.join(selectors)}"
        display = [
            id_to_name.get(antenna_id, antenna_id) for antenna_id in resolved_ids
        ]
        display = self._dedupe(display)
        return f"Antenna: {', '.join(display)}"

    @staticmethod
    def _normalize_str(value: Any) -> str | None:
        if value is None or isinstance(value, bool):
            return None
        if not isinstance(value, str):
            value = str(value)
        s = value.strip()
        return s or None

    @staticmethod
    def _parse_bool(value: Any, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return bool(value)
        if isinstance(value, float):
            return bool(int(value))
        if isinstance(value, str):
            s = value.strip().lower()
            if s in {"true", "1", "yes", "y", "on"}:
                return True
            if s in {"false", "0", "no", "n", "off"}:
                return False
        return default

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
        if not self.skip_self:
            return False
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

    async def _build_reply_text(self, note_data: dict[str, Any]) -> str | None:
        if self.reply_text:
            text = self._format_reply_text(self.reply_text, note_data).strip()
            if text:
                return text
        if not self.reply_ai:
            return None
        try:
            return await self._generate_ai(
                note_data, self.reply_ai_prompt or self.DEFAULT_REPLY_AI_PROMPT
            )
        except Exception as e:
            logger.error(f"Radar AI reply failed: {e!r}")
            return None

    async def _maybe_reply(
        self, note_data: dict[str, Any], note_id: str, channel: str
    ) -> None:
        if not self.reply_enabled:
            return
        if not (text := await self._build_reply_text(note_data)):
            return
        try:
            await self.context.misskey.create_note(
                text=text, reply_id=note_id, local_only=self.reply_local_only
            )
            self._log_plugin_action("replied", f"{note_id} [{channel}]")
        except Exception as e:
            logger.error(f"Radar reply failed: {e!r}")

    async def _build_quote_text(self, note_data: dict[str, Any]) -> str | None:
        if self.quote_text:
            text = self._format_reply_text(self.quote_text, note_data).strip()
            if text:
                return text
        if not self.quote_ai:
            return None
        try:
            return await self._generate_ai(
                note_data, self.quote_ai_prompt or self.DEFAULT_QUOTE_AI_PROMPT
            )
        except Exception as e:
            logger.error(f"Radar AI quote failed: {e!r}")
            return None

    async def _maybe_quote(
        self, note_data: dict[str, Any], note_id: str, channel: str
    ) -> bool:
        if not self.quote_enabled:
            return False
        if not (text := await self._build_quote_text(note_data)):
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
