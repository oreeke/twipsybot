import os
import re
from itertools import chain
from pathlib import Path

from rich.highlighter import Highlighter
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.validation import Integer
from textual.widgets import Input, Label, Log, Static

from ..shared.logs import LOG_LINE

__all__ = ("LogTail", "LogsPage")

_MIN_LINES, _DEFAULT_LINES, _MAX_LINES = 50, 200, 5000
_CHUNK = 64 * 1024
_RELOAD_BYTES = 1024 * 1024
_POLL_SECONDS = 0.5
_LEVELS = {
    "TRACE": "log-tail--dim",
    "DEBUG": "log-tail--dim",
    "SUCCESS": "log-tail--success",
    "WARNING": "log-tail--warning",
    "ERROR": "log-tail--error",
    "CRITICAL": "log-tail--error",
}


class _LevelHighlighter(Highlighter):
    def __init__(self, log: "LogTail"):
        self._log = log

    def highlight(self, text: Text) -> None:
        style = self._log.get_component_rich_style
        dim = style("log-tail--dim")
        if match := LOG_LINE.match(text.plain):
            text.stylize(dim, 0, match.end(1))
            if level := _LEVELS.get(match[2]):
                text.stylize(style(level), match.start(2))
        else:
            text.stylize(dim)
        if pattern := self._log.pattern:
            hit = style("log-tail--match")
            for found in pattern.finditer(text.plain):
                text.stylize(hit, *found.span())


class LogTail(Log):
    COMPONENT_CLASSES = {*_LEVELS.values(), "log-tail--match"}

    def __init__(self, path: Path):
        super().__init__(highlight=True, max_lines=_DEFAULT_LINES)
        self.highlighter = _LevelHighlighter(self)
        self.path = path
        self.pattern: re.Pattern[str] | None = None
        self._keyword = ""
        self._hit: int | None = None
        self._limit = _DEFAULT_LINES
        self._ino = -1
        self._offset = 0

    def on_mount(self) -> None:
        self._timer = self.set_interval(_POLL_SECONDS, self._poll, pause=True)

    def on_show(self) -> None:
        self._poll()
        self._timer.resume()

    def on_hide(self) -> None:
        self._timer.pause()

    def set_limit(self, count: int) -> None:
        self.max_lines = self._limit = count
        self._load()

    def search(self, keyword: str) -> None:
        if (keyword := keyword.strip()) != self._keyword:
            self._keyword, self._hit = keyword, None
            self.pattern = (
                re.compile(re.escape(keyword), re.IGNORECASE) if keyword else None
            )
            self.refresh_lines(0, len(self.lines))
        if not (pattern := self.pattern):
            return
        lines = self.lines
        start = len(lines) if self._hit is None else self._hit
        order = chain(range(start - 1, -1, -1), range(len(lines) - 1, start - 1, -1))
        if (hit := next((i for i in order if pattern.search(lines[i])), None)) is None:
            self.notify("no match", severity="warning")
            return
        self._hit = hit
        middle = self.scrollable_content_region.height // 2
        self.scroll_to(y=max(0, hit - middle), animate=False, immediate=True)

    def _load(self, st: os.stat_result | None = None) -> None:
        self.clear()
        self._hit = None
        try:
            st = st or self.path.stat()
            with self.path.open("rb") as f:
                pos, data = st.st_size, b""
                while pos and data.count(b"\n") <= self._limit:
                    step = min(_CHUNK, pos)
                    pos -= step
                    f.seek(pos)
                    data = f.read(step) + data
        except OSError:
            self._ino = -1
            return
        end = data.rfind(b"\n") + 1
        self._ino, self._offset = st.st_ino, pos + end
        lines = data[:end].decode(errors="replace").splitlines()
        self.write_lines(lines[-self._limit :])
        self.call_after_refresh(self.scroll_end, animate=False)

    def _poll(self) -> None:
        try:
            st = self.path.stat()
        except OSError:
            return
        if self._ino < 0:
            return self._load(st)
        start = self._offset
        if st.st_ino != self._ino or st.st_size < start:
            self._ino, start = st.st_ino, 0
        if st.st_size - start > _RELOAD_BYTES:
            return self._load(st)
        if st.st_size == start:
            self._offset = start
            return
        try:
            with self.path.open("rb") as f:
                f.seek(start)
                data = f.read()
        except OSError:
            return
        end = data.rfind(b"\n") + 1
        self._offset = start + end
        if not end:
            return
        lines = data[:end].decode(errors="replace").splitlines()
        follow = self.is_vertical_scroll_end
        dropped = max(0, len(self.lines) + len(lines) - self._limit)
        self.write_lines(lines)
        if self._hit is not None:
            self._hit = self._hit - dropped if self._hit >= dropped else None
        if follow:
            self.call_after_refresh(self.scroll_end, animate=False)
        elif dropped:
            self.scroll_to(y=self.scroll_y - dropped, animate=False, immediate=True)


class LogsPage(Vertical):
    def __init__(
        self, path: Path, *, id: str | None = None, classes: str | None = None
    ):
        super().__init__(id=id, classes=classes)
        self.path = path

    def compose(self) -> ComposeResult:
        with Horizontal(classes="title"):
            yield Static(
                f"[b]logs[/]  [$text-muted]{self.path.name}[/]", classes="name"
            )
            yield Label("lines")
            yield Input(
                str(_DEFAULT_LINES),
                type="integer",
                validators=[Integer(_MIN_LINES, _MAX_LINES)],
                compact=True,
                id="log-lines",
            )
            yield Label("search")
            yield Input(placeholder="keyword", compact=True, id="log-search")
        yield LogTail(self.path)

    @on(Input.Submitted, "#log-lines")
    def _set_limit(self, event: Input.Submitted) -> None:
        event.stop()
        if (result := event.validation_result) and result.is_valid:
            self.query_one(LogTail).set_limit(int(event.value))

    @on(Input.Submitted, "#log-search")
    def _search(self, event: Input.Submitted) -> None:
        event.stop()
        self.query_one(LogTail).search(event.value)
