from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from support.factories import set_plugin_config
from support.types import MakeBot, MakePluginDir, WriteConfig

from twipsybot.plugin import (
    PluginBase,
)
from twipsybot.shared.errors import ConfigurationError


async def test_sync_hook_is_rejected_at_load(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir(
        "sync",
        body="def on_message(self, event):\n    return self.handled('invalid')\n\n",
    )

    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    assert bot.plugin_manager.plugins.get("sync") is None


@pytest.mark.parametrize(
    ("name", "method"),
    [
        ("missingevent", "async def on_message(self):\n        pass"),
        (
            "extrarequired",
            "async def initialize(self, required):\n        return True",
        ),
        (
            "missingcontent",
            "async def on_auto_post_published(self):\n        pass",
        ),
    ],
)
async def test_incompatible_plugin_signature_is_rejected_at_load(
    name: str,
    method: str,
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
) -> None:
    class_name = "".join(part.capitalize() for part in name.split("_"))
    plugins_dir = make_plugin_dir(
        name,
        "from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase\n\n"
        f"class {class_name}Plugin(PluginBase):\n"
        "    api_version = PLUGIN_API_VERSION\n"
        f"    {method}\n\n"
        f"plugin = {class_name}Plugin\n",
    )

    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    assert bot.plugin_manager.plugins.get(name) is None


@pytest.mark.parametrize(
    ("name", "api_version"),
    [("boolean", "True"), ("float", "4.0"), ("legacy", "3"), ("future", "5")],
)
async def test_incompatible_plugin_api_is_rejected(
    name: str,
    api_version: str,
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
) -> None:
    plugins_dir = make_plugin_dir(
        name,
        "from twipsybot.plugin import PluginBase\n\n\n"
        "class Plugin(PluginBase):\n"
        f"    api_version = {api_version}\n\n"
        "plugin = Plugin\n",
    )

    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    assert bot.plugin_manager.plugins.get(name) is None


async def test_plugin_module_uses_explicit_export(
    tmp_path: Path, make_bot: MakeBot, write_config: WriteConfig
) -> None:
    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / "echo"
    plugin_dir.mkdir(parents=True)
    set_plugin_config(tmp_path, "echo", {"enabled": True})
    (plugin_dir / "plugin.py").write_text(
        "from twipsybot.plugin import PluginBase\n\n"
        "class Reply(PluginBase):\n"
        "    api_version = 4\n"
        "    async def on_message(self, event):\n"
        "        return self.handled('explicit')\n\n"
        "plugin = Reply\n",
        encoding="utf-8",
    )

    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    plugin = bot.plugin_manager.plugins.get("echo")
    assert plugin is not None
    assert plugin.__class__.__name__ == "Reply"


async def test_plugin_module_requires_explicit_export(
    tmp_path: Path, make_bot: MakeBot, write_config: WriteConfig
) -> None:
    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / "implicit"
    plugin_dir.mkdir(parents=True)
    set_plugin_config(tmp_path, "implicit", {"enabled": True})
    (plugin_dir / "plugin.py").write_text(
        "from twipsybot.plugin import PluginBase\n\n"
        "class ImplicitPlugin(PluginBase):\n"
        "    api_version = 4\n",
        encoding="utf-8",
    )

    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    assert bot.plugin_manager.plugins.get("implicit") is None


async def test_failed_plugin_import_removes_partial_module(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    module_prefix = "_twipsybot_plugin_broken_import_"
    previous_modules = {name for name in sys.modules if name.startswith(module_prefix)}
    plugins_dir = make_plugin_dir(
        "broken_import",
        "from twipsybot.plugin import PluginBase\n\n"
        "class BrokenImportPlugin(PluginBase):\n"
        "    api_version = 4\n\n"
        "plugin = BrokenImportPlugin\n"
        "raise RuntimeError('broken import')\n",
    )

    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    assert bot.plugin_manager.plugins.get("broken_import") is None
    assert {name for name in sys.modules if name.startswith(module_prefix)} == (
        previous_modules
    )


async def test_plugin_directory_supports_relative_imports(
    tmp_path: Path, make_bot: MakeBot, write_config: WriteConfig
) -> None:
    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / "multi_file"
    plugin_dir.mkdir(parents=True)
    set_plugin_config(tmp_path, "multi_file", {"enabled": True})
    (plugin_dir / "helper.py").write_text("VALUE = 'ok'\n", encoding="utf-8")
    (plugin_dir / "plugin.py").write_text(
        "from twipsybot.plugin import PluginBase\n"
        "from .helper import VALUE\n\n"
        "class MultiFilePlugin(PluginBase):\n"
        "    api_version = 4\n"
        "    value = VALUE\n\n"
        "plugin = MultiFilePlugin\n",
        encoding="utf-8",
    )

    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    plugin = bot.plugin_manager.plugins.get("multi_file")
    assert plugin is not None
    assert plugin.value == "ok"  # type: ignore[attr-defined]


async def test_failed_plugin_import_removes_loaded_submodules(
    make_bot: MakeBot, write_config: WriteConfig, tmp_path: Path
) -> None:
    module_prefix = "_twipsybot_plugin_broken_sub_"
    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / "broken_sub"
    plugin_dir.mkdir(parents=True)
    set_plugin_config(tmp_path, "broken_sub", {"enabled": True})
    (plugin_dir / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    (plugin_dir / "plugin.py").write_text(
        "from .helper import VALUE\nraise RuntimeError('broken import')\n",
        encoding="utf-8",
    )

    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    assert bot.plugin_manager.plugins.get("broken_sub") is None
    assert not [name for name in sys.modules if name.startswith(module_prefix)]


async def test_entry_point_plugin_uses_central_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    make_bot: MakeBot,
    write_config: WriteConfig,
) -> None:
    import twipsybot.plugin.manager as manager_module

    class ExternalPlugin(PluginBase):
        api_version = 4

    entry_point = SimpleNamespace(name="external", load=lambda: ExternalPlugin)
    monkeypatch.setattr(
        manager_module,
        "entry_points",
        lambda *, group: [entry_point] if group == "twipsybot.plugins" else [],
    )
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    set_plugin_config(tmp_path, "external", {"enabled": True})

    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    plugin = bot.plugin_manager.plugins.get("external")
    assert plugin is not None
    assert plugin.context.name == "external"


async def test_unconfigured_entry_point_is_not_loaded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    make_bot: MakeBot,
    write_config: WriteConfig,
) -> None:
    import twipsybot.plugin.manager as manager_module

    load = AsyncMock()
    entry_point = SimpleNamespace(name="external", load=load)
    monkeypatch.setattr(manager_module, "entry_points", lambda *, group: [entry_point])

    bot = await make_bot(write_config(), plugins_dir=tmp_path / "plugins")

    assert bot.plugin_manager.plugins.get("external") is None
    load.assert_not_called()


def test_invalid_plugin_entry_is_rejected(write_config: WriteConfig) -> None:
    config = write_config(load=False, plugins={"invalid_config": True})

    with pytest.raises(ConfigurationError, match="plugins.invalid_config"):
        config.load()


async def test_local_plugin_shadows_entry_point(
    monkeypatch: pytest.MonkeyPatch,
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
) -> None:
    import twipsybot.plugin.manager as manager_module

    load = AsyncMock()
    entry_point = SimpleNamespace(name="echo", load=load)
    monkeypatch.setattr(manager_module, "entry_points", lambda *, group: [entry_point])
    plugins_dir = make_plugin_dir("echo")

    bot = await make_bot(write_config(), plugins_dir=plugins_dir)

    assert bot.plugin_manager.plugins.get("echo") is not None
    load.assert_not_called()
