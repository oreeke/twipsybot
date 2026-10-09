from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from conftest import WriteConfig

from twipsybot import Neuro
from twipsybot.shared.config import Config, Settings, needs_restart
from twipsybot.shared.config_keys import ConfigKeys
from twipsybot.shared.exceptions import ConfigurationError
from twipsybot.shared.settings import (
    patch_settings,
    prune,
    read_settings,
    write_settings,
)


def test_invalid_config_fails_fast(write_config: WriteConfig) -> None:
    config = write_config(load=False, bot={"temperature": 5.0})

    with pytest.raises(ConfigurationError, match="temperature"):
        config.load()


@pytest.mark.parametrize(
    "unknown_config",
    [
        {"unexpected": True},
        {"reply": {"caht": False}},
    ],
)
def test_unknown_config_fields_fail_fast(
    write_config: WriteConfig, unknown_config: dict[str, Any]
) -> None:
    config = write_config(load=False, **unknown_config)

    with pytest.raises(ConfigurationError, match="Extra inputs are not permitted"):
        config.load()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("memory", -1, "greater than or equal to 0"),
        ("memory", 101, "less than or equal to 100"),
        ("ctx_tokens", -1, "greater than or equal to 0"),
    ],
)
def test_chat_context_limits_reject_out_of_range_values(
    write_config: WriteConfig, field: str, value: int, message: str
) -> None:
    with pytest.raises(ConfigurationError, match=message):
        write_config(reply={field: value})


def test_secrets_come_from_file_with_env_override(
    monkeypatch: pytest.MonkeyPatch, write_config: WriteConfig
) -> None:
    config = write_config()
    write_settings(
        config.secrets_path, {"misskey_token": "file-token", "openai_api_key": "k"}
    )
    monkeypatch.delenv("MISSKEY_ACCESS_TOKEN")
    config.load()

    assert config.get(ConfigKeys.MISSKEY_TOKEN) == "file-token"
    assert config.get(ConfigKeys.OPENAI_API_KEY) == "test-key"

    monkeypatch.delenv("OPENAI_API_KEY")
    write_settings(config.secrets_path, {"misskey_token": "file-token"})
    with pytest.raises(ConfigurationError, match="missing openai_api_key"):
        config.load()


def test_plugin_secrets_merge_into_plugin_config(write_config: WriteConfig) -> None:
    config = write_config(plugins={"demo": {"enabled": True, "limit": 5}})
    write_settings(
        config.secrets_path,
        {"plugins": {"demo": {"token": "tok"}, "other": {"token": "t2"}}},
    )
    config.load()

    assert config.get("plugins") == {
        "demo": {"enabled": True, "limit": 5, "token": "tok"},
        "other": {"token": "t2"},
    }


def test_defaults_need_no_settings_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for env in ("MISSKEY_INSTANCE_URL", "MISSKEY_ACCESS_TOKEN", "OPENAI_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    config = Config(tmp_path)
    write_settings(
        config.secrets_path,
        {
            "misskey_url": "https://m.example",
            "misskey_token": "t",
            "openai_api_key": "k",
        },
    )
    config.load()

    assert config.get(ConfigKeys.BOT_MODEL) == "gpt-6-luna"
    assert config.get(ConfigKeys.POST_ROTATION) is False
    assert config.get(ConfigKeys.POST_SCHEDULE) is False
    assert config.get(ConfigKeys.TIMELINE_GLOBAL) is False
    assert config.get("plugins") == {}
    assert config.log_path.parent.is_dir()


def test_reply_limit_defaults(write_config: WriteConfig) -> None:
    config = write_config()

    assert config.get(ConfigKeys.REPLY_RATE_LIMIT_MSG) == "我需要休息一下..."
    assert config.get(ConfigKeys.REPLY_MAX_TURNS_MSG) == "我要回家了..."
    assert config.get(ConfigKeys.REPLY_TURNS_RELEASE) == 3600
    assert config.get(ConfigKeys.BOT_MAX_TOKENS) == 2000
    assert config.get(ConfigKeys.SYSTEM_DB_CLEAR_DAYS) == -1


def test_prompt_files_resolve_from_root(
    tmp_path: Path, write_config: WriteConfig
) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "system.md").write_text("system from file", encoding="utf-8")
    (prompts_dir / "post.txt").write_text("post from file", encoding="utf-8")

    config = write_config(
        bot={"system_prompt": "prompts/system.md"},
        autopost={"prompt": "prompts/post.txt"},
    )

    assert config.get(ConfigKeys.BOT_SYSTEM_PROMPT) == "system from file"
    assert config.get(ConfigKeys.POST_PROMPT) == "post from file"
    fingerprint = config.fingerprint
    (prompts_dir / "post.txt").write_text("post edited", encoding="utf-8")
    assert config.stat() != fingerprint


def test_timeline_channels_are_independently_enabled(write_config: WriteConfig) -> None:
    config = write_config(timeline={"home": True, "local": False})
    bot = Neuro(config)

    assert bot.connect._timeline_channels == {"homeTimeline"}
    assert config.data["timeline"]["global"] is False
    assert "global_" not in config.data["timeline"]


