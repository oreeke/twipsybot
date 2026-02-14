import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

from .config_keys import ConfigKeys
from .exceptions import ConfigurationError

__all__ = ("Config",)

_MISSING = object()

_ENV_TO_KEY = {
    "MISSKEY_INSTANCE_URL": ConfigKeys.MISSKEY_INSTANCE_URL,
    "MISSKEY_ACCESS_TOKEN": ConfigKeys.MISSKEY_ACCESS_TOKEN,
    "OPENAI_API_KEY": ConfigKeys.OPENAI_API_KEY,
    "OPENAI_MODEL": ConfigKeys.OPENAI_MODEL,
    "OPENAI_API_BASE": ConfigKeys.OPENAI_API_BASE,
    "OPENAI_API_MODE": ConfigKeys.OPENAI_API_MODE,
    "OPENAI_MAX_TOKENS": ConfigKeys.OPENAI_MAX_TOKENS,
    "OPENAI_TEMPERATURE": ConfigKeys.OPENAI_TEMPERATURE,
    "BOT_SYSTEM_PROMPT": ConfigKeys.BOT_SYSTEM_PROMPT,
    "BOT_AUTO_POST_ENABLED": ConfigKeys.BOT_AUTO_POST_ENABLED,
    "BOT_AUTO_POST_INTERVAL": ConfigKeys.BOT_AUTO_POST_INTERVAL,
    "BOT_AUTO_POST_MAX_PER_DAY": ConfigKeys.BOT_AUTO_POST_MAX_PER_DAY,
    "BOT_AUTO_POST_VISIBILITY": ConfigKeys.BOT_AUTO_POST_VISIBILITY,
    "BOT_AUTO_POST_LOCAL_ONLY": ConfigKeys.BOT_AUTO_POST_LOCAL_ONLY,
    "BOT_AUTO_POST_PROMPT": ConfigKeys.BOT_AUTO_POST_PROMPT,
    "BOT_RESPONSE_MENTION": ConfigKeys.BOT_RESPONSE_MENTION,
    "BOT_RESPONSE_CHAT": ConfigKeys.BOT_RESPONSE_CHAT,
    "BOT_RESPONSE_CHAT_MEMORY": ConfigKeys.BOT_RESPONSE_CHAT_MEMORY,
    "BOT_RESPONSE_RATE_LIMIT": ConfigKeys.BOT_RESPONSE_RATE_LIMIT,
    "BOT_RESPONSE_RATE_LIMIT_REPLY": ConfigKeys.BOT_RESPONSE_RATE_LIMIT_REPLY,
    "BOT_RESPONSE_MAX_TURNS": ConfigKeys.BOT_RESPONSE_MAX_TURNS,
    "BOT_RESPONSE_MAX_TURNS_REPLY": ConfigKeys.BOT_RESPONSE_MAX_TURNS_REPLY,
    "BOT_RESPONSE_MAX_TURNS_RELEASE": ConfigKeys.BOT_RESPONSE_MAX_TURNS_RELEASE,
    "BOT_RESPONSE_WHITELIST": ConfigKeys.BOT_RESPONSE_WHITELIST,
    "BOT_RESPONSE_BLACKLIST": ConfigKeys.BOT_RESPONSE_BLACKLIST,
    "BOT_TIMELINE_ENABLED": ConfigKeys.BOT_TIMELINE_ENABLED,
    "BOT_TIMELINE_HOME": ConfigKeys.BOT_TIMELINE_HOME,
    "BOT_TIMELINE_LOCAL": ConfigKeys.BOT_TIMELINE_LOCAL,
    "BOT_TIMELINE_HYBRID": ConfigKeys.BOT_TIMELINE_HYBRID,
    "BOT_TIMELINE_GLOBAL": ConfigKeys.BOT_TIMELINE_GLOBAL,
    "BOT_TIMELINE_ANTENNA_IDS": ConfigKeys.BOT_TIMELINE_ANTENNA_IDS,
    "DB_PATH": ConfigKeys.DB_PATH,
    "DB_CLEAR": ConfigKeys.DB_CLEAR,
    "LOG_PATH": ConfigKeys.LOG_PATH,
    "LOG_LEVEL": ConfigKeys.LOG_LEVEL,
    "LOG_DUMP_EVENTS": ConfigKeys.LOG_DUMP_EVENTS,
}


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _set_dotted(config: dict[str, Any], dotted: str, value: Any) -> None:
    cur: dict[str, Any] = config
    parts = dotted.split(".")
    for key in parts[:-1]:
        nxt = cur.get(key)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[key] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _get_dotted(config: dict[str, Any], dotted: str) -> Any:
    cur: Any = config
    for key in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _maybe_load_text_file(
    value: str,
    *,
    config_dir: Path,
    project_root: Path,
) -> str:
    s = value.strip()
    if not s:
        return ""
    file_path = s.removeprefix("file://") if s.startswith("file://") else s
    path = Path(file_path)
    if not path.is_absolute():
        path = config_dir / path
    try:
        resolved = path.resolve()
    except OSError:
        return value
    if not resolved.is_file() or not resolved.is_relative_to(project_root):
        return value
    try:
        return resolved.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return value


