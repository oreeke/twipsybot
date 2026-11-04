from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from support.factories import set_plugin_config
from support.types import MakeBot, MakePluginDir, WriteConfig

import twipsybot.plugin.manager as manager_module
from twipsybot.plugin import PluginBase
from twipsybot.shared.errors import ConfigurationError

_EXPORT_SOURCE = (
    "from twipsybot.plugin import PluginBase\n"
    "from .helper import VALUE\n\n"
    "class Reply(PluginBase):\n"
    "    value = VALUE\n\n"
    "plugin = Reply\n"
)


@pytest.fixture
def entry_points(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    def register(*points: SimpleNamespace) -> None:
        monkeypatch.setattr(
            manager_module,
            "entry_points",
            lambda *, group: list(points) if group == "twipsybot.plugins" else [],
        )

    return register


@pytest.mark.parametrize(
    ("name", "body"),
    [
        ("synchook", "def on_message(self, event):\n    return None"),
        ("missingevent", "async def on_message(self):\n    pass"),
        ("extrarequired", "async def initialize(self, required):\n    return True"),
        ("missingcontent", "async def on_auto_post_published(self):\n    pass"),
    ],
)
async def test_incompatible_hook_is_rejected_at_load(
    name: str, body: str, boot
) -> None:
    bot = await boot(**{name: body})

    assert name not in bot.plugin_manager.plugins


async def test_plugin_directory_loads_explicit_export_with_relative_imports(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir("multi_file", _EXPORT_SOURCE)
    (plugins_dir / "multi_file" / "helper.py").write_text(
        "VALUE = 'ok'\n", encoding="utf-8"
    )

    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    plugin = bot.plugin_manager.plugins["multi_file"]
    assert type(plugin).__name__ == "Reply"
    assert plugin.value == "ok"  # type: ignore[attr-defined]
    assert type(plugin).__module__ != "plugins.multi_file.plugin"


async def test_plugin_module_requires_explicit_export(
    tmp_path: Path, make_bot: MakeBot, write_config: WriteConfig
) -> None:
    plugin_dir = tmp_path / "plugins" / "implicit"
    plugin_dir.mkdir(parents=True)
    set_plugin_config(tmp_path, "implicit", {"enabled": True})
    (plugin_dir / "plugin.py").write_text(
        "from twipsybot.plugin import PluginBase\n\n"
        "class ImplicitPlugin(PluginBase):\n"
        "    pass\n",
        encoding="utf-8",
    )

    bot = await make_bot(write_config(), plugins_dir=tmp_path / "plugins")

    assert "implicit" not in bot.plugin_manager.plugins


@pytest.mark.parametrize(
    ("enabled", "loaded"),
    [
        (True, True),
        (" True ", True),
        ("false", False),
        (None, False),
        ("maybe", False),
        (1, False),
    ],
)
async def test_enabled_flag_controls_plugin_loading(
    enabled: Any,
    loaded: bool,
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
) -> None:
    plugins_dir = make_plugin_dir("toggle")
    set_plugin_config(plugins_dir.parent, "toggle", {"enabled": enabled})

    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    assert ("toggle" in bot.plugin_manager.plugins) is loaded


async def test_failed_plugin_import_removes_loaded_modules(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir(
        "broken_import",
        _EXPORT_SOURCE + "raise RuntimeError('broken import')\n",
    )
    (plugins_dir / "broken_import" / "helper.py").write_text(
        "VALUE = 1\n", encoding="utf-8"
    )

    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    assert "broken_import" not in bot.plugin_manager.plugins
    assert not [
        n for n in sys.modules if n.startswith("_twipsybot_plugin_broken_import_")
    ]


async def test_entry_point_plugin_uses_central_config(
    tmp_path: Path,
    entry_points: Callable[..., None],
    make_bot: MakeBot,
    write_config: WriteConfig,
) -> None:
    class ExternalPlugin(PluginBase):
        pass

    entry_points(SimpleNamespace(name="external", load=lambda: ExternalPlugin))
    set_plugin_config(tmp_path, "external", {"enabled": True})

    bot = await make_bot(write_config(), plugins_dir=tmp_path / "plugins")

    assert bot.plugin_manager.plugins["external"].context.name == "external"


async def test_unconfigured_entry_point_is_not_loaded(
    tmp_path: Path,
    entry_points: Callable[..., None],
    make_bot: MakeBot,
    write_config: WriteConfig,
) -> None:
    load = Mock()
    entry_points(SimpleNamespace(name="external", load=load))

    bot = await make_bot(write_config(), plugins_dir=tmp_path / "plugins")

    assert "external" not in bot.plugin_manager.plugins
    load.assert_not_called()


async def test_local_plugin_shadows_entry_point(
    entry_points: Callable[..., None],
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
) -> None:
    load = Mock()
    entry_points(SimpleNamespace(name="echo", load=load))
    plugins_dir = make_plugin_dir("echo")

    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    assert "echo" in bot.plugin_manager.plugins
    load.assert_not_called()


def test_invalid_plugin_entry_is_rejected(write_config: WriteConfig) -> None:
    config = write_config(load=False, plugins={"invalid_config": True})

    with pytest.raises(ConfigurationError, match="plugins.invalid_config"):
        config.load()
