from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from twipsybot.shared.settings import read_settings, write_settings


def set_plugin_config(root: Path, name: str, config: dict[str, Any] | None) -> None:
    settings_path = root / "data" / "settings.yaml"
    data = read_settings(settings_path)
    plugins = data.setdefault("plugins", {})
    if config is None:
        plugins.pop(name, None)
    else:
        plugins[name] = config
    write_settings(settings_path, data)


def plugin_context(config: dict[str, Any], **services: Any) -> Any:
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
