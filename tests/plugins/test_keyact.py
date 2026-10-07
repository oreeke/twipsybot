from __future__ import annotations

import pytest
from conftest import plugin_context

from plugins.keyact.plugin import KeyActPlugin
from twipsybot.plugin import MentionEvent, MessageEvent, UserRef


def test_keyact_parses_boolean_strings() -> None:
    plugin = KeyActPlugin(
        plugin_context(
            {
                "enabled": True,
                "mention_enabled": "false",
                "chat_enabled": "true",
                "case_sensitive": "false",
                "rules": [],
            }
        )
    )

    assert plugin.settings.mention_enabled is False
    assert plugin.settings.chat_enabled is True
    assert plugin.settings.case_sensitive is False


async def test_keyact_matches_body_when_mention_has_cw() -> None:
    plugin = KeyActPlugin(
        plugin_context(
            {
                "enabled": True,
                "rules": [{"keywords": ["ping"], "response": "pong"}],
            }
        )
    )
    await plugin.initialize()
    event = MentionEvent(
        id="note-1",
        text="ping",
        cw="content warning",
        user=UserRef(id="user-1", username="alice", host=None),
        files=(),
        raw={},
    )

    assert await plugin.on_mention(event) == {"handled": True, "response": "pong"}


async def test_keyact_normalizes_case_once_when_loading_rules() -> None:
    plugin = KeyActPlugin(
        plugin_context(
            {
                "enabled": True,
                "rules": [{"keywords": ["PING"], "response": "pong"}],
            }
        )
    )
    await plugin.initialize()
    event = MessageEvent(
        id="message-1",
        text="ping",
        user=UserRef(id="user-1", username="alice", host=None),
        room_id=None,
        files=(),
        raw={},
    )

    assert await plugin.on_message(event) == {"handled": True, "response": "pong"}


async def test_keyact_reads_line_rules() -> None:
    plugin = KeyActPlugin(
        plugin_context(
            {
                "enabled": True,
                "rules": "# 问候\nping, hi = pong\n帮助，help | ? = 看文档\\n#话题\n",
            }
        )
    )
    await plugin.initialize()

    assert [(rule.keywords, rule.response) for rule in plugin.rules] == [
        (("ping", "hi"), "pong"),
        (("帮助", "help", "?"), "看文档\n#话题"),
    ]


def test_keyact_rejects_rule_without_response() -> None:
    context = plugin_context({"enabled": True, "rules": "ping"})
    with pytest.raises(ValueError, match="keywords = response"):
        KeyActPlugin(context)
