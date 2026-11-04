from __future__ import annotations

import pytest
from support.factories import plugin_context

from twipsybot.plugin import PluginBase, PluginConfig


def test_plugin_config_is_typed_immutable_and_ignores_framework_fields() -> None:
    class Config(PluginConfig):
        limit: int = 10

    class Plugin(PluginBase):
        config_class = Config
        settings: Config

    config = Plugin(plugin_context({"enabled": True, "limit": "20"})).settings

    assert config.limit == 20
    assert config.model_dump() == {"limit": 20}
    with pytest.raises(ValueError):
        config.limit = 30
