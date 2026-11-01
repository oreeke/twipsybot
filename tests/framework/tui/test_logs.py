from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest
from textual.widgets import Input, OptionList

from twipsybot.shared.config import Config
from twipsybot.tui import logs
from twipsybot.tui.logs import LogTail


def _line(i: int, level: str = "INFO", pad: int = 0) -> str:
    return f"2026-10-04 03:00:00.000 | {level: <8} | line {i}{'.' * pad}\n"


def _lines(start: int, stop: int) -> str:
    return "".join(_line(i) for i in range(start, stop))


def _numbers(lines: Sequence[str]) -> list[int]:
    return [int(line.split("line ")[1].rstrip(".")) for line in lines]


def _seed(config: Config, count: int, pad: int = 0) -> Path:
    config.log_path.parent.mkdir(parents=True)
    config.log_path.write_text(
        "".join(_line(i, pad=pad) for i in range(count)), "utf-8"
    )
    return config.log_path


def _append(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(text)


async def _wait(condition: Callable[[], bool]) -> None:
    async with asyncio.timeout(3):
        while not condition():
            await asyncio.sleep(0.02)


def _last(tail: LogTail) -> str:
    return tail.lines[-1] if tail.lines else ""


async def _show(ui: Any, *, logs_page: bool) -> LogTail:
    nav = ui.app.query_one("#nav", OptionList)
    page = next(o.id for o in nav.options if o.id and (o.id == "logs") is logs_page)
    nav.highlighted = nav.get_option_index(page)
    page_widget = ui.app.query_one("#logs")
    await ui.until(lambda: page_widget.display is logs_page)
    return ui.app.query_one(LogTail)


async def _at_end(tail: LogTail) -> None:
    await _wait(lambda: 0 < tail.max_scroll_y == tail.scroll_y)


@pytest.fixture(autouse=True)
def fast_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(logs, "_POLL_SECONDS", 0.02)


async def test_logs_page_sits_between_core_and_plugin_pages(config, open_app) -> None:
    _seed(config, 1)
    async with open_app(config) as ui:
        nav = ui.app.query_one("#nav", OptionList)
        ids = [o.id for o in nav.options if o.id]

        assert len(ids) == len(ui.app.sections) + 1
        assert ids.index("logs") == sum(not s.plugin for s in ui.app.sections)


async def test_tail_shows_last_lines_scrolled_to_end(config, open_app) -> None:
    _seed(config, 400, pad=300)
    async with open_app(config) as ui:
        tail = await _show(ui, logs_page=True)
        await _at_end(tail)

        assert _numbers(tail.lines) == list(range(200, 400))


async def test_tail_follows_new_lines_when_scrolled_to_end(config, open_app) -> None:
    path = _seed(config, 400)
    async with open_app(config) as ui:
        tail = await _show(ui, logs_page=True)
        await _at_end(tail)

        _append(path, _lines(400, 420))
        await _wait(lambda: _last(tail).endswith("line 419"))

        await _at_end(tail)


async def test_tail_keeps_position_when_scrolled_up(config, open_app) -> None:
    path = _seed(config, 400)
    async with open_app(config) as ui:
        tail = await _show(ui, logs_page=True)
        await _at_end(tail)
        tail.scroll_to(y=50, animate=False, immediate=True)
        top = tail.lines[50]

        _append(path, _lines(400, 420))
        await _wait(lambda: _last(tail).endswith("line 419"))

        assert tail.lines[int(tail.scroll_y)] == top


async def test_tail_waits_for_partial_line_to_complete(config, open_app) -> None:
    path = _seed(config, 1)
    async with open_app(config) as ui:
        tail = await _show(ui, logs_page=True)

        _append(path, _line(1, "ERROR") + "2026-10-04 partial")
        await _wait(lambda: _last(tail).endswith("line 1"))
        _append(path, " done\n")

        await _wait(lambda: _last(tail) == "2026-10-04 partial done")


async def test_tail_reads_replaced_file_and_caps_lines(config, open_app) -> None:
    path = _seed(config, 400)
    async with open_app(config) as ui:
        tail = await _show(ui, logs_page=True)
        await _wait(lambda: len(tail.lines) == 200)

        path.unlink()
        path.write_text(_line(0, "WARNING"), "utf-8")
        await _wait(lambda: _last(tail).endswith("line 0"))

        assert len(tail.lines) == 200


async def test_tail_pauses_while_hidden_and_catches_up_on_show(
    config, open_app
) -> None:
    path = _seed(config, 1)
    async with open_app(config) as ui:
        tail = await _show(ui, logs_page=True)
        await _wait(lambda: _last(tail).endswith("line 0"))
        await _show(ui, logs_page=False)
        await ui.pilot.pause()

        _append(path, _lines(1, 101))
        await asyncio.sleep(0.2)
        assert _last(tail).endswith("line 0")

        await _show(ui, logs_page=True)
        await _wait(lambda: _last(tail).endswith("line 100"))
        assert _numbers(tail.lines) == list(range(101))


async def test_tail_reloads_from_end_after_large_backlog(
    monkeypatch: pytest.MonkeyPatch, config, open_app
) -> None:
    monkeypatch.setattr(logs, "_RELOAD_BYTES", 4096)
    path = _seed(config, 5)
    async with open_app(config) as ui:
        tail = await _show(ui, logs_page=True)
        await _show(ui, logs_page=False)

        _append(path, _lines(5, 305))
        await _show(ui, logs_page=True)

        await _wait(lambda: _last(tail).endswith("line 304"))
        assert _numbers(tail.lines) == list(range(105, 305))
        await _at_end(tail)


async def test_line_limit_applies_only_when_valid(config, open_app) -> None:
    _seed(config, 1000)
    async with open_app(config) as ui:
        tail = await _show(ui, logs_page=True)
        await _wait(lambda: len(tail.lines) == 200)
        count = ui.app.query_one("#log-lines", Input)
        count.focus()

        count.value = "10"
        await ui.pilot.press("enter")
        assert len(tail.lines) == 200

        count.value = "600"
        await ui.pilot.press("enter")
        await _wait(lambda: len(tail.lines) == 600)
        assert _numbers(tail.lines) == list(range(400, 1000))


async def test_slash_focuses_search_and_types_literally(config, open_app) -> None:
    _seed(config, 1)
    async with open_app(config) as ui:
        await _show(ui, logs_page=True)
        search = ui.app.query_one("#log-search", Input)

        await ui.pilot.press("slash")
        assert ui.app.focused is search
        await ui.pilot.press("a", "slash", "b")

        assert search.value == "a/b"


async def test_search_cycles_matches_backwards_and_wraps(config, open_app) -> None:
    _seed(config, 100)
    async with open_app(config) as ui:
        tail = await _show(ui, logs_page=True)
        await _wait(lambda: len(tail.lines) == 100)
        search = ui.app.query_one("#log-search", Input)
        search.focus()
        search.value = "LINE 9"

        hits = []
        for _ in range(12):
            await ui.pilot.press("enter")
            hits.append(tail._hit)

        assert hits == [*range(99, 89, -1), 9, 99]
        assert any(
            seg.text == "line 9" and seg.style and seg.style.bold
            for y in range(tail.scrollable_content_region.height)
            for seg in tail.render_line(y)
        )


async def test_search_without_match_or_keyword_clears_hit(config, open_app) -> None:
    _seed(config, 100)
    async with open_app(config) as ui:
        tail = await _show(ui, logs_page=True)
        await _wait(lambda: len(tail.lines) == 100)
        search = ui.app.query_one("#log-search", Input)
        search.focus()
        search.value = "line 5"
        await ui.pilot.press("enter")
        assert tail._hit is not None

        search.value = "nothing"
        await ui.pilot.press("enter")
        assert tail._hit is None

        search.value = ""
        await ui.pilot.press("enter")
        assert tail.pattern is None
