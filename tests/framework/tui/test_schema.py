from __future__ import annotations

from pathlib import Path

from twipsybot.plugin.manager import discover_plugin_classes
from twipsybot.tui.schema import build_sections


def test_sections_are_generated_from_models() -> None:
    plugins, errors = discover_plugin_classes(Path("plugins"))
    sections = {s.id: s for s in build_sections(plugins)}
    specs = {spec.key: spec for s in sections.values() for spec in s.specs}

    assert errors == {}
    assert list(sections)[:6] == [
        "connect",
        "bot",
        "timeline",
        "autopost",
        "reply",
        "system",
    ]
    assert sections["connect"].files == ("secrets",)
    assert sections["plugins.iincho"].files == ("secrets", "settings")
    assert sections["bot"].files == ("settings",)
    assert specs["plugins.iincho.moderation.cf_api_token"].file == "secrets"
    assert specs["plugins.iincho.moderation.cf_account_id"].file == "secrets"
    assert specs["plugins.iincho.min_notes"].file == "settings"
    assert specs["misskey_token"].kind == "secret"
    assert specs["misskey_url"].restart
    assert {"plugins.keyact", "plugins.mllm"} <= sections.keys()
    assert [s.key for s in sections["connect"].specs] == [
        "misskey_url",
        "misskey_token",
        "ai_base_url",
        "ai_api_key",
    ]
    assert specs["ai_base_url"].restart
    assert [(s.group, s.label) for s in sections["connect"].specs] == [
        ("misskey", "url"),
        ("misskey", "token"),
        ("ai", "base url"),
        ("ai", "api key"),
    ]
    assert not specs["bot.model"].restart
    assert specs["bot.api_mode"].choices == ("auto", "chat", "responses")
    assert specs["plugins.topics.rss_list"].kind == "lines"
    assert specs["plugins.keyact.rules"].kind == "lines"
    assert specs["plugins.keyact.rules"].hint == "ping, hi = pong"
    assert specs["plugins.topics.rss_post_mode"].default == "rotate"
    assert specs["reply.turns_release"].kind == "seconds"
    assert specs["reply.turns_release"].default == "1h"
    assert specs["reply.rate_limit"].default == "-1"
    assert "system.db_clear_days" in specs
    radar = [k.split(".")[-1] for k in specs if k.startswith("plugins.radar.")]
    assert radar.index("reply_visibility") < radar.index("reply_local_only")
    assert radar.index("reply_local_only") < radar.index("repeat")
    assert specs["plugins.radar.repeat"].choices == ("off", "renote", "quote")
    assert specs["plugins.radar.quote_text"].when == (
        "plugins.radar.repeat",
        "quote",
    )
    assert specs["plugins.iincho.moderation.cf_api_token"].when == (
        "plugins.iincho.moderation.provider",
        "cloudflare",
    )
    assert specs["plugins.topics.list"].kind == "list"
    assert specs["plugins.topics.source"].choices == ("list", "rss")
    keys = [k for k in specs if k.startswith("autopost.")]
    assert keys[:4] == [
        "autopost.prompt",
        "autopost.visibility",
        "autopost.local_only",
        "autopost.mode",
    ]
    assert specs["autopost.interval"].default == "3h"
    assert specs["timeline.global"].kind == "bool"
    assert specs["bot.system_prompt"].kind == "multiline"
    assert specs["reply.whitelist"].kind == "list"
    assert specs["plugins.iincho.moderation.cf_api_token"].kind == "secret"
    assert specs["plugins.iincho.moderation.provider"].group == "moderation"
