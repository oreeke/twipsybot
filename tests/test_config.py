from __future__ import annotations

from typing import Any

import pytest
from conftest import WriteConfig

from twipsybot import MisskeyBot
from twipsybot.shared.config_keys import ConfigKeys
from twipsybot.shared.exceptions import (
    ConfigurationError,
)


def test_invalid_config_fails_fast(write_config: WriteConfig) -> None:
    config = write_config(load=False, openai={"temperature": 5.0})

    with pytest.raises(ConfigurationError):
        config.load()


@pytest.mark.parametrize(
    "unknown_config",
    [
        {"unexpected": True},
        {"bot": {"response": {"caht": False}}},
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
        ("chat_memory", -1, "chat memory must be between 0 and 100"),
        ("chat_memory", 101, "chat memory must be between 0 and 100"),
        ("chat_context_tokens", -1, "chat context tokens must be >= 0"),
    ],
)
def test_chat_context_limits_reject_out_of_range_values(
    write_config: WriteConfig, field: str, value: int, message: str
) -> None:
    with pytest.raises(ConfigurationError, match=message):
        write_config(bot={"response": {field: value}})


def test_environment_overrides_yaml_config(
    monkeypatch: pytest.MonkeyPatch,
    write_config: WriteConfig,
) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "model-from-env")
    monkeypatch.setenv("BOT_RESPONSE_CHAT_CONTEXT_TOKENS", "2000")
    monkeypatch.setenv("BOT_RESPONSE_RATE_LIMIT", "3")
    monkeypatch.setenv("BOT_TIMELINE_GLOBAL", "true")
    monkeypatch.setenv("DB_CLEAR", "30")

    config = write_config(
        openai={"model": "model-from-yaml"},
        bot={"timeline": {"global": False}},
    )

    assert config.get(ConfigKeys.OPENAI_MODEL) == "model-from-env"
    assert config.get(ConfigKeys.BOT_RESPONSE_CHAT_CONTEXT_TOKENS) == 2000
    assert config.get(ConfigKeys.BOT_RESPONSE_RATE_LIMIT) == 3
    assert config.get(ConfigKeys.BOT_TIMELINE_GLOBAL) is True
    assert config.data["bot"]["timeline"]["global"] is True
    assert "global_" not in config.data["bot"]["timeline"]
    assert config.get(ConfigKeys.DB_CLEAR) == 30


def test_environment_chat_memory_rejects_values_above_misskey_limit(
    monkeypatch: pytest.MonkeyPatch, write_config: WriteConfig
) -> None:
    monkeypatch.setenv("BOT_RESPONSE_CHAT_MEMORY", "101")

    with pytest.raises(
        ConfigurationError, match="chat memory must be between 0 and 100"
    ):
        write_config()


def test_timeline_channels_are_independently_enabled(write_config: WriteConfig) -> None:
    config = write_config(bot={"timeline": {"home": True, "local": False}})
    bot = MisskeyBot(config)

    assert bot.connect._timeline_channels == {"homeTimeline"}


def test_legacy_auto_post_interval_field_is_rejected(
    write_config: WriteConfig,
) -> None:
    config = write_config(load=False, bot={"auto_post": {"interval_minutes": 180}})

    with pytest.raises(ConfigurationError, match="interval_minutes"):
        config.load()


@pytest.mark.parametrize("interval", (180, "180", "3h"))
def test_auto_post_interval_accepts_minutes_and_units(
    write_config: WriteConfig, interval: int | str
) -> None:
    config = write_config(bot={"auto_post": {"interval": interval}})

    assert config.get(ConfigKeys.BOT_AUTO_POST_INTERVAL).total_seconds() == 10800


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (30, 30),
        ("30", 30),
        ("-1", -1),
        ("off", -1),
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
    config = write_config(bot={"response": {"rate_limit": value}})

    assert config.get(ConfigKeys.BOT_RESPONSE_RATE_LIMIT) == expected


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
    ),
)
def test_response_limit_rejects_invalid_duration(
    write_config: WriteConfig, value: Any
) -> None:
    with pytest.raises(ConfigurationError, match="limits must use"):
        write_config(bot={"response": {"rate_limit": value}})
