import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, ClassVar, Literal, get_type_hints

from loguru import logger
from pydantic import BaseModel, BeforeValidator, ConfigDict, create_model

from .contracts import HandledResult, PluginContext

PLUGIN_API_VERSION = 4
ToolAccess = Literal["all", "followers", "admin"]


def _split_lines(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    lines = (line.strip() for line in value.splitlines())
    return [line for line in lines if line and not line.startswith("#")]


LineText = BeforeValidator(_split_lines)


class PluginConfig(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, validate_default=True)


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    access: ToolAccess
    external: bool
    effect: bool
    params: type[BaseModel]
    schema: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolEntry:
    label: str
    spec: ToolSpec
    call: Callable[[Any, dict[str, Any]], Awaitable[Any]]


def tool(
    description: str,
    *,
    access: ToolAccess = "all",
    external: bool = False,
    effect: bool = False,
):
    def mark(method):
        parameters = inspect.signature(method).parameters
        if not inspect.iscoroutinefunction(method) or [*parameters][:2] != [
            "self",
            "event",
        ]:
            raise TypeError(
                f"tool {method.__name__} must be `async def (self, event, ...)`"
            )
        hints = get_type_hints(method, include_extras=True)
        fields: dict[str, Any] = {
            name: (hints.get(name, Any), ... if p.default is p.empty else p.default)
            for name, p in list(parameters.items())[2:]
        }
        params = create_model(
            method.__name__, __config__=ConfigDict(extra="forbid"), **fields
        )
        schema = params.model_json_schema()
        schema.pop("title", None)
        for prop in schema.get("properties", {}).values():
            prop.pop("title", None)
        method.__tool__ = ToolSpec(
            method.__name__, description, access, external, effect, params, schema
        )
        return method

    return mark


def tool_specs(owner: object) -> tuple[ToolSpec, ...]:
    return tuple(
        method.__tool__
        for _, method in inspect.getmembers(type(owner), inspect.isfunction)
        if hasattr(method, "__tool__")
    )


class PluginBase:
    api_version: ClassVar[int]
    config_class: ClassVar[type[PluginConfig]] = PluginConfig
    settings: PluginConfig

    def __init__(self, context: PluginContext):
        self.context = context
        self.settings = self.config_class.model_validate(context.config)
        self._enabled = self._parse_bool(context.config.get("enabled"), False)
        self._tools = tool_specs(self)
        self._initialized = False
        self._started = False
        self._resources_to_cleanup = []

    @staticmethod
    def _parse_bool(value: Any, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
            return value.strip().lower() == "true"
        raise ValueError(f"invalid boolean value: {value!r}")

    async def initialize(self) -> bool:
        await asyncio.sleep(0)
        return True

    async def cleanup(self) -> None:
        await self._cleanup_registered_resources()

    async def on_startup(self) -> None:
        await asyncio.sleep(0)

    async def on_shutdown(self) -> None:
        await asyncio.sleep(0)

    async def on_auto_post_published(self, _content: str) -> None:
        await asyncio.sleep(0)

    def _set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        logger.info(
            f"Plugin {self.context.name} {'enabled' if enabled else 'disabled'}"
        )

    def _log_plugin_action(self, action: str, details: str = "") -> None:
        logger.info(
            f"Plugin {self.context.name} {action}{': ' + details if details else ''}"
        )

    def handled(
        self,
        response: str,
        file_id: str | None = None,
        followup: Callable[[], Awaitable[HandledResult]] | None = None,
    ) -> HandledResult:
        result: HandledResult = {"handled": True, "response": response}
        if file_id:
            result["file_id"] = file_id
        if followup:
            result["followup"] = followup
        return result

    def _register_resource(self, resource: Any, cleanup_method: str = "close") -> None:
        self._resources_to_cleanup.append((resource, cleanup_method))

    async def _cleanup_registered_resources(self) -> None:
        for resource, cleanup_method in self._resources_to_cleanup:
            try:
                if hasattr(resource, cleanup_method):
                    method = getattr(resource, cleanup_method)
                    if inspect.iscoroutinefunction(method):
                        await method()
                    else:
                        method()
            except Exception as e:
                logger.error(f"Plugin {self.context.name} resource cleanup failed: {e}")
        self._resources_to_cleanup.clear()
