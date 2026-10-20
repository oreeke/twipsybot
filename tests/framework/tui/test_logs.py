from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from rich.style import Style
from support.tui import config_root, until
from textual.widgets import Input, OptionList

from twipsybot.shared.config import Config
from twipsybot.tui import ConfigApp, logs
from twipsybot.tui.logs import LogTail


def _log_line(i: int, level: str = "INFO", pad: int = 0) -> str:
    return f"2026-10-04 03:00:00.000 | {level: <8} | line {i}{'.' * pad}\n"


def _numbers(lines: Sequence[str]) -> list[int]:
    return [int(line.split("line ")[1].rstrip(".")) for line in lines]


def _log_config(tmp_path: Path, count: int, pad: int = 0) -> Config:
    config = Config(config_root(tmp_path))
    config.log_path.parent.mkdir(parents=True)
    text = "".join(_log_line(i, pad=pad) for i in range(count))
    config.log_path.write_text(text, "utf-8")
    return config


def _append(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(text)


async def _show(app: ConfigApp, pilot: Any, page: str) -> None:
    nav = app.query_one("#nav", OptionList)
    nav.highlighted = nav.get_option_index(page)
    logs_page = app.query_one("#logs")
    await until(pilot, lambda: logs_page.display is (page == "logs"))
    await pilot.pause()


async def test_ops_logs_tails_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _log_config(tmp_path, 400, pad=300)
    path = config.log_path
    app = ConfigApp(config)
    async with app.run_test() as pilot:
        await until(pilot, lambda: bool(app._saved))
        ids = [o.id for o in app.query_one("#nav", OptionList).options if o.id]
        assert ids.index(app._pages["system"]) < ids.index("logs")
        assert ids.index("logs") < ids.index(app._pages["plugins.demo"])
        tail = app.query_one(LogTail)
        await _show(app, pilot, "logs")
        await until(pilot, lambda: 0 < tail.max_scroll_y == tail.scroll_y)
        assert _numbers(tail.lines) == list(range(200, 400))

        tail.scroll_to(y=50, animate=False, immediate=True)
        top = tail.lines[50]
        _append(path, "".join(_log_line(i) for i in range(400, 420)))
        tail._poll()
        assert tail.lines[int(tail.scroll_y)] == top
        tail.scroll_end(animate=False, immediate=True)

        _append(path, _log_line(420, "ERROR") + "2026-10-04 partial")
        tail._poll()
        assert tail.lines[-1].endswith("line 420")
        _append(path, " done\n")
        tail._poll()
        assert tail.lines[-1] == "2026-10-04 partial done"

        path.unlink()
        path.write_text(_log_line(0, "WARNING"), "utf-8")
        tail._poll()
        assert tail.lines[-1].endswith("line 0")
        assert len(tail.lines) == 200

        await _show(app, pilot, app._pages["connect"])
        _append(path, "".join(_log_line(i) for i in range(1, 101)))
        await pilot.pause(0.8)
        assert tail.lines[-1].endswith("line 0")
        await _show(app, pilot, "logs")
        await until(pilot, lambda: tail.lines[-1].endswith("line 100"))
        assert _numbers(tail.lines[-101:]) == list(range(101))

        monkeypatch.setattr(logs, "_RELOAD_BYTES", 4096)
        tail.scroll_to(y=50, animate=False, immediate=True)
        await _show(app, pilot, app._pages["connect"])
        _append(path, "".join(_log_line(i) for i in range(101, 401)))
        await _show(app, pilot, "logs")
        await until(pilot, lambda: tail.lines[-1].endswith("line 400"))
        assert _numbers(tail.lines) == list(range(201, 401))
        await until(pilot, lambda: 0 < tail.max_scroll_y == tail.scroll_y)


async def test_ops_logs_lines_and_search(tmp_path: Path) -> None:
    app = ConfigApp(_log_config(tmp_path, 1000))
    async with app.run_test() as pilot:
        await until(pilot, lambda: bool(app._saved))
        tail = app.query_one(LogTail)
        await _show(app, pilot, "logs")
        await until(pilot, lambda: len(tail.lines) == 200)

        search = app.query_one("#log-search", Input)
        await pilot.press("slash")
        assert app.focused is search
        await pilot.press("a", "slash", "b")
        assert search.value == "a/b"

        count = app.query_one("#log-lines", Input)
        count.focus()
        count.value = "10"
        await pilot.press("enter")
        assert len(tail.lines) == 200
        count.value = "600"
        await pilot.press("enter")
        await until(pilot, lambda: len(tail.lines) == 600)
        assert _numbers(tail.lines) == list(range(400, 1000))

        search.focus()
        search.value = "LINE 99"
        hits = []
        for _ in range(11):
            await pilot.press("enter")
            hits.append(tail._hit)
        assert hits == [*range(599, 589, -1), 599]
        assert tail.scroll_y > 0
        strip = tail._render_line_strip(599, Style())
        assert any(s.text == "line 99" and s.style and s.style.bold for s in strip)

        search.value = "nothing"
        await pilot.press("enter")
        assert tail._hit is None
        search.value = ""
        await pilot.press("enter")
        assert tail.pattern is None
