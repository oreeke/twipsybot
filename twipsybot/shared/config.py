import os
import re
from datetime import timedelta
from itertools import pairwise
from pathlib import Path
from typing import Annotated, Any, Literal, TypeVar

import durationpy
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

from .config_keys import ConfigKeys
from .exceptions import ConfigurationError
from .settings import When, deep_merge, get_dotted, read_settings, set_dotted

__all__ = (
    "POST_MAX_TIMES",
    "POST_MIN_GAP",
    "PROMPT_KEYS",
    "SECONDS",
    "SECRETS",
    "ClockTime",
    "Config",
    "Secrets",
    "SecretsFile",
    "Settings",
    "needs_restart",
    "parse_clock",
    "parse_seconds",
)

SECRETS = {
    "misskey_url": "MISSKEY_INSTANCE_URL",
    "misskey_token": "MISSKEY_ACCESS_TOKEN",
    "openai_base_url": "OPENAI_BASE_URL",
    "openai_api_key": "OPENAI_API_KEY",
}
_REQUIRED_SECRETS = ("misskey_url", "misskey_token", "openai_api_key")
PROMPT_KEYS = (ConfigKeys.BOT_SYSTEM_PROMPT, ConfigKeys.POST_PROMPT)
_RESTART_PREFIXES = ("connect.", "timeline.")
POST_MIN_GAP = timedelta(minutes=5)
POST_MAX_TIMES = 24

_MISSING = object()
_INTERVAL_PATTERN = re.compile(r"(?:\d+(?:\.\d+)?[mhd]\s*)+")
_MINUTES_PATTERN = re.compile(r"\d+(?:\.\d+)?")
_INTERVAL_ERROR = "auto-post interval must use minutes, hours, or days"
_SECONDS_PATTERN = re.compile(r"(?:\d+[smhd]\s*)+")
_SECONDS_ERROR = "limits must use whole seconds, minutes, hours, or days"
_CLOCK_PATTERN = re.compile(r"(\d{1,2}):(\d{2})")
_CLOCK_ERROR = "times must use HH:MM"


def needs_restart(key: str) -> bool:
    return key.startswith(_RESTART_PREFIXES)


def _parse_interval(value: Any) -> timedelta:
    match value:
        case bool():
            raise ValueError(_INTERVAL_ERROR)
        case timedelta():
            interval = value
        case int() | float():
            interval = timedelta(minutes=value)
        case str() if _MINUTES_PATTERN.fullmatch(s := value.strip().lower()):
            interval = timedelta(minutes=float(s))
        case str() if _INTERVAL_PATTERN.fullmatch(s := value.strip().lower()):
            interval = durationpy.from_str(s)
        case _:
            raise ValueError(_INTERVAL_ERROR)
    if interval < POST_MIN_GAP:
        raise ValueError(f"auto-post interval must be at least {_MIN_GAP_TEXT}")
    return interval


def parse_seconds(value: Any) -> int:
    match value:
        case False:
            return -1
        case int() if not isinstance(value, bool) and value >= -1:
            return value
        case str():
            s = value.strip().lower()
            if s in {"-1", "off", "none", "unlimited"}:
                return -1
            if s.isdigit():
                return int(s)
            if _SECONDS_PATTERN.fullmatch(s):
                return int(durationpy.from_str(s).total_seconds())
    raise ValueError(_SECONDS_ERROR)


def parse_clock(value: Any) -> str:
    match value:
        case bool():
            raise ValueError(_CLOCK_ERROR)
        case int():
            hour, minute = divmod(value, 60)
        case str() if match := _CLOCK_PATTERN.fullmatch(value.strip()):
            hour, minute = int(match[1]), int(match[2])
        case _:
            raise ValueError(_CLOCK_ERROR)
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(_CLOCK_ERROR)
    return f"{hour:02d}:{minute:02d}"


def _minutes(clock: str) -> int:
    hour, minute = clock.split(":")
    return int(hour) * 60 + int(minute)


def _blank_to_none(value: Any) -> Any:
    return None if value is None or str(value).strip() == "" else str(value).strip()


Interval = Annotated[timedelta, BeforeValidator(_parse_interval)]
SECONDS = BeforeValidator(parse_seconds)
Seconds = Annotated[int, SECONDS]
OptionalText = Annotated[str | None, BeforeValidator(_blank_to_none)]
ClockTime = Annotated[str, BeforeValidator(parse_clock)]
Visibility = Literal["public", "home", "followers"]
_MIN_GAP_TEXT = f"{POST_MIN_GAP.seconds // 60}m"


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)


class BotConfig(_Section):
    system_prompt: str = "你是一个可爱的AI助手..."
    admins: list[str] = []
    model: str = Field(default="gpt-6-luna", min_length=1)
    api_mode: Literal["auto", "chat", "responses"] = "auto"
    max_tokens: int = Field(default=2000, gt=0)
    temperature: float = Field(default=0.8, ge=0, le=2)
    image_model: OptionalText = None
    image_size: OptionalText = None
    image_quality: OptionalText = None


class TimelineConfig(_Section):
    home: bool = False
    local: bool = False
    hybrid: bool = False
    global_: bool = Field(default=False, alias="global")
    antennas: list[str] = []


