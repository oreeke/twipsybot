from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from types import NoneType, UnionType
from typing import Any, Literal, TypeGuard, Union, get_args, get_origin

import yaml
from pydantic import (
    BaseModel,
    ByteSize,
    ConfigDict,
    SecretStr,
    ValidationError,
    create_model,
)
from pydantic_core import to_jsonable_python

from ..plugin.base import LineText, PluginBase
from ..shared.config import (
    SECONDS,
    ClockTime,
    Secrets,
    SecretsFile,
    Settings,
    needs_restart,
    parse_clock,
    parse_seconds,
)
from ..shared.settings import deep_merge, field_key, get_dotted, prune, set_dotted

__all__ = ("Kind", "Section", "Spec", "build_sections", "overlay", "to_widget")

Kind = Literal[
    "bool",
    "choice",
    "text",
    "value",
    "seconds",
    "secret",
    "multiline",
    "list",
    "lines",
    "yaml",
    "times",
]
_MULTILINE_HINTS = ("prompt", "prefix")
File = Literal["settings", "secrets"]


@dataclass(frozen=True, slots=True)
class Spec:
    path: tuple[str, ...]
    label: str
    kind: Kind
    default: Any
    choices: tuple[str, ...] = ()
    optional: bool = False
    restart: bool = False
    group: str = ""
    hint: str = ""
    file: File = "settings"

    @property
    def key(self) -> str:
        return ".".join(self.path)

    def parse(self, value: Any) -> Any:
        match self.kind:
            case "bool":
                return bool(value)
            case "choice":
                return value
            case "times":
                return sorted(value)
            case "list":
                return [s.strip() for s in str(value).splitlines() if s.strip()]
            case "lines":
                return str(value).rstrip() if str(value).strip() else None
            case "yaml" | "value" | "seconds":
                return yaml.safe_load(value) if str(value).strip() else None
        return None if self.optional and not str(value).strip() else value


