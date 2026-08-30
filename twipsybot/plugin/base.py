import asyncio
import inspect
from typing import Any, ClassVar

from loguru import logger

from .context import PluginContext
from .results import HandledResult

__all__ = ("PluginBase",)


class PluginBase:
    api_version: ClassVar[int]

    def __init__(self, context: PluginContext):
        self.context = context
        self._enabled = self._parse_bool(context.config.get("enabled"), False)
        self._priority = int(context.config.get("priority", 0))
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

    def _get_info(self) -> dict[str, Any]:
        return {
            "name": self.context.name,
            "enabled": self._enabled,
            "priority": self._priority,
            "description": getattr(self, "description", "No description available"),
        }

    def _set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        logger.info(
            f"Plugin {self.context.name} {'enabled' if enabled else 'disabled'}"
        )

    def _log_plugin_action(self, action: str, details: str = "") -> None:
        logger.info(
            f"Plugin {self.context.name} {action}{': ' + details if details else ''}"
        )

    def handled(self, response: str) -> HandledResult:
        return {"handled": True, "response": response}

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
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Plugin {self.context.name} resource cleanup failed: {e}")
        self._resources_to_cleanup.clear()
