import asyncio
import importlib.util
import inspect
import re
import sys
from collections.abc import Coroutine
from contextlib import suppress
from copy import deepcopy
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml
from loguru import logger

from ..shared.config import Config
from .api import PLUGIN_API_VERSION
from .base import PluginBase
from .context import PluginContext
from .events import build_hook_event
from .services import (
    BotControlAdapter,
    MisskeyServiceAdapter,
    NamespacedPluginStorage,
    OpenAIServiceAdapter,
)

__all__ = ("PluginManager",)

_PLUGIN_CONFIG_FILENAME = "config.yaml"
_PLUGIN_HOOK_TIMEOUT_SECONDS = 60.0
_PLUGIN_LIFECYCLE_TIMEOUT_SECONDS = 30.0
_EVENT_HOOKS = {
    "on_message",
    "on_mention",
    "on_notification",
    "on_timeline_note",
    "on_auto_post",
}
_ASYNC_PLUGIN_METHODS = {
    "initialize",
    "on_startup",
    *_EVENT_HOOKS,
    "on_shutdown",
    "cleanup",
}


class PluginManager:
    def __init__(
        self,
        config: Config,
        plugins_dir: str = "plugins",
        *,
        db: Any,
        misskey: Any,
        openai: Any,
        bot: Any,
    ):
        self.config = config
        self.plugins_dir = Path(plugins_dir)
        self.plugins: dict[str, PluginBase] = {}
        self.discovered_plugins: dict[str, dict[str, Any]] = {}
        self.db = db
        self.misskey = misskey
        self.openai = openai
        self.bot = bot
        self._master_config: dict[str, Any] = {}
        self._accepting_hooks = False
        self._active_hooks = 0
        self._hooks_idle = asyncio.Event()
        self._hooks_idle.set()

    def _iter_plugin_dirs(self):
        for plugin_dir in self.plugins_dir.iterdir():
            if (
                plugin_dir.is_dir()
                and not plugin_dir.name.startswith(".")
                and plugin_dir.name != "__pycache__"
            ):
                yield plugin_dir

    def _discover_plugin_dir(
        self, plugin_dir: Path, plugin_config: dict[str, Any]
    ) -> tuple[bool, bool]:
        configured = self._is_plugin_configured(plugin_dir)
        try:
            enabled = PluginBase._parse_bool(plugin_config.get("enabled"), False)
        except ValueError as e:
            logger.error(f"Invalid plugin config: plugin={plugin_dir.name}: {e}")
            enabled = False
        key = plugin_dir.name
        name = self._camelize(key)
        self.discovered_plugins[key] = {
            "name": name,
            "enabled": enabled,
            "priority": plugin_config.get("priority", 0),
            "configured": configured,
        }
        if configured:
            status = "enabled" if enabled else "disabled"
            logger.debug(f"Discovered plugin: {plugin_dir.name} (status: {status})")
        return configured, enabled

    async def load_plugins(self) -> None:
        if not self.plugins_dir.exists():
            logger.info(f"Plugins directory not found: {self.plugins_dir}")
            return
        self._master_config = self._load_master_config()
        for plugin_dir in self._iter_plugin_dirs():
            plugin_config = self._load_plugin_config(plugin_dir)
            configured, enabled = self._discover_plugin_dir(plugin_dir, plugin_config)
            if configured and enabled:
                self._load_plugin(plugin_dir, plugin_config)
        await self._initialize_plugins()
        enabled_count = sum(plugin._enabled for plugin in self.plugins.values())
        logger.info(
            f"Found {len(self.discovered_plugins)} plugins; {enabled_count} enabled"
        )

    def _load_master_config(self) -> dict[str, Any]:
        master_file = self.plugins_dir / _PLUGIN_CONFIG_FILENAME
        if not master_file.exists():
            return {}
        try:
            with open(master_file, encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
            if not isinstance(loaded, dict):
                logger.error(
                    "Error loading plugins config: root node must be an object"
                )
                return {}
            return loaded
        except Exception as e:
            logger.error(f"Error loading plugins config: {e}")
            return {}

    def _is_plugin_configured(self, plugin_dir: Path) -> bool:
        return (
            plugin_dir.name in self._master_config
            or (plugin_dir / _PLUGIN_CONFIG_FILENAME).exists()
        )

    def _load_plugin_config(self, plugin_dir: Path) -> dict[str, Any]:
        master_entry = self._master_config.get(plugin_dir.name)
        if isinstance(master_entry, dict):
            return master_entry
        config_file = plugin_dir / _PLUGIN_CONFIG_FILENAME
        if not config_file.exists():
            return {"enabled": False}
        try:
            with open(config_file, encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
            if not isinstance(loaded, dict):
                logger.error(
                    f"Error loading plugin config for {plugin_dir.name}: root node must be an object"
                )
                return {"enabled": False}
            return loaded
        except Exception as e:
            logger.error(f"Error loading plugin config for {plugin_dir.name}: {e}")
            return {"enabled": False}

    def _load_plugin(self, plugin_dir: Path, plugin_config: dict[str, Any]) -> None:
        try:
            plugin_file = plugin_dir / f"{plugin_dir.name}.py"
            if not plugin_file.exists():
                logger.warning(
                    f"Missing plugin file in {plugin_dir.name}: {plugin_dir.name}.py"
                )
                return
            if not (module := self._load_plugin_module(plugin_dir, plugin_file)):
                return
            if not (plugin_class := self._find_plugin_class(module, plugin_dir.name)):
                return
            api_version = getattr(plugin_class, "api_version", None)
            if type(api_version) is not int or api_version != PLUGIN_API_VERSION:
                logger.error(
                    f"Incompatible plugin API: plugin={plugin_dir.name} "
                    f"requires={api_version} "
                    f"supported={PLUGIN_API_VERSION}"
                )
                return
            if invalid := self._find_sync_plugin_method(plugin_class):
                logger.error(
                    f"Invalid plugin method: plugin={plugin_dir.name} "
                    f"method={invalid} must be async"
                )
                return
            if invalid := self._find_invalid_plugin_signature(plugin_class):
                logger.error(
                    f"Invalid plugin method: plugin={plugin_dir.name} "
                    f"method={invalid} has incompatible signature"
                )
                return
            plugin_instance = self._create_plugin_instance(plugin_class, plugin_config)
            self.plugins[plugin_dir.name] = plugin_instance
        except Exception as e:
            logger.error(f"Failed to load plugin {plugin_dir.name}: {e}")

    @staticmethod
    def _camelize(name: str) -> str:
        parts = [p for p in re.split(r"[^a-zA-Z0-9]+", name) if p]
        if not parts:
            return name.capitalize()
        return "".join(part[:1].upper() + part[1:] for part in parts)

    @staticmethod
    def _load_plugin_module(plugin_dir: Path, plugin_file: Path):
        project_root = str(plugin_dir.parent.parent.resolve())
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        spec = importlib.util.spec_from_file_location(
            f"plugins.{plugin_dir.name}.plugin", plugin_file
        )
        if spec is None or spec.loader is None:
            logger.warning(f"Failed to load plugin spec: {plugin_dir.name}")
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def _find_plugin_class(module, plugin_name):
        candidates = [
            attr
            for attr in (getattr(module, name) for name in dir(module))
            if isinstance(attr, type)
            and attr.__module__ == module.__name__
            and issubclass(attr, PluginBase)
            and attr is not PluginBase
        ]
        if not candidates:
            logger.warning(f"No valid plugin class found in {plugin_name.capitalize()}")
            return None
        expected = f"{PluginManager._camelize(plugin_name)}Plugin"
        for cls in candidates:
            if cls.__name__ == expected:
                return cls
        expected_lower = expected.lower()
        case_insensitive = [
            cls for cls in candidates if cls.__name__.lower() == expected_lower
        ]
        if len(case_insensitive) == 1:
            return case_insensitive[0]
        names = sorted(cls.__name__ for cls in candidates)
        logger.warning(
            f"No matching plugin class found in {plugin_name.capitalize()}: "
            f"{names}; expected {expected}"
        )
        return None

    @staticmethod
    def _find_sync_plugin_method(plugin_class: type[PluginBase]) -> str | None:
        for method_name in _ASYNC_PLUGIN_METHODS:
            method = getattr(plugin_class, method_name, None)
            if method is not None and not asyncio.iscoroutinefunction(method):
                return method_name
        return None

    @staticmethod
    def _find_invalid_plugin_signature(plugin_class: type[PluginBase]) -> str | None:
        instance = object()
        event = object()
        for method_name in _ASYNC_PLUGIN_METHODS:
            method = getattr(plugin_class, method_name, None)
            if method is None:
                continue
            args = (instance, event) if method_name in _EVENT_HOOKS else (instance,)
            try:
                inspect.signature(method).bind(*args)
            except (TypeError, ValueError):
                return method_name
        return None

    def _create_plugin_instance(self, plugin_class, plugin_config):
        name = plugin_class.__name__
        if name.endswith("Plugin"):
            name = name[: -len("Plugin")]
        context = PluginContext(
            name=name,
            config=MappingProxyType(deepcopy(plugin_config)),
            storage=NamespacedPluginStorage(self.db, name),
            misskey=MisskeyServiceAdapter(self.misskey),
            openai=OpenAIServiceAdapter(self.openai, self.config),
            bot=BotControlAdapter(self.bot),
        )
        return plugin_class(context)

    async def _initialize_plugins(self) -> None:
        for _, plugin in sorted(
            self.plugins.items(), key=lambda x: x[1]._priority, reverse=True
        ):
            if not plugin._enabled:
                continue
            await self._initialize_plugin(plugin)

    async def _initialize_plugin(self, plugin: PluginBase) -> bool:
        try:
            async with asyncio.timeout(_PLUGIN_LIFECYCLE_TIMEOUT_SECONDS):
                initialized = await plugin.initialize()
            if initialized is True:
                plugin._initialized = True
                return True
            logger.warning(f"Plugin {plugin.context.name} initialization failed")
        except asyncio.CancelledError:
            await self._cleanup_plugin(plugin)
            raise
        except TimeoutError:
            logger.warning(
                f"Plugin lifecycle timeout: plugin={plugin.context.name} "
                f"method=initialize timeout={_PLUGIN_LIFECYCLE_TIMEOUT_SECONDS:g}s"
            )
        except Exception as e:
            logger.exception(f"Error initializing plugin {plugin.context.name}: {e}")
        await self._cleanup_plugin(plugin)
        plugin._set_enabled(False)
        return False

    async def startup_plugins(self) -> None:
        for plugin in self._iter_enabled_plugins():
            if not plugin._initialized:
                continue
            if await self._call_lifecycle(plugin, "on_startup"):
                plugin._started = True
                continue
            await self._cleanup_plugin(plugin)
            plugin._set_enabled(False)
        self._accepting_hooks = True

    async def shutdown_plugins(self) -> None:
        await self._pause_hook_dispatch()
        for plugin in self._iter_enabled_plugins():
            if not plugin._started:
                continue
            await self._call_lifecycle(plugin, "on_shutdown")
            plugin._started = False

    @staticmethod
    async def _call_lifecycle(plugin: PluginBase, method_name: str) -> bool:
        try:
            async with asyncio.timeout(_PLUGIN_LIFECYCLE_TIMEOUT_SECONDS):
                await getattr(plugin, method_name)()
            return True
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            logger.warning(
                f"Plugin lifecycle timeout: plugin={plugin.context.name} "
                f"method={method_name} timeout={_PLUGIN_LIFECYCLE_TIMEOUT_SECONDS:g}s"
            )
        except Exception as e:
            logger.exception(
                f"Error in plugin lifecycle: plugin={plugin.context.name} "
                f"method={method_name}: {e}"
            )
        return False

    async def cleanup_plugins(self) -> None:
        await self._pause_hook_dispatch()
        for plugin in tuple(self.plugins.values()):
            if plugin._initialized:
                await self._cleanup_plugin(plugin)

    def _begin_hook_dispatch(self) -> bool:
        if not self._accepting_hooks:
            return False
        self._active_hooks += 1
        self._hooks_idle.clear()
        return True

    def _end_hook_dispatch(self) -> None:
        self._active_hooks -= 1
        if self._active_hooks == 0:
            self._hooks_idle.set()

    async def _pause_hook_dispatch(self) -> None:
        self._accepting_hooks = False
        await self._hooks_idle.wait()

    @staticmethod
    async def _complete_before_cancellation(
        awaitable: Coroutine[Any, Any, Any],
    ) -> Any:
        task = asyncio.create_task(awaitable)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            while not task.done():
                with suppress(asyncio.CancelledError):
                    await asyncio.shield(task)
            task.result()
            raise

    def _iter_enabled_plugins(self):
        yield from sorted(
            (p for p in self.plugins.values() if p._enabled),
            key=lambda x: x._priority,
            reverse=True,
        )

    async def _call_single_plugin_hook(
        self, plugin: PluginBase, hook_name: str, *, args, kwargs
    ) -> Any | None:
        method = getattr(plugin, hook_name, None)
        if method is None:
            return None
        try:
            async with asyncio.timeout(_PLUGIN_HOOK_TIMEOUT_SECONDS):
                result = await method(*args, **kwargs)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            logger.warning(
                f"Plugin hook timeout: plugin={plugin.context.name} hook={hook_name} "
                f"timeout={_PLUGIN_HOOK_TIMEOUT_SECONDS:g}s"
            )
            return None
        except Exception as e:
            logger.exception(
                f"Unhandled exception in plugin {plugin.context.name} hook {hook_name}: {e}"
            )
            return None
        if result is None:
            return None
        if not self._validate_hook_result(hook_name, result):
            logger.warning(
                f"Ignoring invalid plugin result: plugin={plugin.context.name} hook={hook_name} type={type(result).__name__}"
            )
            return None
        output = {**result, "plugin_name": plugin.context.name}
        if hook_name == "on_auto_post" and hasattr(plugin, "_on_auto_post_published"):
            output["_publisher"] = plugin
        return output

    @staticmethod
    def _validate_hook_result(hook_name: str, result: Any) -> bool:
        if not isinstance(result, dict):
            return False
        if hook_name in {"on_message", "on_mention"}:
            return (
                set(result) == {"handled", "response"}
                and result["handled"] is True
                and isinstance(result["response"], str)
            )
        if hook_name == "on_auto_post":
            if set(result) <= {"contents", "visibility"} and "contents" in result:
                contents = result["contents"]
                visibility = result.get("visibility")
                visibility_valid = "visibility" not in result or (
                    isinstance(visibility, str)
                    and visibility in {"public", "home", "followers"}
                )
                return (
                    isinstance(contents, list)
                    and bool(contents)
                    and all(
                        isinstance(content, str) and bool(content.strip())
                        for content in contents
                    )
                    and visibility_valid
                )
            if set(result) <= {"prompt", "timestamp"} and "prompt" in result:
                return (
                    isinstance(result["prompt"], str)
                    and bool(result["prompt"].strip())
                    and type(result.get("timestamp", 0)) is int
                )
        return False

    async def call_plugin_hook(self, hook_name: str, *args, **kwargs) -> list[Any]:
        if not self._begin_hook_dispatch():
            return []
        try:
            return await self._dispatch_plugin_hook(hook_name, args, kwargs)
        finally:
            self._end_hook_dispatch()

    async def _dispatch_plugin_hook(
        self, hook_name: str, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> list[Any]:
        results: list[Any] = []
        stop_on_handled = hook_name in {"on_message", "on_mention"}
        payload = args[0] if args else None
        shared_event = (
            build_hook_event(hook_name, payload)
            if hook_name == "on_auto_post"
            else None
        )
        for plugin in self._iter_enabled_plugins():
            if not plugin._initialized:
                continue
            try:
                hook_args = (
                    (shared_event or build_hook_event(hook_name, payload),)
                    if hook_name in _EVENT_HOOKS
                    else args
                )
            except ValueError as e:
                logger.warning(f"Invalid plugin event: hook={hook_name}: {e}")
                return []
            result = await self._call_single_plugin_hook(
                plugin, hook_name, args=hook_args, kwargs=kwargs
            )
            if result is None:
                continue
            results.append(result)
            if stop_on_handled and result.get("handled") is True:
                break
        return results

    def get_plugin_info(self) -> list[dict[str, Any]]:
        info = {
            name: info
            for name, info in self.discovered_plugins.items()
            if info.get("configured")
        }
        info.update({name: plugin._get_info() for name, plugin in self.plugins.items()})
        return [info[name] for name in sorted(info)]

    def get_plugin(self, name: str) -> PluginBase | None:
        return self.plugins.get(name)

    async def confirm_auto_post_published(
        self, result: dict[str, Any], content: str
    ) -> None:
        plugin = result.get("_publisher")
        if plugin is None:
            return
        callback = getattr(plugin, "_on_auto_post_published", None)
        if callback is None:
            return

        async def confirm() -> None:
            async with asyncio.timeout(_PLUGIN_HOOK_TIMEOUT_SECONDS):
                await callback(content)

        try:
            await self._complete_before_cancellation(confirm())
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(
                f"Auto-post confirmation failed: plugin={plugin.context.name}: {e}"
            )

    @staticmethod
    async def _cleanup_plugin(plugin: PluginBase) -> None:
        cleanup_task = asyncio.create_task(PluginManager._run_cleanup(plugin))
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            await cleanup_task
            raise

    @staticmethod
    async def _run_cleanup(plugin: PluginBase) -> None:
        try:
            async with asyncio.timeout(_PLUGIN_LIFECYCLE_TIMEOUT_SECONDS):
                await plugin.cleanup()
        except TimeoutError:
            logger.warning(
                f"Plugin lifecycle timeout: plugin={plugin.context.name} "
                f"method=cleanup timeout={_PLUGIN_LIFECYCLE_TIMEOUT_SECONDS:g}s"
            )
        except Exception as e:
            logger.exception(f"Error cleaning up plugin {plugin.context.name}: {e}")
        finally:
            plugin._initialized = False
            plugin._started = False
