from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from twipsybot.plugin.manager import discover_plugin_classes
from twipsybot.tui.schema import Section, Spec, build_sections


@pytest.fixture(scope="module")
def sections() -> dict[str, Section]:
    plugins, errors = discover_plugin_classes(Path("plugins"))
    assert errors == {}
    return {s.id: s for s in build_sections(plugins)}


@pytest.fixture(scope="module")
def specs(sections: dict[str, Section]) -> dict[str, Spec]:
    return {spec.key: spec for s in sections.values() for spec in s.specs}


def test_core_sections_precede_discovered_plugin_sections(
    sections: dict[str, Section],
) -> None:
    assert list(sections)[:6] == [
        "connect",
        "bot",
        "timeline",
        "autopost",
        "reply",
        "system",
    ]
    assert {"plugins.keyact", "plugins.mllm"} <= sections.keys()
    assert [s.key for s in sections["connect"].specs] == [
        "misskey_url",
        "misskey_token",
        "ai_base_url",
        "ai_api_key",
    ]


def test_sections_and_secret_fields_route_to_their_files(
    sections: dict[str, Section], specs: dict[str, Spec]
) -> None:
    assert sections["connect"].files == ("secrets",)
    assert sections["bot"].files == ("settings",)
    assert sections["plugins.iincho"].files == ("secrets", "settings")
    assert specs["plugins.iincho.moderation.cf_api_token"].file == "secrets"
    assert specs["plugins.iincho.moderation.cf_account_id"].file == "secrets"
    assert specs["plugins.iincho.min_notes"].file == "settings"


@pytest.mark.parametrize(
    ("key", "kind"),
    [
        ("misskey_token", "secret"),
        ("plugins.iincho.moderation.cf_api_token", "secret"),
        ("timeline.global", "bool"),
        ("bot.system_prompt", "multiline"),
        ("reply.whitelist", "list"),
        ("plugins.topics.list", "list"),
        ("plugins.topics.rss_list", "lines"),
        ("plugins.keyact.rules", "lines"),
        ("reply.turns_release", "seconds"),
    ],
)
def test_field_kind_follows_model_annotation(
    specs: dict[str, Spec], key: str, kind: str
) -> None:
    assert specs[key].kind == kind


@pytest.mark.parametrize(
    ("key", "restart"),
    [("misskey_url", True), ("ai_base_url", True), ("bot.model", False)],
)
def test_restart_flag_marks_connection_fields(
    specs: dict[str, Spec], key: str, restart: bool
) -> None:
    assert specs[key].restart is restart


@pytest.mark.parametrize(
    ("key", "choices"),
    [
        ("bot.api_mode", ("auto", "chat", "responses")),
        ("plugins.radar.repeat", ("off", "renote", "quote")),
        ("plugins.topics.source", ("list", "rss")),
    ],
)
def test_literal_fields_expose_choices(
    specs: dict[str, Spec], key: str, choices: tuple[str, ...]
) -> None:
    assert specs[key].choices == choices


@pytest.mark.parametrize(
    ("key", "when"),
    [
        ("plugins.radar.quote_text", ("plugins.radar.repeat", "quote")),
        (
            "plugins.iincho.moderation.cf_api_token",
            ("plugins.iincho.moderation.provider", "cloudflare"),
        ),
    ],
)
def test_dependent_fields_record_their_condition(
    specs: dict[str, Spec], key: str, when: tuple[str, Any]
) -> None:
    assert specs[key].when == when


def test_declared_field_order_is_preserved(specs: dict[str, Spec]) -> None:
    radar = [k.split(".")[-1] for k in specs if k.startswith("plugins.radar.")]
    autopost = [k for k in specs if k.startswith("autopost.")]

    assert radar.index("reply_visibility") < radar.index("reply_local_only")
    assert radar.index("reply_local_only") < radar.index("repeat")
    assert autopost[:4] == [
        "autopost.prompt",
        "autopost.visibility",
        "autopost.local_only",
        "autopost.mode",
    ]
