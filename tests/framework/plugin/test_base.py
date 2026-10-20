from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from twipsybot.plugin import (
    PluginBase,
    PluginConfig,
)


def _context(config: dict[str, Any], **services: Any) -> Any:
    defaults = {
        "name": "Test",
        "config": config,
        "storage": SimpleNamespace(),
        "misskey": SimpleNamespace(),
        "openai": SimpleNamespace(),
        "bot": SimpleNamespace(),
    }
    defaults.update(services)
    return SimpleNamespace(**defaults)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, False),
        (True, True),
        (False, False),
        ("true", True),
        ("false", False),
    ],
)
def test_parse_bool(value: Any, expected: bool) -> None:
    assert PluginBase._parse_bool(value, False) is expected


@pytest.mark.parametrize("value", ("", "maybe", 0, 1, [], {}))
def test_parse_bool_rejects_invalid_values(value: Any) -> None:
    with pytest.raises(ValueError, match="invalid boolean value"):
        PluginBase._parse_bool(value, False)


def test_plugin_config_is_typed_immutable_and_ignores_framework_fields() -> None:
    class Config(PluginConfig):
        limit: int = 10

    class Plugin(PluginBase):
        config_class = Config
        settings: Config

    config = Plugin(_context({"enabled": True, "limit": "20"})).settings

    assert config.limit == 20
    assert config.model_dump() == {"limit": 20}
    with pytest.raises(ValueError):
        config.limit = 30
