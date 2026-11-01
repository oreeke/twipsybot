from __future__ import annotations

from typing import Any

import pytest
from support.factories import plugin_context

from plugins.keyact.plugin import KeyActPlugin
from twipsybot.plugin import MentionEvent, MessageEvent, UserRef

ALICE = UserRef(id="user-1", username="alice", host=None)
PONG = {"handled": True, "response": "pong"}


async def _plugin(**config: Any) -> KeyActPlugin:
    plugin = KeyActPlugin(plugin_context({"enabled": True, **config}))
    await plugin.initialize()
    return plugin


def _message(text: str) -> MessageEvent:
    return MessageEvent(
        id="message-1", text=text, user=ALICE, room_id=None, files=(), raw={}
    )


def _mention(text: str, cw: str | None = None) -> MentionEvent:
    return MentionEvent(id="note-1", text=text, cw=cw, user=ALICE, files=(), raw={})


async def test_keyact_matches_body_when_mention_has_cw() -> None:
    plugin = await _plugin(rules=[{"keywords": ["ping"], "response": "pong"}])

    assert await plugin.on_mention(_mention("ping", cw="content warning")) == PONG


async def test_keyact_ignores_mention_tokens_when_matching() -> None:
    plugin = await _plugin(rules=[{"keywords": ["ping"], "response": "pong"}])

    assert await plugin.on_mention(_mention("@bot @other@host.example ping")) == PONG


@pytest.mark.parametrize("text", ("pong", "ping pong", "", "@bot"))
async def test_keyact_ignores_text_without_exact_keyword(text: str) -> None:
    plugin = await _plugin(rules=[{"keywords": ["ping"], "response": "pong"}])

    assert await plugin.on_message(_message(text)) is None


@pytest.mark.parametrize(
    ("global_cs", "rule_cs", "text", "matched"),
    (
        (False, None, "ping", True),
        (True, None, "ping", False),
        (True, None, "PING", True),
        (True, False, "ping", True),
        (False, True, "ping", False),
    ),
)
async def test_keyact_applies_case_sensitivity(
    global_cs: bool, rule_cs: bool | None, text: str, matched: bool
) -> None:
    rule = {"keywords": ["PING"], "response": "pong", "case_sensitive": rule_cs}
    plugin = await _plugin(rules=[rule], case_sensitive=global_cs)

    assert (await plugin.on_message(_message(text)) == PONG) is matched


async def test_keyact_skips_disabled_rule() -> None:
    plugin = await _plugin(
        rules=[
            {"keywords": ["ping"], "response": "off", "enabled": False},
            {"keywords": ["ping"], "response": "pong"},
        ]
    )

    assert await plugin.on_message(_message("ping")) == PONG


async def test_keyact_reads_line_rules() -> None:
    plugin = await _plugin(
        rules="# 问候\nping, hi = pong\n帮助，help | ? = 看文档\\n#话题\n"
    )

    assert await plugin.on_message(_message("hi")) == PONG
    assert await plugin.on_message(_message("?")) == {
        "handled": True,
        "response": "看文档\n#话题",
    }


@pytest.mark.parametrize(
    ("option", "hook", "event"),
    (
        ("mention", "on_mention", _mention("ping")),
        ("chat", "on_message", _message("ping")),
    ),
)
async def test_keyact_ignores_disabled_channel(
    option: str, hook: str, event: MentionEvent | MessageEvent
) -> None:
    plugin = await _plugin(
        rules=[{"keywords": ["ping"], "response": "pong"}], **{option: False}
    )

    assert await getattr(plugin, hook)(event) is None


def test_keyact_rejects_rule_without_response() -> None:
    context = plugin_context({"enabled": True, "rules": "ping"})

    with pytest.raises(ValueError, match="keywords = response"):
        KeyActPlugin(context)
