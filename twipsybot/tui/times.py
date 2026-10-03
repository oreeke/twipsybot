from collections.abc import Iterable

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, Select, Static

from ..shared.config import POST_MAX_TIMES

__all__ = ("TimesField",)

_HOURS = [(f"{h:02d}", f"{h:02d}") for h in range(24)]
_MINUTES = [(f"{m:02d}", f"{m:02d}") for m in range(60)]
_STEP_MINUTES = 60


class _TimeRow(Horizontal):
    def __init__(self, value: str) -> None:
        super().__init__(classes="time-row")
        self.value = value

    def compose(self) -> ComposeResult:
        hour, minute = self.value.split(":")
        yield Select(_HOURS, value=hour, allow_blank=False, compact=True, classes="hh")
        yield Static(":", classes="sep")
        yield Select(
            _MINUTES, value=minute, allow_blank=False, compact=True, classes="mm"
        )
        yield Button("−", compact=True, classes="del")

    @on(Select.Changed)
    def _changed(self, event: Select.Changed) -> None:
        event.stop()
        hour, minute = self.value.split(":")
        if event.select.has_class("hh"):
            hour = str(event.value)
        else:
            minute = str(event.value)
        if (value := f"{hour}:{minute}") != self.value:
            self.value = value
            self.post_message(TimesField.Edited())


class TimesField(Vertical):
    class Edited(Message):
        pass

    class Changed(Message):
        def __init__(self, field: "TimesField") -> None:
            super().__init__()
            self.field = field

        @property
        def control(self) -> "TimesField":
            return self.field

    def __init__(self, *, id: str) -> None:
        super().__init__(id=id)
        self._rows: list[_TimeRow] = []

    def compose(self) -> ComposeResult:
        yield Vertical(classes="rows")
        yield Button("+", compact=True, classes="add")

    @property
    def value(self) -> list[str]:
        return [row.value for row in self._rows]

    def load(self, values: Iterable[str]) -> None:
        for row in self._rows:
            row.remove()
        self._rows = []
        for value in values:
            self._add(value)
        self._sync()

    def _add(self, value: str) -> None:
        row = _TimeRow(value)
        self._rows.append(row)
        self.query_one(".rows").mount(row)

    def _next(self) -> str:
        if not self._rows:
            return "09:00"
        hour, minute = map(int, self._rows[-1].value.split(":"))
        total = (hour * 60 + minute + _STEP_MINUTES) % 1440
        return f"{total // 60:02d}:{total % 60:02d}"

    def _sync(self) -> None:
        self.query_one(".add", Button).disabled = len(self._rows) >= POST_MAX_TIMES
        self.post_message(self.Changed(self))

    @on(Button.Pressed, ".add")
    def _pressed_add(self, event: Button.Pressed) -> None:
        event.stop()
        self._add(self._next())
        self._sync()

    @on(Button.Pressed, ".del")
    def _pressed_del(self, event: Button.Pressed) -> None:
        event.stop()
        row = event.button.parent
        if isinstance(row, _TimeRow) and row in self._rows:
            self._rows.remove(row)
            row.remove()
            self._sync()

    @on(Edited)
    def _edited(self, event: Edited) -> None:
        event.stop()
        self._sync()
