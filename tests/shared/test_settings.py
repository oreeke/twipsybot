from __future__ import annotations

from pathlib import Path

import pytest

from twipsybot.shared.config import Settings
from twipsybot.shared.errors import ConfigurationError
from twipsybot.shared.settings import (
    patch_settings,
    prune,
    read_settings,
)


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
