import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from .exceptions import ConfigurationError

__all__ = (
    "field_key",
    "get_dotted",
    "patch_settings",
    "prune",
    "read_settings",
    "set_dotted",
    "write_settings",
)

_HEADER = "# Managed by `twipsybot cfg`; hand edits are fine.\n"


class _Dumper(yaml.SafeDumper):
    pass


def _represent_str(dumper: yaml.SafeDumper, value: str) -> yaml.Node:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _represent_str)


def get_dotted(data: Mapping[str, Any], dotted: str) -> Any:
    cur: Any = data
    for key in dotted.split("."):
        if not isinstance(cur, Mapping):
            return None
        cur = cur.get(key)
    return cur


def set_dotted(data: dict[str, Any], dotted: str, value: Any) -> None:
    *parents, leaf = dotted.split(".")
    cur = data
    for key in parents:
        if not isinstance(nxt := cur.get(key), dict):
            nxt = cur[key] = {}
        cur = nxt
    if value is None:
        cur.pop(leaf, None)
    else:
        cur[leaf] = value


def field_key(name: str, field: Any) -> str:
    alias = field.validation_alias
    return alias if isinstance(alias, str) else field.alias or name


def read_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
        raise ConfigurationError(f"cannot read {path}: {e}") from e
    if not isinstance(loaded, dict):
        raise ConfigurationError(f"{path} root must be a mapping")
    return loaded


def write_settings(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (
        yaml.dump(dict(data), Dumper=_Dumper, allow_unicode=True, sort_keys=False)
        if data
        else ""
    )
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".settings.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(_HEADER + body)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def prune(model: type[BaseModel], raw: Mapping[str, Any]) -> dict[str, Any]:
    full, base = model.model_validate(raw), model.model_validate({})
    out: dict[str, Any] = {}
    for name, field in type(full).model_fields.items():
        if (key := field_key(name, field)) not in raw:
            continue
        value = raw[key]
        sub = field.annotation
        if isinstance(sub, type) and issubclass(sub, BaseModel):
            if isinstance(value, Mapping) and (value := prune(sub, value)):
                out[key] = value
        elif getattr(full, name) != getattr(base, name):
            out[key] = value
    return out


def patch_settings(
    path: Path, changes: Mapping[str, Any], model: type[BaseModel]
) -> None:
    raw = read_settings(path)
    for dotted, value in changes.items():
        set_dotted(raw, dotted, value)
    try:
        pruned = prune(model, raw)
    except ValidationError as e:
        raise ConfigurationError(str(e)) from e
    write_settings(path, pruned)
