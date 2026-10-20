from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from twipsybot import Config, Neuro


class WriteConfig(Protocol):
    def __call__(self, *, load: bool = True, **overrides: Any) -> Config: ...


class MakeBot(Protocol):
    async def __call__(
        self, config: Config, *, plugins_dir: Path | None = None
    ) -> Neuro: ...


class MakePluginDir(Protocol):
    def __call__(
        self,
        name: str,
        source: str | None = None,
        *,
        body: str = "",
        config: str = "enabled: true\n",
    ) -> Path: ...
