from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import pytest
from support.tui import config_root
from textual.content import Content
from textual.pilot import Pilot
from textual.widgets import Static, TextArea

from twipsybot.shared.config import Config
from twipsybot.tui import ConfigApp


class Session:
    def __init__(self, app: ConfigApp, pilot: Pilot[None]):
        self.app = app
        self.pilot = pilot

    def field(self, key: str) -> Any:
        return self.app.query_one(f"#{self.app._ids[key]}")

    def row(self, key: str) -> Any:
        return self.app.query_one(f"#r{self.field(key).id}")

    def error(self, key: str) -> str:
        return self.text(f"#e{self.field(key).id}")

    def text(self, selector: str) -> str:
        return str(self.app.query_one(selector, Static).render())

    @property
    def status(self) -> str:
        return self.text("#status").rsplit("  ·  ", 1)[0]

    @property
    def lights(self) -> list[str]:
        content = self.app.query_one("#env", Static).render()
        assert isinstance(content, Content)
        return [
            str(span.style)
            for span in content.spans
            if content.plain[span.start : span.end] == "●"
        ]

    async def until(self, condition: Callable[[], bool]) -> None:
        async with asyncio.timeout(5):
            while not condition():
                await asyncio.sleep(0.01)

    async def edit(self, key: str, value: Any) -> None:
        widget, before = self.field(key), self.status
        if isinstance(widget, TextArea):
            widget.load_text(value)
        else:
            widget.value = value
        await self.until(lambda: self.status != before)

    async def save(self) -> None:
        await self.app.run_action("save")
        await self.pilot.pause()


@asynccontextmanager
async def _open_app(config: Config) -> AsyncIterator[Session]:
    app = ConfigApp(config)
    async with app.run_test() as pilot:
        session = Session(app, pilot)
        await session.until(lambda: bool(session.status))
        yield session


@pytest.fixture
def config(tmp_path) -> Config:
    return Config(config_root(tmp_path))


@pytest.fixture
def open_app() -> Callable[[Config], AbstractAsyncContextManager[Session]]:
    return _open_app
