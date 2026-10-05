import asyncio
import hashlib
import importlib.util
import inspect
import sys
from collections import Counter
from collections.abc import Callable
from contextlib import contextmanager
from copy import deepcopy
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, get_args

from loguru import logger

from ..shared.config import Config
from .base import PLUGIN_API_VERSION, PluginBase
from .contracts import PluginContext
from .events import AutoPostVisibility, build_hook_event
from .services import (
    BotControlAdapter,
    MisskeyServiceAdapter,
    NamespacedPluginStorage,
    OpenAIServiceAdapter,
)

__all__ = ("HookResult", "PluginManager", "discover_plugin_classes")

PluginReloadStatus = Literal["enabled", "disabled", "failed", "unknown", "unavailable"]
HookResult = tuple[str, dict[str, Any]]

_PLUGIN_ENTRY_POINT_GROUP = "twipsybot.plugins"
_PLUGIN_HOOK_TIMEOUT_SECONDS = 180.0
_PLUGIN_LIFECYCLE_TIMEOUT_SECONDS = 30.0
_PLUGIN_SHUTDOWN_GRACE_SECONDS = 3.0
_EVENT_HOOKS = {
    "on_message",
    "on_mention",
    "on_context",
    "on_notification",
    "on_timeline_note",
    "on_auto_post",
}
_AUTO_POST_VISIBILITIES = frozenset(get_args(AutoPostVisibility))
_CALLBACK_HOOKS = {"on_auto_post_published"}
_PLUGIN_HOOKS = _EVENT_HOOKS | _CALLBACK_HOOKS
_ASYNC_PLUGIN_METHODS = {
    "initialize",
    "on_startup",
    *_PLUGIN_HOOKS,
    "on_shutdown",
    "cleanup",
}


def _non_empty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_handled(result: dict[str, Any]) -> bool:
    return (
        set(result) == {"handled", "response"}
        and result["handled"] is True
        and isinstance(result["response"], str)
    )


def _valid_context(result: dict[str, Any]) -> bool:
    return (
        bool(result)
        and set(result) <= {"context", "text"}
        and all(_non_empty_text(value) for value in result.values())
    )


def _valid_auto_post(result: dict[str, Any]) -> bool:
    keys = set(result)
    if "contents" in keys and keys <= {"contents", "visibility"}:
        contents = result["contents"]
        visibility = result.get("visibility", "public")
        return (
            isinstance(contents, list)
            and bool(contents)
            and all(_non_empty_text(content) for content in contents)
            and isinstance(visibility, str)
            and visibility in _AUTO_POST_VISIBILITIES
        )
    if "prompt" in keys and keys <= {"prompt", "timestamp"}:
        return (
            _non_empty_text(result["prompt"])
            and type(result.get("timestamp", 0)) is int
        )
    return False


_RESULT_VALIDATORS: dict[str, Callable[[dict[str, Any]], bool]] = {
    "on_message": _valid_handled,
    "on_mention": _valid_handled,
    "on_context": _valid_context,
    "on_auto_post": _valid_auto_post,
}


def _plugin_sources(plugins_dir: Path) -> dict[str, Path | EntryPoint]:
    sources: dict[str, Path | EntryPoint] = {}
    if plugins_dir.exists():
        sources |= {
            d.name: d
            for d in sorted(plugins_dir.iterdir())
            if d.is_dir() and not d.name.startswith(".") and d.name != "__pycache__"
        }
    for entry_point in entry_points(group=_PLUGIN_ENTRY_POINT_GROUP):
        sources.setdefault(entry_point.name, entry_point)
    return sources


def _load_plugin_module(plugin_dir: Path, plugin_file: Path):
    digest = hashlib.sha256(str(plugin_file.resolve()).encode()).hexdigest()[:12]
    spec = importlib.util.spec_from_file_location(
        f"_twipsybot_plugin_{plugin_dir.name}_{digest}", plugin_file
    )
    if spec is None or spec.loader is None:
        raise ImportError("failed to load plugin spec")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


def _load_plugin_class(source: Path | EntryPoint) -> Any:
    if not isinstance(source, Path):
        return source.load()
    plugin_file = source / "plugin.py"
    if not plugin_file.exists():
        raise FileNotFoundError("missing plugin.py")
    return getattr(_load_plugin_module(source, plugin_file), "plugin", None)