class PostConfig(_Section):
    prompt: str = "生成一篇有趣的社交帖子..."
    visibility: Visibility = "public"
    local_only: bool = False
    mode: Literal["off", "rotation", "schedule"] = "off"
    interval: Annotated[Interval, When("mode", "rotation")] = timedelta(hours=3)
    daily_max: Annotated[int, When("mode", "rotation")] = Field(default=8, ge=0)
    times: Annotated[list[ClockTime], When("mode", "schedule")] = []

    @field_validator("times")
    @classmethod
    def _validate_times(cls, value: list[str]) -> list[str]:
        times = sorted(value)
        if len(times) > POST_MAX_TIMES:
            raise ValueError(f"at most {POST_MAX_TIMES} times")
        minutes = [_minutes(t) for t in times]
        if len(minutes) > 1:
            gaps = [b - a for a, b in pairwise(minutes)]
            gaps.append(minutes[0] + 1440 - minutes[-1])
            if min(gaps) < POST_MIN_GAP.seconds // 60:
                raise ValueError(f"times must be at least {_MIN_GAP_TEXT} apart")
        return times

    @model_validator(mode="after")
    def _validate_mode(self) -> "PostConfig":
        if self.mode == "schedule" and not self.times:
            raise ValueError("schedule needs at least one time")
        return self


class ReplyConfig(_Section):
    mention: bool = True
    chat: bool = True
    memory: int = Field(default=10, ge=0, le=100)
    ctx_tokens: int = Field(default=2000, ge=0)
    rate_limit: Seconds = -1
    rate_limit_msg: str = "我需要休息一下..."
    max_turns: int = Field(default=-1, ge=-1)
    max_turns_msg: str = "我要回家了..."
    turns_release: Seconds = 3600
    whitelist: list[str] = []
    blacklist: list[str] = []


class SystemConfig(_Section):
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    dump_events: bool = False
    db_clear_days: int = Field(default=-1, ge=-1)


class Settings(_Section):
    bot: BotConfig = Field(default_factory=BotConfig)
    timeline: TimelineConfig = Field(default_factory=TimelineConfig)
    autopost: PostConfig = Field(default_factory=PostConfig)
    reply: ReplyConfig = Field(default_factory=ReplyConfig)
    system: SystemConfig = Field(default_factory=SystemConfig)
    plugins: dict[str, dict[str, Any]] = {}

    @field_validator("plugins", mode="before")
    @classmethod
    def _plugins_default(cls, value: Any) -> Any:
        return {} if value is None else value


class Secrets(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    misskey_url: str = ""
    misskey_token: SecretStr = SecretStr("")
    openai_base_url: str = ""
    openai_api_key: SecretStr = SecretStr("")

    def resolve(self, field: str, env: str) -> str:
        value = getattr(self, field)
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        return os.environ.get(env, "").strip() or value.strip()


class SecretsFile(Secrets):
    plugins: dict[str, dict[str, Any]] = {}


_M = TypeVar("_M", bound=BaseModel)


def _validate(model: type[_M], raw: dict[str, Any]) -> _M:
    try:
        return model.model_validate(raw)
    except ValidationError as e:
        raise ConfigurationError(str(e)) from e


def _prompt_file(value: str, root: Path) -> Path | None:
    path = Path(value.strip())
    if (
        path.is_absolute()
        or path.suffix.lower() not in {".md", ".txt"}
        or ".." in path.parts
        or path.parts[:1] != ("prompts",)
    ):
        return None
    resolved = (root / path).resolve()
    prompts = (root / "prompts").resolve()
    return resolved if resolved.is_file() and resolved.is_relative_to(prompts) else None


def _stat(path: Path) -> tuple[int, int]:
    try:
        st = path.stat()
    except OSError:
        return 0, 0
    return st.st_mtime_ns, st.st_size


class Config:
    def __init__(self, root: str | Path = "."):
        self.root = Path(root)
        self.settings_path = self.root / "data" / "settings.yaml"
        self.secrets_path = self.root / "data" / "secrets.yaml"
        self.db_path = self.root / "data" / "twipsybot.db"
        self.log_path = self.root / "data" / "logs" / "twipsybot.log"
        self.data: dict[str, Any] = {}
        self.fingerprint: tuple[tuple[int, int], ...] = ()
        self._watched: tuple[Path, ...] = (self.settings_path, self.secrets_path)

    def load(self) -> dict[str, Any]:
        watched = [self.settings_path, self.secrets_path]
        stats = [_stat(path) for path in watched]
        raw = read_settings(self.settings_path)
        for key in PROMPT_KEYS:
            value = get_dotted(raw, key)
            if isinstance(value, str) and (path := _prompt_file(value, self.root)):
                watched.append(path)
                stats.append(_stat(path))
                set_dotted(raw, key, path.read_text(encoding="utf-8").strip())
        data = _validate(Settings, raw).model_dump(by_alias=True)
        secrets = _validate(SecretsFile, read_settings(self.secrets_path))
        data["plugins"] = deep_merge(data["plugins"], secrets.plugins)
        data["connect"] = {f: secrets.resolve(f, env) for f, env in SECRETS.items()}
        if missing := [f for f in _REQUIRED_SECRETS if not data["connect"][f]]:
            raise ConfigurationError(
                f"missing {', '.join(missing)}; set them with `twipsybot cfg`"
            )
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ConfigurationError(f"cannot create {self.log_path.parent}") from e
        old, self.data = self.data, data
        self._watched = tuple(watched)
        self.fingerprint = tuple(stats)
        return old

    def stat(self) -> tuple[tuple[int, int], ...]:
        return tuple(_stat(path) for path in self._watched)

    def get(self, key: str, default: Any = _MISSING) -> Any:
        value = get_dotted(self.data, key)
        if value is None and default is not _MISSING:
            return default
        return value

    def get_required(self, key: str, desc: str | None = None) -> Any:
        value = self.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ConfigurationError(f"missing required config: {desc or key}")
        return value
