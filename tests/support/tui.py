from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

DEMO_PLUGIN = """\
from typing import Annotated

from pydantic import Field, SecretStr

from twipsybot.plugin import LineText, PLUGIN_API_VERSION, PluginBase, PluginConfig


class _Config(PluginConfig):
    greeting: str = "hi"
    token: SecretStr = SecretStr("")
    tags: tuple[str, ...] = ()
    feeds: Annotated[tuple[str, ...], LineText] = ()
    limit: int = Field(3, ge=1)


class Demo(PluginBase):
    api_version = PLUGIN_API_VERSION
    priority = 7
    config_class = _Config


plugin = Demo
"""


async def until(pilot: Any, condition: Callable[[], bool]) -> None:
    for _ in range(100):
        if condition():
            return
        await pilot.pause()
    raise AssertionError("condition not reached")


def config_root(tmp_path: Path) -> Path:
    plugin_dir = tmp_path / "plugins" / "demo"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.py").write_text(DEMO_PLUGIN, encoding="utf-8")
    return tmp_path
