from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from support.tui import config_root, until
from textual.widgets import Button, Input, Switch, TextArea

from twipsybot.shared.config import Config
from twipsybot.shared.settings import read_settings, write_settings
from twipsybot.shared.status import write_status
from twipsybot.tui import ConfigApp


async def test_cfg_validates_and_saves_minimal_diff(tmp_path: Path) -> None:
    config = Config(config_root(tmp_path))
    app = ConfigApp(config)
    async with app.run_test() as pilot:
        await until(pilot, lambda: bool(app._saved))

        def widget(key: str) -> Any:
            return app.query_one(f"#{app._ids[key]}")

        assert isinstance(widget("reply.memory"), Input)
        assert isinstance(widget("plugins.demo.enabled"), Switch)
        assert isinstance(widget("plugins.demo.tags"), TextArea)
        widget("reply.memory").value = "500"
        await until(pilot, lambda: bool(app._errors["reply"]))
        assert app._errors["reply"] == {
            "reply.memory": "input should be less than or equal to 100"
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
    config = Config(config_root(tmp_path))
    write_settings(
        config.settings_path,
        {"bot": {"model": "custom"}, "plugins": {"demo": {"limit": 5}}},
    )
    app = ConfigApp(config)
    async with app.run_test() as pilot:
        await until(pilot, lambda: bool(app._saved))
        model = app.query_one(f"#{app._ids['bot.model']}", Input)
        limit = app.query_one(f"#{app._ids['plugins.demo.limit']}", Input)
        assert (model.value, limit.value) == ("custom", "5")

        model.value = "gpt-6-luna"
        limit.value = ""
        status = app.query_one("#status")
        await until(pilot, lambda: "2 unsaved" in str(status.render()))
        assert app.action_save() is True

    assert read_settings(config.settings_path) == {}


async def test_cfg_connect_writes_secrets_and_locks_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MISSKEY_INSTANCE_URL", raising=False)
    monkeypatch.delenv("MISSKEY_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("AI_API_KEY", "from-env")
    config = Config(config_root(tmp_path))
    app = ConfigApp(config)
    async with app.run_test() as pilot:
        await until(pilot, lambda: bool(app._saved))
        key = app.query_one(f"#{app._ids['ai_api_key']}", Input)
        assert key.disabled
        assert key.value == "from-env"

        app.query_one(f"#{app._ids['misskey_url']}", Input).value = "https://m.example"
        app.query_one(f"#{app._ids['misskey_token']}", Input).value = "tok"
        env = app.query_one("#env")
        await until(pilot, lambda: "m.example" in str(env.render()))
        assert app.action_save() is True

    assert read_settings(config.secrets_path) == {
        "misskey_url": "https://m.example",
        "misskey_token": "tok",
    }
    assert not config.settings_path.exists()


async def test_cfg_shows_bot_and_stream_status(tmp_path: Path) -> None:
    config = Config(config_root(tmp_path))
    config.status_path.parent.mkdir(parents=True)
    app = ConfigApp(config)
    async with app.run_test() as pilot:
        await until(pilot, lambda: bool(app._saved))
        assert app._connect_text().count("[$error]●[/]") == 2
        write_status(config.status_path, "running", "reconnecting")
        app._poll_status()
        text = app._connect_text()
        assert "[$success]●[/] bot" in text
        assert "[$warning]●[/] stream" in text
        write_status(config.status_path, "waiting", "disconnected")
        app._poll_status()
        text = app._connect_text()
        assert "[$warning]●[/] bot" in text
        assert "[$error]●[/] stream" in text


async def test_cfg_saves_plugin_secrets_to_secrets_file(tmp_path: Path) -> None:
    config = Config(config_root(tmp_path))
    write_settings(
        config.settings_path, {"plugins": {"demo": {"token": "stale", "limit": 5}}}
    )
    write_settings(
        config.secrets_path,
        {"misskey_url": "https://m.example", "plugins": {"other": {"token": "keep"}}},
    )
    app = ConfigApp(config)
    async with app.run_test() as pilot:
        await until(pilot, lambda: bool(app._saved))
        token = app.query_one(f"#{app._ids['plugins.demo.token']}", Input)
        assert token.value == ""

        token.value = "s3cret"
        app.query_one(f"#{app._ids['plugins.demo.greeting']}", Input).value = "yo"
        await until(pilot, lambda: app._is_dirty("plugins.demo.token"))
        assert app.action_save() is True

    assert read_settings(config.settings_path) == {
        "plugins": {"demo": {"limit": 5, "greeting": "yo"}}
    }
    assert read_settings(config.secrets_path) == {
        "misskey_url": "https://m.example",
        "plugins": {"other": {"token": "keep"}, "demo": {"token": "s3cret"}},
    }


async def test_cfg_line_fields_keep_comments(tmp_path: Path) -> None:
    config = Config(config_root(tmp_path))
    text = "# news\nhttps://a.example/rss\n\nhttps://b.example/rss#top"
    app = ConfigApp(config)
    async with app.run_test() as pilot:
        await until(pilot, lambda: bool(app._saved))
        feeds = app.query_one(f"#{app._ids['plugins.demo.feeds']}", TextArea)
        feeds.load_text(text)
        await until(pilot, lambda: app._is_dirty("plugins.demo.feeds"))
        assert app.action_save() is True

    assert read_settings(config.settings_path)["plugins"]["demo"]["feeds"] == text
    app = ConfigApp(config)
    async with app.run_test() as pilot:
        await until(pilot, lambda: bool(app._saved))
        feeds = app.query_one(f"#{app._ids['plugins.demo.feeds']}", TextArea)
        assert feeds.text == text


async def test_cfg_drops_unknown_settings_on_save(tmp_path: Path) -> None:
    config = Config(config_root(tmp_path))
    write_settings(
        config.settings_path,
        {
            "autopost": {"rotation": True, "prompt": "p"},
            "plugins": {"demo": {"limit": 5, "old": 1}},
        },
    )
    app = ConfigApp(config)
    async with app.run_test() as pilot:
        await until(pilot, lambda: bool(app._saved))
        assert app._unknown == {
            "autopost": ["autopost.rotation"],
            "plugins.demo": ["plugins.demo.old"],
        }
        assert not any(app._errors.values())
        assert app.action_save() is True
        assert app._unknown == {}

    assert read_settings(config.settings_path) == {
        "autopost": {"prompt": "p"},
        "plugins": {"demo": {"limit": 5}},
    }


async def test_cfg_drops_unknown_connect_keys_on_save(tmp_path: Path) -> None:
    config = Config(config_root(tmp_path))
    write_settings(
        config.secrets_path,
        {
            "misskey_url": "https://m.example",
            "openai_api_key": "old",
            "plugins": {"other": {"token": "keep"}},
        },
    )
    app = ConfigApp(config)
    async with app.run_test() as pilot:
        await until(pilot, lambda: bool(app._saved))
        assert app._unknown == {"connect": ["openai_api_key"]}
        app.query_one(f"#{app._ids['ai_api_key']}", Input).value = "new"
        await until(pilot, lambda: app._is_dirty("ai_api_key"))
        assert app.action_save() is True
        assert app._unknown == {}

    assert read_settings(config.secrets_path) == {
        "misskey_url": "https://m.example",
        "ai_api_key": "new",
        "plugins": {"other": {"token": "keep"}},
    }


async def test_cfg_resets_invalid_choice_on_save(tmp_path: Path) -> None:
    config = Config(config_root(tmp_path))
    write_settings(config.settings_path, {"autopost": {"mode": "both", "prompt": "p"}})
    app = ConfigApp(config)
    async with app.run_test() as pilot:
        await until(pilot, lambda: bool(app._saved))
        assert app._get("autopost.mode") == "off"
        assert app._is_dirty("autopost.mode")
        assert app.action_save() is True

    assert read_settings(config.settings_path) == {"autopost": {"prompt": "p"}}


async def test_cfg_times_field_and_mode_visibility(tmp_path: Path) -> None:
    config = Config(config_root(tmp_path))
    app = ConfigApp(config)
    async with app.run_test() as pilot:
        await until(pilot, lambda: bool(app._saved))
        field = app._widget_of("autopost.times")
        row = app.query_one(f"#r{app._ids['autopost.times']}")
        assert app._get("autopost.mode") == "off"
        assert row.display is False

        app._set("autopost.mode", "schedule")
        await until(pilot, lambda: row.display is True)
        await until(pilot, lambda: bool(app._errors["autopost"]))
        assert app.action_save() is False

        field.query_one(".add", Button).press()
        field.query_one(".add", Button).press()
        field.query_one(".add", Button).press()
        await until(
            pilot, lambda: app._get("autopost.times") == ["09:00", "10:00", "11:00"]
        )
        field.query(".del").first(Button).press()
        await until(pilot, lambda: app._get("autopost.times") == ["10:00", "11:00"])
        await until(pilot, lambda: not app._errors["autopost"])
        assert app.action_save() is True

    assert read_settings(config.settings_path)["autopost"] == {
        "mode": "schedule",
        "times": ["10:00", "11:00"],
    }