@dataclass(frozen=True, slots=True, eq=False)
class Section:
    id: str
    title: str
    path: tuple[str, ...]
    models: tuple[type[BaseModel], ...]
    specs: tuple[Spec, ...]
    plugin: bool = False

    @property
    def key(self) -> str:
        return ".".join(self.path)

    @property
    def files(self) -> tuple[File, ...]:
        return tuple(sorted({spec.file for spec in self.specs}))

    def _rel(self, spec: Spec) -> str:
        return ".".join(spec.path[len(self.path) :])

    def read(self, raws: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        for file in self.files:
            raw = raws[file]
            value = deepcopy(
                dict((get_dotted(raw, self.key) if self.path else raw) or {})
            )
            for spec in self.specs:
                if spec.file != file:
                    set_dotted(value, self._rel(spec), None)
            merged = deep_merge(merged, value)
        return merged

    def write(
        self, file: File, raw: dict[str, Any], pruned: Mapping[str, Any]
    ) -> dict[str, Any]:
        mine: dict[str, Any] = {}
        for spec in self.specs:
            if spec.file == file:
                rel = self._rel(spec)
                set_dotted(mine, rel, get_dotted(pruned, rel))
        if self.path:
            set_dotted(raw, self.key, mine or None)
            return raw
        owned = {spec.path[0] for spec in self.specs}
        return {k: v for k, v in raw.items() if k not in owned} | mine

    def errors(self, raw: Mapping[str, Any]) -> dict[str, str]:
        found: dict[str, str] = {}
        rel = {spec.path[len(self.path) :]: spec.key for spec in self.specs}
        for model in self.models:
            try:
                model.model_validate(raw)
            except ValidationError as e:
                for err in e.errors():
                    loc = tuple(str(p) for p in err["loc"])
                    key = next(
                        (
                            rel[loc[:i]]
                            for i in range(len(loc), 0, -1)
                            if loc[:i] in rel
                        ),
                        "",
                    )
                    found.setdefault(key, err["msg"].removeprefix("Value error, "))
        return found

    def prune(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for model in self.models:
            out |= prune(model, raw)
        return out


def _unwrap(annotation: Any) -> tuple[Any, bool]:
    args = get_args(annotation)
    if get_origin(annotation) in (Union, UnionType) and NoneType in args:
        rest = [a for a in args if a is not NoneType]
        return (rest[0] if len(rest) == 1 else annotation), True
    return annotation, False


def _is_model(annotation: Any) -> TypeGuard[type[BaseModel]]:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def _choices(annotation: Any) -> tuple[str, ...] | None:
    if get_origin(annotation) is Literal:
        return tuple(str(a) for a in get_args(annotation))
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return tuple(str(m.value) for m in annotation)
    return None


def _classify(name: str, annotation: Any, default: Any) -> tuple[Kind, tuple[str, ...]]:
    origin, args = get_origin(annotation), get_args(annotation)
    if annotation is bool:
        return "bool", ()
    if (choices := _choices(annotation)) is not None:
        return "choice", choices
    if annotation is SecretStr:
        return "secret", ()
    if origin is list and args == (ClockTime,):
        return "times", ()
    if origin in (list, tuple) and all(a is str or a is Ellipsis for a in args):
        return "list", ()
    if origin in (list, tuple, dict) or annotation in (list, tuple, dict):
        return "yaml", ()
    if annotation is str:
        multiline = any(h in name for h in _MULTILINE_HINTS) or "\n" in str(default)
        return ("multiline" if multiline else "text"), ()
    return "value", ()


def _specs(
    model: type[BaseModel],
    path: tuple[str, ...],
    root: int,
    restart: Callable[[str], bool],
    file: File | None = None,
) -> Iterable[Spec]:
    for name, field in model.model_fields.items():
        sub_path = (*path, field_key(name, field))
        annotation, optional = _unwrap(field.annotation)
        if _is_model(annotation):
            yield from _specs(annotation, sub_path, root, restart, file)
            continue
        default = field.get_default(call_default_factory=True)
        if LineText in field.metadata:
            kind, choices = "lines", ()
        elif SECONDS in field.metadata:
            kind, choices = "seconds", ()
        else:
            kind, choices = _classify(name, annotation, default)
        yield Spec(
            path=sub_path,
            label=sub_path[-1].replace("_", " "),
            kind=kind,
            default=to_widget(kind, default),
            choices=choices,
            optional=optional,
            restart=restart(".".join(sub_path)),
            group=" ".join(sub_path[root:-1]).replace("_", " "),
            hint=field.description or "",
            file=file or ("secrets" if kind == "secret" else "settings"),
        )


def _plugin_meta(plugin: type[PluginBase]) -> type[BaseModel]:
    return create_model(
        f"{plugin.__name__}Meta",
        __config__=ConfigDict(extra="ignore", strict=True),
        enabled=(bool, False),
        priority=(int, plugin.priority),
    )


def build_sections(plugins: Mapping[str, type[PluginBase]]) -> list[Section]:
    secrets = tuple(_specs(Secrets, (), 0, lambda _: True, "secrets"))
    sections = [Section("connect", "connect", (), (SecretsFile,), secrets)]
    for name, field in Settings.model_fields.items():
        if _is_model(model := field.annotation):
            specs = tuple(_specs(model, (name,), 1, needs_restart))
            sections.append(Section(name, name, (name,), (model,), specs))
    for name in sorted(plugins):
        plugin, path = plugins[name], ("plugins", name)
        models = (_plugin_meta(plugin), plugin.config_class)
        specs = tuple(s for m in models for s in _specs(m, path, 2, lambda _: False))
        sections.append(Section(f"plugins.{name}", name, path, models, specs, True))
    return sections


def _duration(value: timedelta) -> str:
    seconds, text = int(value.total_seconds()), ""
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        count, seconds = divmod(seconds, size)
        text += f"{count}{unit}" if count else ""
    return text or "0s"


def _clocks(values: Iterable[Any]) -> list[str]:
    clocks: list[str] = []
    for value in values:
        with suppress(ValueError):
            clocks.append(parse_clock(value))
    return clocks


def _list_text(value: Any) -> str:
    items = value.split(",") if isinstance(value, str) else value
    return "\n".join(s for v in items if (s := str(v).strip()))


def _yaml_text(value: Any) -> str:
    plain = to_jsonable_python(value)
    if not plain:
        return ""
    return yaml.safe_dump(plain, allow_unicode=True, sort_keys=False).strip()


def _lines_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if all(isinstance(v, str) for v in value):
        return "\n".join(value)
    return _yaml_text(value)


def _seconds_text(value: Any) -> str:
    with suppress(ValueError):
        seconds = parse_seconds(value)
        return "-1" if seconds < 0 else _duration(timedelta(seconds=seconds))
    return str(value)


def _plain_text(value: Any) -> str:
    match value:
        case timedelta():
            return _duration(value)
        case ByteSize():
            return value.human_readable()
        case SecretStr():
            return value.get_secret_value()
        case Enum():
            return str(value.value)
    return str(value)


_TEXT_CONVERTERS: dict[Kind, Callable[[Any], str]] = {
    "list": _list_text,
    "lines": _lines_text,
    "seconds": _seconds_text,
    "yaml": _yaml_text,
}


def to_widget(kind: Kind, value: Any) -> Any:
    if kind == "bool":
        return (
            value.strip().lower() == "true" if isinstance(value, str) else bool(value)
        )
    if kind == "times":
        return _clocks(value or ())
    if value is None:
        return None if kind == "choice" else ""
    return _TEXT_CONVERTERS.get(kind, _plain_text)(value)


def overlay(
    section: Section,
    raws: Mapping[str, Mapping[str, Any]],
    values: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    merged = section.read(raws)
    errors: dict[str, str] = {}
    root = len(section.path)
    for spec in section.specs:
        try:
            parsed = spec.parse(values[spec.key])
        except yaml.YAMLError:
            errors[spec.key] = "invalid YAML"
            continue
        set_dotted(merged, ".".join(spec.path[root:]), parsed)
    return merged, errors