def test_legacy_auto_post_interval_field_is_rejected(
    write_config: WriteConfig,
) -> None:
    config = write_config(load=False, autopost={"interval_minutes": 180})

    with pytest.raises(ConfigurationError, match="interval_minutes"):
        config.load()


@pytest.mark.parametrize("interval", (180, "180", "3h"))
def test_auto_post_interval_accepts_minutes_and_units(
    write_config: WriteConfig, interval: int | str
) -> None:
    config = write_config(autopost={"interval": interval})

    assert config.get(ConfigKeys.POST_INTERVAL).total_seconds() == 10800


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (30, 30),
        ("30", 30),
        ("-1", -1),
        ("off", -1),
        (False, -1),
        ("30s", 30),
        ("5m", 300),
        ("1h", 3600),
        ("1d", 86400),
        ("1h30m", 5400),
        ("1h 30m", 5400),
    ],
)
def test_response_limit_duration_normalization(
    write_config: WriteConfig, value: Any, expected: int
) -> None:
    config = write_config(reply={"rate_limit": value})

    assert config.get(ConfigKeys.REPLY_RATE_LIMIT) == expected


@pytest.mark.parametrize(
    "value",
    (
        "1w",
        "1mm",
        "1y",
        "500ms",
        "1us",
        "1ns",
        "0.5s",
        "1.5m",
        "1h0.5m",
        "1hXXX30m",
        "invalid",
        True,
        -2,
    ),
)
def test_response_limit_rejects_invalid_duration(
    write_config: WriteConfig, value: Any
) -> None:
    with pytest.raises(ConfigurationError, match="limits must use"):
        write_config(reply={"rate_limit": value})


def test_prune_drops_values_equal_to_defaults() -> None:
    raw = {
        "bot": {"model": "gpt-6-luna", "temperature": 1.1},
        "autopost": {"interval": "180", "daily_max": 3},
        "timeline": {"global": False},
        "plugins": {"demo": {"enabled": True}},
    }

    assert prune(Settings, raw) == {
        "bot": {"temperature": 1.1},
        "autopost": {"daily_max": 3},
        "plugins": {"demo": {"enabled": True}},
    }


def test_patch_settings_sets_deletes_and_validates(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    patch_settings(path, {"bot.model": "m1", "reply.chat": False}, Settings)
    patch_settings(path, {"bot.model": None, "bot.system_prompt": "a\nb"}, Settings)

    assert read_settings(path) == {
        "bot": {"system_prompt": "a\nb"},
        "reply": {"chat": False},
    }
    assert "system_prompt: |-" in path.read_text(encoding="utf-8")
    with pytest.raises(ConfigurationError):
        patch_settings(path, {"reply.memory": 500}, Settings)
    assert read_settings(path)["reply"] == {"chat": False}


def test_restart_keys() -> None:
    assert needs_restart(ConfigKeys.OPENAI_BASE_URL)
    assert needs_restart(ConfigKeys.MISSKEY_TOKEN)
    assert needs_restart(ConfigKeys.OPENAI_API_KEY)
    assert needs_restart(ConfigKeys.TIMELINE_ANTENNAS)
    assert not needs_restart(ConfigKeys.BOT_MODEL)
    assert not needs_restart("plugins.keyact")


def test_secret_values_are_hidden_in_errors(write_config: WriteConfig) -> None:
    config = write_config()
    write_settings(config.secrets_path, {"openai_apikey": "sk-SECRET123"})

    with pytest.raises(ConfigurationError) as error:
        config.load()

    assert "openai_apikey" in str(error.value)
    assert "sk-SECRET123" not in str(error.value)


def test_fingerprint_is_taken_before_reading(
    monkeypatch: pytest.MonkeyPatch, write_config: WriteConfig
) -> None:
    import twipsybot.shared.config as module

    config = write_config(load=False)
    read = module.read_settings

    def read_then_edit(path: Path) -> dict[str, Any]:
        data = read(path)
        if path == config.settings_path:
            write_settings(path, {**data, "reply": {"chat": False}})
        return data

    monkeypatch.setattr(module, "read_settings", read_then_edit)
    config.load()

    assert config.get(ConfigKeys.REPLY_CHAT) is True
    assert config.stat() != config.fingerprint


def test_post_times_are_normalized_and_sorted(write_config: WriteConfig) -> None:
    config = write_config(
        autopost={"rotation": False, "schedule": True, "times": ["21:15", 510, "7:05"]}
    )

    assert config.get(ConfigKeys.POST_TIMES) == ["07:05", "08:30", "21:15"]


@pytest.mark.parametrize(
    ("post", "message"),
    [
        ({"times": ["23:58", "00:02"]}, "at least 5m apart"),
        ({"times": [f"{h:02d}:00" for h in range(24)] + ["12:30"]}, "at most 24"),
        ({"times": ["24:00"]}, "HH:MM"),
        ({"rotation": True, "schedule": True, "times": ["09:00"]}, "cannot both"),
        ({"rotation": False, "schedule": True}, "at least one time"),
        ({"interval": "4m"}, "at least 5m"),
    ],
)
def test_post_schedule_rules(
    write_config: WriteConfig, post: dict[str, Any], message: str
) -> None:
    with pytest.raises(ConfigurationError, match=message):
        write_config(autopost=post)
