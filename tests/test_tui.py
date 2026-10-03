from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from textual.widgets import Button, Input, Switch, TextArea

from twipsybot.plugin.manager import discover_plugin_classes
from twipsybot.shared.config import Config
from twipsybot.shared.settings import read_settings, write_settings
from twipsybot.tui import ConfigApp
from twipsybot.tui.schema import build_sections

_DEMO_PLUGIN = """\
from typing import Annotated

from pydantic import Field

from twipsybot.plugin import LineText, PLUGIN_API_VERSION, PluginBase, PluginConfig


class _Config(PluginConfig):
    greeting: str = "hi"
    tags: tuple[str, ...] = ()
    feeds: Annotated[tuple[str, ...], LineText] = ()
    limit: int = Field(3, ge=1)


class Demo(PluginBase):
    api_version = PLUGIN_API_VERSION
    priority = 7
    config_class = _Config


plugin = Demo
"""


async def _until(pilot: Any, condition: Callable[[], bool]) -> None:
    for _ in range(100):
        if condition():
            return
        await pilot.pause()
    raise AssertionError("condition not reached")


def _root(tmp_path: Path) -> Path:
    plugin_dir = tmp_path / "plugins" / "demo"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.py").write_text(_DEMO_PLUGIN, encoding="utf-8")
    return tmp_path


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
    assert sections["connect"].file == "secrets"
    assert specs["misskey_token"].kind == "secret"
    assert specs["misskey_url"].restart
    assert {"plugins.keyact", "plugins.vision"} <= sections.keys()
    assert [s.key for s in sections["connect"].specs] == [
        "misskey_url",
        "misskey_token",
        "openai_base_url",
        "openai_api_key",
    ]
    assert specs["openai_base_url"].restart
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
    assert radar.index("renote") < radar.index("quote")
    assert specs["autopost.interval"].default == "3h"
    assert specs["timeline.global"].kind == "bool"
    assert specs["bot.system_prompt"].kind == "multiline"
    assert specs["reply.whitelist"].kind == "list"
    assert specs["plugins.keyact.priority"].default == "990"
    assert specs["plugins.iincho.moderation.cf_api_token"].kind == "secret"
    assert specs["plugins.iincho.moderation.provider"].group == "moderation"


async def test_cfg_validates_and_saves_minimal_diff(tmp_path: Path) -> None:
    config = Config(_root(tmp_path))
    app = ConfigApp(config)
    async with app.run_test() as pilot:
        await _until(pilot, lambda: bool(app._saved))

        def widget(key: str) -> Any:
            return app.query_one(f"#{app._ids[key]}")

        assert isinstance(widget("reply.memory"), Input)
        assert isinstance(widget("plugins.demo.enabled"), Switch)
        assert isinstance(widget("plugins.demo.tags"), TextArea)
        widget("reply.memory").value = "500"
        await _until(pilot, lambda: bool(app._errors["reply"]))
        assert app._errors["reply"] == {
            "reply.memory": "Input should be less than or equal to 100"
        }
        assert app.action_save() is False
        assert not config.settings_path.exists()

        widget("reply.memory").value = "20"
        widget("plugins.demo.enabled").value = True
        widget("plugins.demo.tags").load_text("a\n\nb\n")
        write_settings(config.settings_path, {"autopost": {"local_only": True}})
        assert app.action_save() is True

    assert read_settings(config.settings_path) == {
        "autopost": {"local_only": True},
        "reply": {"memory": 20},
        "plugins": {"demo": {"enabled": True, "tags": ["a", "b"]}},
    }


async def test_cfg_loads_existing_values_and_resets_to_defaults(tmp_path: Path) -> None:
    config = Config(_root(tmp_path))
    write_settings(
        config.settings_path,
        {"bot": {"model": "custom"}, "plugins": {"demo": {"limit": 5}}},
    )
    app = ConfigApp(config)
    async with app.run_test() as pilot:
        await _until(pilot, lambda: bool(app._saved))
        model = app.query_one(f"#{app._ids['bot.model']}", Input)
        limit = app.query_one(f"#{app._ids['plugins.demo.limit']}", Input)
        assert (model.value, limit.value) == ("custom", "5")

        model.value = "deepseek-flash"
        limit.value = ""
        status = app.query_one("#status")
        await _until(pilot, lambda: "2 unsaved" in str(status.render()))
        assert app.action_save() is True

    assert read_settings(config.settings_path) == {}


async def test_cfg_connect_writes_secrets_and_locks_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MISSKEY_INSTANCE_URL", raising=False)
    monkeypatch.delenv("MISSKEY_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "from-env")
    config = Config(_root(tmp_path))
    app = ConfigApp(config)
    async with app.run_test() as pilot:
        await _until(pilot, lambda: bool(app._saved))
        key = app.query_one(f"#{app._ids['openai_api_key']}", Input)
        assert key.disabled
        assert key.value == "from-env"

        app.query_one(f"#{app._ids['misskey_url']}", Input).value = "https://m.example"
        app.query_one(f"#{app._ids['misskey_token']}", Input).value = "tok"
        env = app.query_one("#env")
        await _until(pilot, lambda: "m.example" in str(env.render()))
        assert app.action_save() is True

    assert read_settings(config.secrets_path) == {
        "misskey_url": "https://m.example",
        "misskey_token": "tok",
    }
    assert not config.settings_path.exists()


async def test_cfg_line_fields_keep_comments(tmp_path: Path) -> None:
    config = Config(_root(tmp_path))
    text = "# news\nhttps://a.example/rss\n\nhttps://b.example/rss#top"
    app = ConfigApp(config)
    async with app.run_test() as pilot:
        await _until(pilot, lambda: bool(app._saved))
        feeds = app.query_one(f"#{app._ids['plugins.demo.feeds']}", TextArea)
        feeds.load_text(text)
        await _until(pilot, lambda: app._is_dirty("plugins.demo.feeds"))
        assert app.action_save() is True

    assert read_settings(config.settings_path)["plugins"]["demo"]["feeds"] == text
    app = ConfigApp(config)
    async with app.run_test() as pilot:
        await _until(pilot, lambda: bool(app._saved))
        feeds = app.query_one(f"#{app._ids['plugins.demo.feeds']}", TextArea)
        assert feeds.text == text


async def test_cfg_times_field_and_exclusive_switches(tmp_path: Path) -> None:
    config = Config(_root(tmp_path))
    app = ConfigApp(config)
    async with app.run_test() as pilot:
        await _until(pilot, lambda: bool(app._saved))
        field = app._widget_of("autopost.times")
        assert app.query_one(f"#{app._ids['autopost.schedule']}", Switch).value is False

        app._set("autopost.schedule", True)
        await _until(pilot, lambda: app._get("autopost.rotation") is False)
        await _until(pilot, lambda: bool(app._errors["autopost"]))
        assert app.action_save() is False

        field.query_one(".add", Button).press()
        field.query_one(".add", Button).press()
        field.query_one(".add", Button).press()
        await _until(
            pilot, lambda: app._get("autopost.times") == ["09:00", "10:00", "11:00"]
        )
        field.query(".del").first(Button).press()
        await _until(pilot, lambda: app._get("autopost.times") == ["10:00", "11:00"])
        await _until(pilot, lambda: not app._errors["autopost"])
        assert app.action_save() is True

    assert read_settings(config.settings_path)["autopost"] == {
        "schedule": True,
        "times": ["10:00", "11:00"],
    }
