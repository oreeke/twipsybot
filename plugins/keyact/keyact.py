import re
from dataclasses import dataclass
from typing import Any

from twipsybot.plugin import (
    PLUGIN_API_VERSION,
    HandledResult,
    MentionEvent,
    MessageEvent,
    PluginBase,
)

_MENTION_TOKEN_RE = re.compile(r"@[\w.@-]+\s*")


@dataclass(slots=True)
class _Rule:
    keywords: tuple[str, ...]
    response: str
    case_sensitive: bool


class KeyActPlugin(PluginBase):
    api_version = PLUGIN_API_VERSION
    description = "KeyAct 插件：匹配自定义关键词直接回复，绕过 AI"

    def __init__(self, context):
        super().__init__(context)
        config = self.context.config
        self.mention_enabled = self._parse_bool(config.get("mention_enabled"), True)
        self.chat_enabled = self._parse_bool(config.get("chat_enabled"), True)
        self.default_case_sensitive = self._parse_bool(
            config.get("case_sensitive"), False
        )
        self.rules: tuple[_Rule, ...] = ()

    async def initialize(self) -> bool:
        self.rules = self._load_rules()
        self._log_plugin_action(
            "initialized",
            f"rules={len(self.rules)}, mention={self.mention_enabled}, chat={self.chat_enabled}",
        )
        return True

    @staticmethod
    def _as_keywords(v: Any) -> tuple[str, ...]:
        if isinstance(v, str):
            s = v.strip()
            return (s,) if s else ()
        if isinstance(v, list):
            out: list[str] = []
            for item in v:
                if isinstance(item, str) and (s := item.strip()):
                    out.append(s)
            return tuple(out)
        return ()

    def _parse_rule_item(self, item: dict[str, Any]) -> _Rule | None:
        if not self._parse_bool(item.get("enabled"), True):
            return None
        response = item.get("response")
        if not isinstance(response, str) or not (response := response.strip()):
            return None
        case_sensitive = self._parse_bool(
            item.get("case_sensitive"), self.default_case_sensitive
        )
        keywords = self._as_keywords(item.get("keywords") or item.get("keyword"))
        if not case_sensitive:
            keywords = tuple(keyword.lower() for keyword in keywords)
        if not keywords:
            return None
        return _Rule(
            keywords=keywords,
            response=response,
            case_sensitive=case_sensitive,
        )

    def _load_rules(self) -> tuple[_Rule, ...]:
        raw = self.context.config.get("rules")
        if not isinstance(raw, list) or not raw:
            return ()
        rules = (self._parse_rule_item(item) for item in raw if isinstance(item, dict))
        return tuple(r for r in rules if r is not None)

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
        if not self.mention_enabled:
            return None
        return self._handle(event.text)

    async def on_message(self, event: MessageEvent) -> HandledResult | None:
        if not self.chat_enabled:
            return None
        return self._handle(event.text)
