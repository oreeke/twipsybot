import re
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import StringConstraints, field_validator

from twipsybot.plugin import (
    PLUGIN_API_VERSION,
    HandledResult,
    MentionEvent,
    MessageEvent,
    PluginBase,
    PluginConfig,
)

_MENTION_TOKEN_RE = re.compile(r"@[\w.@-]+\s*")


@dataclass(slots=True)
class _Rule:
    keywords: tuple[str, ...]
    response: str
    case_sensitive: bool


class _RuleConfig(PluginConfig):
    keywords: tuple[
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)], ...
    ]
    response: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    case_sensitive: bool | None = None
    enabled: bool = True

    @field_validator("keywords", mode="before")
    @classmethod
    def _accept_single_keyword(cls, value: Any) -> Any:
        return (value,) if isinstance(value, str) else value


class _Config(PluginConfig):
    mention_enabled: bool = True
    chat_enabled: bool = True
    case_sensitive: bool = False
    rules: tuple[_RuleConfig, ...] = ()


class KeyActPlugin(PluginBase):
    api_version = PLUGIN_API_VERSION
    config_class = _Config
    settings: _Config
    description = "匹配自定义关键词触发直接回复，绕过 AI"

    rules: tuple[_Rule, ...] = ()

    async def initialize(self) -> bool:
        self.rules = tuple(
            _Rule(
                tuple(
                    keyword if case_sensitive else keyword.lower()
                    for keyword in rule.keywords
                ),
                rule.response,
                case_sensitive,
            )
            for rule in self.settings.rules
            if rule.enabled
            for case_sensitive in (
                rule.case_sensitive
                if rule.case_sensitive is not None
                else self.settings.case_sensitive,
            )
        )
        self._log_plugin_action(
            "initialized",
            f"rules={len(self.rules)}, mention={self.settings.mention_enabled}, "
            f"chat={self.settings.chat_enabled}",
        )
        return True

    def _handle(self, text: str) -> HandledResult | None:
        if not self.rules or not text:
            return None
        cleaned = _MENTION_TOKEN_RE.sub("", text).strip()
        lowered = cleaned.lower()
        for rule in self.rules:
            candidate = cleaned if rule.case_sensitive else lowered
            if candidate and candidate in rule.keywords:
                return self.handled(rule.response)
        return None

    async def on_mention(self, event: MentionEvent) -> HandledResult | None:
        if not self.settings.mention_enabled:
            return None
        return self._handle(event.text)

    async def on_message(self, event: MessageEvent) -> HandledResult | None:
        if not self.settings.chat_enabled:
            return None
        return self._handle(event.text)