class MisskeyConfig(BaseModel):
    instance_url: str
    access_token: str


class OpenAIConfig(BaseModel):
    api_key: str
    model: str = "deepseek-chat"
    api_base: str = "https://api.deepseek.com/v1"
    api_mode: str = "auto"
    max_tokens: int = 1000
    temperature: float = 0.8

    @field_validator("api_mode")
    @classmethod
    def _validate_api_mode(cls, v: str) -> str:
        s = v.strip().lower()
        if s not in {"auto", "chat", "responses"}:
            raise ValueError("OpenAI API mode must be auto/chat/responses")
        return s

    @field_validator("max_tokens")
    @classmethod
    def _validate_max_tokens(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("max tokens must be > 0")
        return v

    @field_validator("temperature")
    @classmethod
    def _validate_temperature(cls, v: float) -> float:
        if not (0 <= float(v) <= 2):
            raise ValueError("temperature must be between 0 and 2")
        return float(v)


class TimelineConfig(BaseModel):
    enabled: bool = False
    home: bool = False
    local: bool = False
    hybrid: bool = False
    global_: bool = Field(default=False, alias="global")
    antenna_ids: list[str] | str = []

    @field_validator("antenna_ids", mode="before")
    @classmethod
    def _normalize_antenna_ids(cls, v: Any) -> Any:
        if v is None:
            return []
        return v


class AutoPostConfig(BaseModel):
    enabled: bool = True
    interval_minutes: int = 180
    max_posts_per_day: int = 8
    visibility: str = "public"
    local_only: bool = False
    prompt: str = ""

    @field_validator("interval_minutes")
    @classmethod
    def _validate_interval_minutes(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("auto-post interval must be > 0")
        return v

    @field_validator("max_posts_per_day")
    @classmethod
    def _validate_max_posts_per_day(cls, v: int) -> int:
        if v < 0:
            raise ValueError("max auto-posts per day must be >= 0")
        return v

    @field_validator("visibility")
    @classmethod
    def _validate_visibility(cls, v: str) -> str:
        s = v.strip()
        if s not in {"public", "home", "followers"}:
            raise ValueError("post visibility must be public/home/followers")
        return s


class ResponseConfig(BaseModel):
    mention: bool = True
    chat: bool = True
    chat_memory: int = 10
    rate_limit: int | str = -1
    rate_limit_reply: str = "我需要休息一下..."
    max_turns: int = -1
    max_turns_reply: str = "我要回家了..."
    max_turns_release: int | str = -1
    whitelist: list[str] | str = []
    blacklist: list[str] | str = []

    @field_validator("chat_memory")
    @classmethod
    def _validate_chat_memory(cls, v: int) -> int:
        if v < 0:
            raise ValueError("chat context memory length must be >= 0")
        return v

    @field_validator("max_turns")
    @classmethod
    def _validate_max_turns(cls, v: int) -> int:
        if v < -1:
            raise ValueError("response max turns must be >= -1")
        return v


class BotConfig(BaseModel):
    system_prompt: str = ""
    timeline: TimelineConfig = TimelineConfig()
    auto_post: AutoPostConfig = AutoPostConfig()
    response: ResponseConfig = ResponseConfig()


class DBConfig(BaseModel):
    path: str = "data/twipsybot.db"
    clear: int | str = 30

    @field_validator("clear")
    @classmethod
    def _validate_clear(cls, v: int | str) -> int | str:
        if isinstance(v, int) and v < -1:
            raise ValueError("database clear days must be >= -1")
        if isinstance(v, str) and not v.strip():
            raise ValueError("database clear days must not be empty")
        return v


class LogConfig(BaseModel):
    path: str = "logs/twipsybot.log"
    level: str = "INFO"
    dump_events: bool = False


class AppConfig(BaseModel):
    misskey: MisskeyConfig
    openai: OpenAIConfig
    bot: BotConfig = BotConfig()
    db: DBConfig = DBConfig()
    log: LogConfig = LogConfig()


class Config:
    def __init__(self, config_path: str | None = None):
        self.config_path = config_path or os.environ.get("CONFIG_PATH", "config.yaml")
        self._model: AppConfig | None = None
        self.data: dict[str, Any] = {}

    def load(self) -> None:
        config_path = Path(self.config_path)
        merged = self._load_yaml_config(config_path)
        self._apply_env_overrides(merged)
        self._expand_prompt_files(merged, config_path)
        self._model = self._validate_model(merged)
        self.data = self._model.model_dump()
        self._ensure_paths()

    @staticmethod
    def _load_yaml_config(config_path: Path) -> dict[str, Any]:
        if not config_path.exists():
            return {}
        if not config_path.is_file():
            raise ConfigurationError(f"config path is not a file: {config_path}")
        try:
            raw = config_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as e:
            raise ConfigurationError(f"Config file decode error: {e}") from e
        except OSError as e:
            raise ConfigurationError(f"Config file read error: {e}") from e
        try:
            loaded = yaml.safe_load(raw) or {}
        except yaml.YAMLError as e:
            raise ConfigurationError(f"YAML config parse error: {e}") from e
        if not isinstance(loaded, dict):
            raise ConfigurationError("config file root node must be an object")
        return loaded

    @staticmethod
    def _apply_env_overrides(config: dict[str, Any]) -> None:
        for env_name, key in _ENV_TO_KEY.items():
            if (env_value := os.environ.get(env_name)) is not None:
                _set_dotted(config, key, env_value)

    @staticmethod
    def _expand_prompt_files(config: dict[str, Any], config_path: Path) -> None:
        config_dir = config_path.parent if config_path.parent else Path(".")
        project_root = _project_root()
        for key in (ConfigKeys.BOT_SYSTEM_PROMPT, ConfigKeys.BOT_AUTO_POST_PROMPT):
            value = _get_dotted(config, key)
            if not isinstance(value, str):
                continue
            _set_dotted(
                config,
                key,
                _maybe_load_text_file(
                    value, config_dir=config_dir, project_root=project_root
                ),
            )

    @staticmethod
    def _validate_model(config: dict[str, Any]) -> AppConfig:
        try:
            return AppConfig.model_validate(config)
        except ValidationError as e:
            raise ConfigurationError(str(e)) from e

    def _ensure_paths(self) -> None:
        for key, desc in (
            (ConfigKeys.DB_PATH, "database directory"),
            (ConfigKeys.LOG_PATH, "log directory"),
        ):
            path = self.get(key)
            if not isinstance(path, str) or not path:
                continue
            try:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                raise ConfigurationError(f"failed to create {desc}: {path}") from e

    def get(self, key: str, default: Any = _MISSING) -> Any:
        if self._model is None:
            if default is not _MISSING:
                return default
            return None
        if key == ConfigKeys.BOT_TIMELINE_GLOBAL:
            value = _get_dotted(self.data, "bot.timeline.global_")
        else:
            value = _get_dotted(self.data, key)
        if value is None and default is not _MISSING:
            return default
        return value

    def get_required(self, key: str, desc: str | None = None) -> Any:
        value = self.get(key)
        if value is None:
            raise ConfigurationError(f"missing required config: {desc or key}")
        if isinstance(value, str) and not value.strip():
            raise ConfigurationError(f"missing required config: {desc or key}")
        return value
