from __future__ import annotations

import pytest
from textual.widgets import Button

import twipsybot.tui.app as app_module
from twipsybot.shared.settings import read_settings, write_settings
from twipsybot.shared.status import write_status


async def test_save_is_blocked_by_invalid_field_and_writes_minimal_diff(
    config, open_app
) -> None:
    async with open_app(config) as ui:
        await ui.edit("reply.memory", "500")
        assert ui.error("reply.memory") == "input should be less than or equal to 100"
        assert "1 invalid" in ui.status
        await ui.save()
        assert not config.settings_path.exists()

        await ui.edit("reply.memory", "20")
        await ui.edit("plugins.demo.enabled", True)
        await ui.edit("plugins.demo.tags", "a\n\nb\n")
        write_settings(config.settings_path, {"autopost": {"local_only": True}})
        await ui.save()

    assert read_settings(config.settings_path) == {
        "autopost": {"local_only": True},
        "reply": {"memory": 20},
        "plugins": {"demo": {"enabled": True, "tags": ["a", "b"]}},
    }


async def test_values_reset_to_defaults_are_removed_from_settings(
    config, open_app
) -> None:
    write_settings(
        config.settings_path,
        {"bot": {"model": "custom"}, "plugins": {"demo": {"limit": 5}}},
    )
    async with open_app(config) as ui:
        assert (ui.field("bot.model").value, ui.field("plugins.demo.limit").value) == (
            "custom",
            "5",
        )

        await ui.edit("bot.model", "gpt-6-luna")
        await ui.edit("plugins.demo.limit", "")
        assert "2 unsaved" in ui.status
        await ui.save()

    assert read_settings(config.settings_path) == {}


async def test_connect_secrets_are_saved_and_env_values_are_locked(
    config, open_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MISSKEY_INSTANCE_URL", raising=False)
    monkeypatch.delenv("MISSKEY_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("AI_API_KEY", "from-env")
    async with open_app(config) as ui:
        assert ui.field("ai_api_key").disabled
        assert ui.field("ai_api_key").value == "from-env"

        await ui.edit("misskey_instance_url", "https://m.example")
        await ui.edit("misskey_access_token", "tok")
        assert "m.example" in ui.text("#env")
        await ui.save()

    assert read_settings(config.secrets_path) == {
        "misskey_instance_url": "https://m.example",
        "misskey_access_token": "tok",
    }
    assert not config.settings_path.exists()


@pytest.mark.parametrize(
    ("state", "lights"),
    [
        (None, ["$error", "$error"]),
        (("running", "reconnecting"), ["$success", "$warning"]),
        (("waiting", "disconnected"), ["$warning", "$error"]),
    ],
)
async def test_connect_lights_show_bot_and_stream_status(
    state: tuple[str, str] | None, lights: list[str], config, open_app
) -> None:
    if state:
        config.status_path.parent.mkdir(parents=True)
        write_status(config.status_path, *state)

    async with open_app(config) as ui:
        assert ui.lights == lights


async def test_connect_lights_follow_status_changes(
    monkeypatch: pytest.MonkeyPatch, config, open_app
) -> None:
    monkeypatch.setattr(app_module, "_STATUS_POLL_SECONDS", 0.02)
    config.status_path.parent.mkdir(parents=True)
    async with open_app(config) as ui:
        write_status(config.status_path, "running", "connected")

        await ui.until(lambda: ui.lights == ["$success", "$success"])


async def test_plugin_secrets_are_saved_to_secrets_file(config, open_app) -> None:
    write_settings(
        config.settings_path, {"plugins": {"demo": {"token": "stale", "limit": 5}}}
    )
    write_settings(
        config.secrets_path,
        {
            "misskey_instance_url": "https://m.example",
            "plugins": {"other": {"token": "keep"}},
        },
    )
    async with open_app(config) as ui:
        assert ui.field("plugins.demo.token").value == ""

        await ui.edit("plugins.demo.token", "s3cret")
        await ui.edit("plugins.demo.greeting", "yo")
        await ui.save()

    assert read_settings(config.settings_path) == {
        "plugins": {"demo": {"limit": 5, "greeting": "yo"}}
    }
    assert read_settings(config.secrets_path) == {
        "misskey_instance_url": "https://m.example",
        "plugins": {"other": {"token": "keep"}, "demo": {"token": "s3cret"}},
    }


async def test_line_fields_keep_comments_across_sessions(config, open_app) -> None:
    text = "# news\nhttps://a.example/rss\n\nhttps://b.example/rss#top"
    async with open_app(config) as ui:
        await ui.edit("plugins.demo.feeds", text)
        await ui.save()

    assert read_settings(config.settings_path)["plugins"]["demo"]["feeds"] == text
    async with open_app(config) as ui:
        assert ui.field("plugins.demo.feeds").text == text


async def test_unknown_settings_are_reported_and_dropped_on_save(
    config, open_app
) -> None:
    write_settings(
        config.settings_path,
        {
            "autopost": {"rotation": True, "prompt": "p"},
            "plugins": {"demo": {"limit": 5, "old": 1}},
        },
    )
    async with open_app(config) as ui:
        assert "2 unknown" in ui.status
        assert "invalid" not in ui.status
        await ui.save()
        assert "unknown" not in ui.status

    assert read_settings(config.settings_path) == {
        "autopost": {"prompt": "p"},
        "plugins": {"demo": {"limit": 5}},
    }


async def test_unknown_connect_keys_are_dropped_on_save(config, open_app) -> None:
    write_settings(
        config.secrets_path,
        {
            "misskey_instance_url": "https://m.example",
            "openai_api_key": "old",
            "plugins": {"other": {"token": "keep"}},
        },
    )
    async with open_app(config) as ui:
        assert "1 unknown" in ui.status
        await ui.edit("ai_api_key", "new")
        await ui.save()
        assert "unknown" not in ui.status

    assert read_settings(config.secrets_path) == {
        "misskey_instance_url": "https://m.example",
        "ai_api_key": "new",
        "plugins": {"other": {"token": "keep"}},
    }


async def test_invalid_stored_choice_resets_to_default_on_save(
    config, open_app
) -> None:
    write_settings(config.settings_path, {"autopost": {"mode": "both", "prompt": "p"}})
    async with open_app(config) as ui:
        assert ui.field("autopost.mode").value == "off"
        assert "1 unsaved" in ui.status
        await ui.save()

    assert read_settings(config.settings_path) == {"autopost": {"prompt": "p"}}


async def test_schedule_mode_reveals_times_and_requires_one(config, open_app) -> None:
    async with open_app(config) as ui:
        times = ui.field("autopost.times")
        assert ui.row("autopost.times").display is False

        await ui.edit("autopost.mode", "schedule")
        assert ui.row("autopost.times").display is True
        assert "invalid" in ui.status
        await ui.save()
        assert not config.settings_path.exists()

        for _ in range(3):
            times.query_one(".add", Button).press()
        await ui.until(lambda: times.value == ["09:00", "10:00", "11:00"])

        await ui.until(lambda: len(times.query(".del")) == 3)
        times.query(".del").first(Button).press()
        await ui.until(lambda: len(times.query(".del")) == 2)
        await ui.save()

    assert read_settings(config.settings_path)["autopost"] == {
        "mode": "schedule",
        "times": ["10:00", "11:00"],
    }
