from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest
from rich.style import Style
from textual.widgets import Button, Input, OptionList, Switch, TextArea

from twipsybot.plugin.manager import discover_plugin_classes
from twipsybot.shared.config import Config
from twipsybot.shared.settings import read_settings, write_settings
from twipsybot.tui import ConfigApp, logs
from twipsybot.tui.logs import LogTail
from twipsybot.tui.schema import build_sections

_DEMO_PLUGIN = """\
from typing import Annotated

from pydantic import Field, SecretStr

from twipsybot.plugin import LineText, PLUGIN_API_VERSION, PluginBase, PluginConfig


class _Config(PluginConfig):
    greeting: str = "hi"
    token: SecretStr = SecretStr("")
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
    assert sections["connect"].files == ("secrets",)
    assert sections["plugins.iincho"].files == ("secrets", "settings")
    assert sections["bot"].files == ("settings",)
    assert specs["plugins.iincho.moderation.cf_api_token"].file == "secrets"
    assert specs["plugins.iincho.moderation.cf_account_id"].file == "secrets"
    assert specs["plugins.iincho.min_notes"].file == "settings"
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
    assert specs["plugins.keyact.priority"].default == "850"
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

        model.value = "gpt-6-luna"
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


async def test_cfg_saves_plugin_secrets_to_secrets_file(tmp_path: Path) -> None:
    config = Config(_root(tmp_path))
    write_settings(
        config.settings_path, {"plugins": {"demo": {"token": "stale", "limit": 5}}}
    )
    write_settings(
        config.secrets_path,
        {"misskey_url": "https://m.example", "plugins": {"other": {"token": "keep"}}},
    )
    app = ConfigApp(config)
    async with app.run_test() as pilot:
        await _until(pilot, lambda: bool(app._saved))
        token = app.query_one(f"#{app._ids['plugins.demo.token']}", Input)
        assert token.value == ""

        token.value = "s3cret"
        app.query_one(f"#{app._ids['plugins.demo.greeting']}", Input).value = "yo"
        await _until(pilot, lambda: app._is_dirty("plugins.demo.token"))
        assert app.action_save() is True

    assert read_settings(config.settings_path) == {
        "plugins": {"demo": {"limit": 5, "greeting": "yo"}}
    }
    assert read_settings(config.secrets_path) == {
        "misskey_url": "https://m.example",
        "plugins": {"other": {"token": "keep"}, "demo": {"token": "s3cret"}},
    }


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


def _log_line(i: int, level: str = "INFO", pad: int = 0) -> str:
    return f"2026-10-04 03:00:00.000 | {level: <8} | line {i}{'.' * pad}\n"


def _numbers(lines: Sequence[str]) -> list[int]:
    return [int(line.split("line ")[1].rstrip(".")) for line in lines]


def _log_config(tmp_path: Path, count: int, pad: int = 0) -> Config:
    config = Config(_root(tmp_path))
    config.log_path.parent.mkdir(parents=True)
    text = "".join(_log_line(i, pad=pad) for i in range(count))
    config.log_path.write_text(text, "utf-8")
    return config


def _append(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(text)


async def _show(app: ConfigApp, pilot: Any, page: str) -> None:
    nav = app.query_one("#nav", OptionList)
    nav.highlighted = nav.get_option_index(page)
    logs_page = app.query_one("#logs")
    await _until(pilot, lambda: logs_page.display is (page == "logs"))
    await pilot.pause()


async def test_ops_logs_tails_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _log_config(tmp_path, 400, pad=300)
    path = config.log_path
    app = ConfigApp(config)
    async with app.run_test() as pilot:
        await _until(pilot, lambda: bool(app._saved))
        ids = [o.id for o in app.query_one("#nav", OptionList).options if o.id]
        assert ids.index(app._pages["system"]) < ids.index("logs")
        assert ids.index("logs") < ids.index(app._pages["plugins.demo"])
        tail = app.query_one(LogTail)
        await _show(app, pilot, "logs")
        await _until(pilot, lambda: 0 < tail.max_scroll_y == tail.scroll_y)
        assert _numbers(tail.lines) == list(range(200, 400))

        tail.scroll_to(y=50, animate=False, immediate=True)
        top = tail.lines[50]
        _append(path, "".join(_log_line(i) for i in range(400, 420)))
        tail._poll()
        assert tail.lines[int(tail.scroll_y)] == top
        tail.scroll_end(animate=False, immediate=True)

        _append(path, _log_line(420, "ERROR") + "2026-10-04 partial")
        tail._poll()
        assert tail.lines[-1].endswith("line 420")
        _append(path, " done\n")
        tail._poll()
        assert tail.lines[-1] == "2026-10-04 partial done"

        path.unlink()
        path.write_text(_log_line(0, "WARNING"), "utf-8")
        tail._poll()
        assert tail.lines[-1].endswith("line 0")
        assert len(tail.lines) == 200

        await _show(app, pilot, app._pages["connect"])
        _append(path, "".join(_log_line(i) for i in range(1, 101)))
        await pilot.pause(0.8)
        assert tail.lines[-1].endswith("line 0")
        await _show(app, pilot, "logs")
        await _until(pilot, lambda: tail.lines[-1].endswith("line 100"))
        assert _numbers(tail.lines[-101:]) == list(range(101))

        monkeypatch.setattr(logs, "_RELOAD_BYTES", 4096)
        tail.scroll_to(y=50, animate=False, immediate=True)
        await _show(app, pilot, app._pages["connect"])
        _append(path, "".join(_log_line(i) for i in range(101, 401)))
        await _show(app, pilot, "logs")
        await _until(pilot, lambda: tail.lines[-1].endswith("line 400"))
        assert _numbers(tail.lines) == list(range(201, 401))
        await _until(pilot, lambda: 0 < tail.max_scroll_y == tail.scroll_y)


async def test_ops_logs_lines_and_search(tmp_path: Path) -> None:
    app = ConfigApp(_log_config(tmp_path, 1000))
    async with app.run_test() as pilot:
        await _until(pilot, lambda: bool(app._saved))
        tail = app.query_one(LogTail)
        await _show(app, pilot, "logs")
        await _until(pilot, lambda: len(tail.lines) == 200)

        search = app.query_one("#log-search", Input)
        await pilot.press("slash")
        assert app.focused is search
        await pilot.press("a", "slash", "b")
        assert search.value == "a/b"

        count = app.query_one("#log-lines", Input)
        count.focus()
        count.value = "10"
        await pilot.press("enter")
        assert len(tail.lines) == 200
        count.value = "600"
        await pilot.press("enter")
        await _until(pilot, lambda: len(tail.lines) == 600)
        assert _numbers(tail.lines) == list(range(400, 1000))

        search.focus()
        search.value = "LINE 99"
        hits = []
        for _ in range(11):
            await pilot.press("enter")
            hits.append(tail._hit)
        assert hits == [*range(599, 589, -1), 599]
        assert tail.scroll_y > 0
        strip = tail._render_line_strip(599, Style())
        assert any(s.text == "line 99" and s.style and s.style.bold for s in strip)

        search.value = "nothing"
        await pilot.press("enter")
        assert tail._hit is None
        search.value = ""
        await pilot.press("enter")
        assert tail.pattern is None
