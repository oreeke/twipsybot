import os
from collections.abc import Iterable
from contextlib import suppress
from typing import Any
from urllib.parse import urlsplit

from pydantic import ValidationError
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.markup import escape
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import (
    Button,
    ContentSwitcher,
    Footer,
    Input,
    Label,
    OptionList,
    Select,
    Static,
    Switch,
    TextArea,
)
from textual.widgets.option_list import Option

from ..plugin.manager import discover_plugin_classes
from ..shared.config import EXCLUSIVE, SECRETS, Config
from ..shared.exceptions import ConfigurationError
from ..shared.settings import get_dotted, read_settings, set_dotted, write_settings
from .schema import Section, Spec, build_sections, overlay, to_widget
from .times import TimesField

__all__ = ("ConfigApp", "run")

_BLURBS = {
    "connect": "instance · api",
    "bot": "persona · admins · model",
    "timeline": "streams to watch",
    "autopost": "rotation · schedule",
    "reply": "replies · limits",
    "system": "logs · storage",
}


_CHANGES = (
    Input.Changed,
    Switch.Changed,
    Select.Changed,
    TextArea.Changed,
    TimesField.Changed,
)


class Confirm(ModalScreen[str]):
    BINDINGS = [Binding("escape", "dismiss('cancel')", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Unsaved changes", id="dialog-title")
            with Horizontal(id="dialog-buttons"):
                yield Button("Save", variant="primary", id="save", compact=True)
                yield Button("Discard", variant="error", id="discard", compact=True)
                yield Button("Cancel", id="cancel", compact=True)

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id or "cancel")


class ConfigApp(App[None]):
    CSS_PATH = "app.tcss"
    TITLE = "twipsybot cfg"
    BINDINGS = [
        Binding("ctrl+s", "save", "Save"),
        Binding("ctrl+r", "reload", "Reload"),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        plugins, self._plugin_errors = discover_plugin_classes(config.root / "plugins")
        self.sections = build_sections(plugins)
        self._pages = {s.id: f"p{i}" for i, s in enumerate(self.sections)}
        self._specs = {spec.key: spec for s in self.sections for spec in s.specs}
        self._owner = {spec.key: s for s in self.sections for spec in s.specs}
        self._ids = {key: f"f{i}" for i, key in enumerate(self._specs)}
        self._keys = {wid: key for key, wid in self._ids.items()}
        self._files = {"settings": config.settings_path, "secrets": config.secrets_path}
        self._env = {f: e for f, e in SECRETS.items() if os.environ.get(e, "").strip()}
        self._raw: dict[str, dict[str, Any]] = {}
        self._saved: dict[str, Any] = {}
        self._errors: dict[str, dict[str, str]] = {}

    def compose(self) -> ComposeResult:
        with Horizontal(id="top"):
            yield Static("[b]twipsybot[/] [dim]cfg[/]", id="brand")
            yield Static(id="env")
        with Horizontal(id="body"):
            yield OptionList(*self._nav(), id="nav")
            with ContentSwitcher(initial=self._pages[self.sections[0].id], id="pages"):
                for section in self.sections:
                    yield from self._page(section)
        yield Static(id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.theme = "tokyo-night"
        self.call_after_refresh(self._start)

    def _start(self) -> None:
        self._load()
        self._goto(self.sections[0])
        for name, error in self._plugin_errors.items():
            self.notify(
                f"{name}: {error}",
                title="plugin skipped",
                severity="warning",
                markup=False,
            )

    def _nav(self) -> Iterable[Option | None]:
        plugins = False
        yield Option("[b dim]CORE[/]", disabled=True)
        for section in self.sections:
            if section.plugin and not plugins:
                plugins = True
                yield None
                yield Option("[b dim]PLUGINS[/]", disabled=True)
            yield Option(self._nav_text(section), id=self._pages[section.id])

    def _page(self, section: Section) -> ComposeResult:
        page = self._pages[section.id]
        blurb = "plugin" if section.plugin else _BLURBS.get(section.id, "")
        with VerticalScroll(id=page, classes="page"):
            yield Static(
                f"[b]{section.title}[/]  [$text-muted]{blurb}[/]", classes="title"
            )
            yield Static(classes="section-error", id=f"{page}-err")
            group = ""
            for spec in section.specs:
                if spec.group != group and (group := spec.group):
                    yield Static(group, classes="group")
                yield from self._row(spec)

    def _row(self, spec: Spec) -> ComposeResult:
        wid = self._ids[spec.key]
        with Horizontal(classes="row"):
            yield Label(
                self._label_text(spec, dirty=False), id=f"l{wid}", classes="label"
            )
            with Vertical(classes="cell"):
                yield self._widget(spec, wid)
                yield Static(id=f"e{wid}", classes="error")

    @staticmethod
    def _widget(spec: Spec, wid: str) -> Widget:
        match spec.kind:
            case "bool":
                return Switch(id=wid, animate=False)
            case "choice":
                options = [(c, c) for c in spec.choices]
                return Select(
                    options, allow_blank=spec.optional, prompt="—", id=wid, compact=True
                )
            case "multiline" | "list" | "lines" | "yaml":
                placeholder = {
                    "list": "one per line",
                    "lines": f"{spec.hint or 'one per line'}  · # comment",
                    "yaml": "yaml",
                }.get(spec.kind, spec.hint)
                return TextArea(
                    id=wid, classes=spec.kind, compact=True, placeholder=placeholder
                )
            case "secret":
                return Input(id=wid, password=True, compact=True)
            case "times":
                return TimesField(id=wid)
        return Input(id=wid, placeholder=str(spec.default), compact=True)

    def _label_text(self, spec: Spec, *, dirty: bool) -> str:
        mark = "[$warning]●[/] " if dirty else "  "
        tail = " [$warning]↻[/]" if spec.restart else ""
        tail += " [dim]env[/]" if spec.key in self._env else ""
        return f"{mark}{spec.label}{tail}"

    def _nav_text(self, section: Section) -> str:
        ready = bool(self._saved)
        dot = " "
        if section.plugin:
            on = ready and self._get(f"{section.key}.enabled")
            dot = "[$success]●[/]" if on else "[dim]○[/]"
        tail = ""
        if self._errors.get(section.id):
            tail = " [$error]![/]"
        elif ready and any(self._is_dirty(s.key) for s in section.specs):
            tail = " [$warning]•[/]"
        return f"{dot} {section.title}{tail}"

    def _connect_text(self) -> str:
        host = urlsplit(self._get("misskey_url").strip()).hostname
        marks = [
            f"{'[$success]✓[/]' if self._get(key).strip() else '[$error]✗[/]'} {label}"
            for key, label in (("misskey_token", "token"), ("openai_api_key", "key"))
        ]
        return "  ".join([escape(host) if host else "[$error]no instance[/]", *marks])

    def _widget_of(self, key: str) -> Widget:
        return self.query_one(f"#{self._ids[key]}")

    def _get(self, key: str) -> Any:
        match widget := self._widget_of(key):
            case Switch() | TimesField():
                return widget.value
            case Select():
                return None if widget.is_blank() else widget.value
            case TextArea():
                return widget.text
            case Input():
                return widget.value
        return None

    def _set(self, key: str, value: Any) -> None:
        match widget := self._widget_of(key):
            case Switch():
                widget.value = bool(value)
            case Select():
                if value in self._specs[key].choices:
                    widget.value = value
                elif self._specs[key].optional:
                    widget.clear()
            case TextArea():
                widget.load_text(value)
            case TimesField():
                widget.load(value)
            case Input():
                widget.value = value

    def _is_dirty(self, key: str) -> bool:
        return self._get(key) != self._saved[key]

    def _load(self) -> None:
        for name, path in self._files.items():
            try:
                self._raw[name] = read_settings(path)
            except ConfigurationError as e:
                self._raw[name] = {}
                self.notify(str(e), title="unreadable", severity="error", markup=False)
        with self.prevent(*_CHANGES):
            for key, spec in self._specs.items():
                raw = get_dotted(self._raw[self._owner[key].file], key)
                value = spec.default if raw is None else to_widget(spec.kind, raw)
                if env := self._env.get(key):
                    value = os.environ[env].strip()
                    self._widget_of(key).disabled = True
                self._set(key, value)
                self._saved[key] = self._get(key)
        for section in self.sections:
            self._validate(section)
            self._refresh(section)

    def _validate(self, section: Section) -> dict[str, str]:
        values = {spec.key: self._get(spec.key) for spec in section.specs}
        merged, errors = overlay(section, self._raw[section.file], values)
        for key, message in section.errors(merged).items():
            errors.setdefault(key, message)
        self._errors[section.id] = errors
        return errors

    def _refresh(self, section: Section) -> None:
        errors = self._errors.get(section.id, {})
        for spec in section.specs:
            wid = self._ids[spec.key]
            label = self.query_one(f"#l{wid}", Label)
            label.update(self._label_text(spec, dirty=self._is_dirty(spec.key)))
            label.set_class(spec.key in errors, "-invalid")
            error = self.query_one(f"#e{wid}", Static)
            error.update(escape(errors.get(spec.key, "")))
            error.display = spec.key in errors
        general = self.query_one(f"#{self._pages[section.id]}-err", Static)
        general.update(escape(errors.get("", "")))
        general.display = "" in errors
        nav = self.query_one("#nav", OptionList)
        nav.replace_option_prompt(self._pages[section.id], self._nav_text(section))
        self._status()

    def _status(self) -> None:
        dirty = [k for k in self._specs if self._is_dirty(k)]
        invalid = sum(len(e) for e in self._errors.values())
        restart = [k for k in dirty if self._specs[k].restart]
        parts = [f"{len(dirty)} unsaved" if dirty else "[dim]no changes[/]"]
        if invalid:
            parts.append(f"[$error]{invalid} invalid[/]")
        if restart:
            parts.append(f"[$warning]↻ restart: {', '.join(restart)}[/]")
        parts.append(f"[dim]{escape(str(self.config.settings_path.parent))}[/]")
        self.query_one("#status", Static).update("  ·  ".join(parts))
        self.query_one("#env", Static).update(self._connect_text())

    @on(Input.Changed)
    @on(Switch.Changed)
    @on(Select.Changed)
    @on(TextArea.Changed)
    @on(TimesField.Changed)
    def _changed(self, event: Any) -> None:
        if not self._saved or (key := self._keys.get(event.control.id or "")) is None:
            return
        section = self._owner[key]
        with suppress(NoMatches):
            if (other := EXCLUSIVE.get(key)) and self._get(key):
                self._set(other, False)
            self._validate(section)
            self._refresh(section)

    @on(OptionList.OptionHighlighted, "#nav")
    def _navigate(self, event: OptionList.OptionHighlighted) -> None:
        if event.option.id:
            self.query_one("#pages", ContentSwitcher).current = event.option.id

    def _goto(self, section: Section) -> None:
        nav = self.query_one("#nav", OptionList)
        nav.highlighted = nav.get_option_index(self._pages[section.id])

    def _merge(
        self, section: Section, raw: dict[str, Any], dirty: set[str]
    ) -> dict[str, Any]:
        current = section.read(raw)
        root = len(section.path)
        for spec in section.specs:
            if spec.key in dirty:
                rel = ".".join(spec.path[root:])
                set_dotted(current, rel, spec.parse(self._get(spec.key)))
        return section.write(raw, section.prune(current))

    def _write(self, dirty: set[str]) -> None:
        sections = {self._owner[k] for k in dirty}
        for name in {s.file for s in sections}:
            raw = read_settings(self._files[name])
            for section in (s for s in sections if s.file == name):
                raw = self._merge(section, raw, dirty)
            if raw.get("plugins") == {}:
                raw.pop("plugins")
            write_settings(self._files[name], raw)
            self._raw[name] = raw

    def action_save(self) -> bool:
        if invalid := [s for s in self.sections if self._validate(s)]:
            for section in invalid:
                self._refresh(section)
            self._goto(invalid[0])
            self.notify("Fix invalid fields first", severity="error")
            return False
        if not (dirty := {k for k in self._specs if self._is_dirty(k)}):
            self.notify("Nothing to save")
            return True
        try:
            self._write(dirty)
        except (OSError, ValidationError, ConfigurationError) as e:
            self.notify(str(e), title="save failed", severity="error", markup=False)
            return False
        for key in dirty:
            self._saved[key] = self._get(key)
        for section in self.sections:
            self._refresh(section)
        restart = sorted(k for k in dirty if self._specs[k].restart)
        message = (
            f"↻ restart to apply {', '.join(restart)}" if restart else "applied live"
        )
        self.notify(message, title=f"saved {len(dirty)} change(s)")
        return True

    def action_reload(self) -> None:
        self._load()
        self.notify("reloaded from disk")

    async def action_quit(self) -> None:
        if not any(self._is_dirty(k) for k in self._specs):
            self.exit()
            return

        def done(choice: str | None) -> None:
            if (choice == "save" and self.action_save()) or choice == "discard":
                self.exit()

        self.push_screen(Confirm(), done)


def run(config: Config) -> None:
    ConfigApp(config).run()