def _is_plugin_class(value: Any) -> bool:
    return (
        isinstance(value, type)
        and issubclass(value, PluginBase)
        and value is not PluginBase
    )


def discover_plugin_classes(
    plugins_dir: Path,
) -> tuple[dict[str, type[PluginBase]], dict[str, str]]:
    found: dict[str, type[PluginBase]] = {}
    errors: dict[str, str] = {}
    for name, source in _plugin_sources(plugins_dir).items():
        try:
            if not _is_plugin_class(plugin_class := _load_plugin_class(source)):
                raise TypeError("expected PluginBase subclass")
            found[name] = plugin_class
        except Exception as e:
            errors[name] = str(e) or type(e).__name__
    return found, errors


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
        self._lifecycle_lock = asyncio.Lock()
        self._reloading: set[str] = set()
        self._hook_tasks: Counter[asyncio.Task[Any]] = Counter()
        self._hooks_idle = asyncio.Event()
        self._hooks_idle.set()
        self._plugin_calls: Counter[str] = Counter()
        self._plugin_idle: dict[str, asyncio.Event] = {}

    def _discover_plugin(self, key: str) -> dict[str, Any] | None:
        configured = key in self._master_config
        plugin_config = dict(self._master_config.get(key) or {})
        try:
            enabled = PluginBase._parse_bool(plugin_config.get("enabled"), False)
        except ValueError as e:
            logger.error(f"Invalid plugin config: plugin={key}: {e}")
            enabled = False
        self.discovered_plugins[key] = {
            "name": key,
            "enabled": enabled,
            "priority": plugin_config.get("priority", 0),
            "configured": configured,
        }
        if configured:
            status = "enabled" if enabled else "disabled"
            logger.debug(f"Discovered plugin: {key} (status: {status})")
        return plugin_config if configured and enabled else None

    async def load_plugins(self) -> None:
        self._master_config = self._load_master_config()
        if not self.plugins_dir.exists():
            logger.info(f"Plugins directory not found: {self.plugins_dir}")
        for name, source in _plugin_sources(self.plugins_dir).items():
            if (config := self._discover_plugin(name)) is None:
                continue
            try:
                self._register_plugin(name, _load_plugin_class(source), config)
            except Exception as e:
                logger.error(f"Failed to load plugin {name}: {e}")
        await self._initialize_plugins()
        enabled_count = sum(plugin._enabled for plugin in self.plugins.values())
        logger.info(
            f"Found {len(self.discovered_plugins)} plugins; {enabled_count} enabled"
        )

    def _load_master_config(self) -> dict[str, Any]:
        plugins = self.config.get("plugins")
        return plugins if isinstance(plugins, dict) else {}

    def _register_plugin(
        self,
        plugin_name: str,
        plugin_class: Any,
        plugin_config: dict[str, Any],
    ) -> PluginBase | None:
        if not _is_plugin_class(plugin_class):
            logger.warning(
                f"Invalid plugin export in {plugin_name}: expected PluginBase subclass"
            )
            return None
        api_version = getattr(plugin_class, "api_version", None)
        if type(api_version) is not int or api_version != PLUGIN_API_VERSION:
            logger.error(
                f"Incompatible plugin API: plugin={plugin_name} "
                f"requires={api_version} supported={PLUGIN_API_VERSION}"
            )
            return None
        if invalid := self._find_sync_plugin_method(plugin_class):
            logger.error(
                f"Invalid plugin method: plugin={plugin_name} "
                f"method={invalid} must be async"
            )
            return None
        if invalid := self._find_invalid_plugin_signature(plugin_class):
            logger.error(
                f"Invalid plugin method: plugin={plugin_name} "
                f"method={invalid} has incompatible signature"
            )
            return None
        plugin_instance = self._create_plugin_instance(
            plugin_name, plugin_class, plugin_config
        )
        self.plugins[plugin_name] = plugin_instance
        return plugin_instance

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
            args = (instance, event) if method_name in _PLUGIN_HOOKS else (instance,)
            try:
                inspect.signature(method).bind(*args)
            except (TypeError, ValueError):
                return method_name
        return None

    def _create_plugin_instance(self, plugin_name, plugin_class, plugin_config):
        context = PluginContext(
            name=plugin_name,
            config=MappingProxyType(deepcopy(plugin_config)),
            storage=NamespacedPluginStorage(self.db, plugin_name),
            misskey=MisskeyServiceAdapter(self.misskey),
            openai=OpenAIServiceAdapter(self.openai, self.config),
            bot=BotControlAdapter(self.bot),
        )
        return plugin_class(context)

    async def _initialize_plugins(self) -> None:
        for plugin in self._iter_enabled_plugins():
            await self._initialize_plugin(plugin)

    async def _initialize_plugin(self, plugin: PluginBase) -> bool:
        try:
            if await self._call_lifecycle(plugin, "initialize"):
                plugin._initialized = True
                return True
        except asyncio.CancelledError:
            await self._cleanup_plugin(plugin)
            raise
        return await self._disable_plugin(plugin)

    async def _start_plugin(self, plugin: PluginBase) -> bool:
        if await self._call_lifecycle(plugin, "on_startup"):
            plugin._started = True
            return True
        return await self._disable_plugin(plugin)

    async def _disable_plugin(self, plugin: PluginBase) -> bool:
        await self._cleanup_plugin(plugin)
        plugin._set_enabled(False)
        return False

    async def startup_plugins(self) -> None:
        for plugin in self._iter_enabled_plugins():
            if plugin._initialized:
                await self._start_plugin(plugin)
        self._accepting_hooks = True

    async def shutdown_plugins(self) -> None:
        await self._pause_hook_dispatch()
        async with self._lifecycle_lock:
            for plugin in self._iter_enabled_plugins():
                if not plugin._started:
                    continue
                await self._call_lifecycle(plugin, "on_shutdown")
                plugin._started = False

    async def reload_plugin(self, name: str) -> PluginReloadStatus:
        async with self._lifecycle_lock:
            if not self._accepting_hooks:
                return "unavailable"
            if (source := _plugin_sources(self.plugins_dir).get(name)) is None:
                return "unknown"
            self._reloading.add(name)
            try:
                await self._wait_plugin_idle(name)
                if old := self.plugins.get(name):
                    await self._stop_plugin(old)
                status = await self._restart_plugin(name, source, old)
            finally:
                self._reloading.discard(name)
        logger.info(f"Plugin reloaded: plugin={name} status={status}")
        return status

    async def _wait_plugin_idle(self, name: str) -> None:
        if name not in self._plugin_calls:
            return
        try:
            async with asyncio.timeout(_PLUGIN_HOOK_TIMEOUT_SECONDS):
                await self._plugin_idle[name].wait()
        except TimeoutError:
            logger.warning(f"Plugin hooks still active before reload: plugin={name}")

    async def _stop_plugin(self, plugin: PluginBase) -> None:
        if plugin._started:
            await self._call_lifecycle(plugin, "on_shutdown")
        if plugin._initialized:
            await self._cleanup_plugin(plugin)
        plugin._enabled = False

    async def _restart_plugin(
        self, name: str, source: Path | EntryPoint, old: PluginBase | None
    ) -> PluginReloadStatus:
        self._master_config = self._load_master_config()
        if (config := self._discover_plugin(name)) is None:
            return "disabled"
        try:
            plugin_class = type(old) if old else _load_plugin_class(source)
            plugin = self._register_plugin(name, plugin_class, config)
        except Exception as e:
            logger.error(f"Failed to reload plugin {name}: {e}")
            plugin = None
        if (
            plugin is not None
            and await self._initialize_plugin(plugin)
            and await self._start_plugin(plugin)
        ):
            return "enabled"
        self.discovered_plugins[name]["enabled"] = False
        return "failed"

    @staticmethod
    async def _call_lifecycle(plugin: PluginBase, method_name: str) -> bool:
        name = plugin.context.name
        try:
            async with asyncio.timeout(_PLUGIN_LIFECYCLE_TIMEOUT_SECONDS):
                result = await getattr(plugin, method_name)()
        except TimeoutError:
            logger.warning(
                f"Plugin lifecycle timeout: plugin={name} method={method_name} "
                f"timeout={_PLUGIN_LIFECYCLE_TIMEOUT_SECONDS:g}s"
            )
            return False
        except Exception as e:
            logger.exception(
                f"Error in plugin lifecycle: plugin={name} method={method_name}: {e}"
            )
            return False
        if method_name == "initialize" and result is not True:
            logger.warning(f"Plugin {name} initialization failed")
            return False
        return True

    async def cleanup_plugins(self) -> None:
        await self._pause_hook_dispatch()
        async with self._lifecycle_lock:
            for plugin in tuple(self.plugins.values()):
                if plugin._initialized:
                    await self._cleanup_plugin(plugin)

    @contextmanager
    def _track_plugin_call(self, name: str):
        self._plugin_calls[name] += 1
        self._plugin_idle.setdefault(name, asyncio.Event()).clear()
        try:
            yield
        finally:
            self._plugin_calls[name] -= 1
            if not self._plugin_calls[name]:
                del self._plugin_calls[name]
                self._plugin_idle[name].set()

    @contextmanager
    def _track_hook_task(self, task: asyncio.Task[Any]):
        self._hook_tasks[task] += 1
        self._hooks_idle.clear()
        try:
            yield
        finally:
            self._hook_tasks[task] -= 1
            if not self._hook_tasks[task]:
                del self._hook_tasks[task]
            if not self._hook_tasks:
                self._hooks_idle.set()

    async def _pause_hook_dispatch(self) -> None:
        self._accepting_hooks = False
        try:
            async with asyncio.timeout(_PLUGIN_SHUTDOWN_GRACE_SECONDS):
                await self._hooks_idle.wait()
            return
        except TimeoutError:
            tasks = tuple(self._hook_tasks)
            logger.warning(
                f"Cancelling {len(tasks)} active plugin hook task(s) after "
                f"{_PLUGIN_SHUTDOWN_GRACE_SECONDS:g}s shutdown grace period"
            )
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _iter_enabled_plugins(self):
        yield from sorted(
            (p for p in self.plugins.values() if p._enabled),
            key=lambda x: x._priority,
            reverse=True,
        )

    def _iter_dispatch_plugins(self):
        for plugin in self._iter_enabled_plugins():
            if plugin._initialized and plugin.context.name not in self._reloading:
                yield plugin

    async def _call_single_plugin_hook(
        self, plugin: PluginBase, hook_name: str, event: Any
    ) -> HookResult | None:
        method = getattr(plugin, hook_name, None)
        if method is None:
            return None
        try:
            with self._track_plugin_call(plugin.context.name):
                async with asyncio.timeout(_PLUGIN_HOOK_TIMEOUT_SECONDS):
                    result = await method(event)
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
        return plugin.context.name, result

    @staticmethod
    def _validate_hook_result(hook_name: str, result: Any) -> bool:
        validator = _RESULT_VALIDATORS.get(hook_name)
        return isinstance(result, dict) and validator is not None and validator(result)

    async def call_plugin_hook(
        self, hook_name: str, payload: Any = None, *, event_hook: str | None = None
    ) -> list[HookResult]:
        if not self._accepting_hooks or (task := asyncio.current_task()) is None:
            return []
        results: list[HookResult] = []
        shared_event = (
            build_hook_event(hook_name) if hook_name == "on_auto_post" else None
        )
        with self._track_hook_task(task):
            for plugin in self._iter_dispatch_plugins():
                try:
                    event = shared_event or build_hook_event(
                        event_hook or hook_name, payload
                    )
                except ValueError as e:
                    logger.warning(f"Invalid plugin event: hook={hook_name}: {e}")
                    return []
                if result := await self._call_single_plugin_hook(
                    plugin, hook_name, event
                ):
                    results.append(result)
                    if hook_name in {"on_message", "on_mention"}:
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

    async def confirm_auto_post_published(self, plugin_name: str, content: str) -> None:
        if plugin_name in self._reloading or not (
            (plugin := self.plugins.get(plugin_name)) and plugin._started
        ):
            return

        try:
            with self._track_plugin_call(plugin_name):
                async with asyncio.timeout(_PLUGIN_HOOK_TIMEOUT_SECONDS):
                    await plugin.on_auto_post_published(content)
        except Exception as e:
            logger.exception(
                f"Auto-post confirmation failed: plugin={plugin.context.name}: {e}"
            )

    @classmethod
    async def _cleanup_plugin(cls, plugin: PluginBase) -> None:
        try:
            await cls._call_lifecycle(plugin, "cleanup")
        finally:
            plugin._initialized = False
            plugin._started = False
