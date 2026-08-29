import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from cachetools import TTLCache
from loguru import logger

from ...admin import AdminCommandService
from ...clients.misskey.misskey_api import MisskeyAPI
from ...clients.misskey.streaming import StreamingClient
from ...clients.misskey.transport import TCPClient
from ...clients.openai import OpenAIAPI
from ...db.sqlite import DBManager
from ...plugin.manager import PluginManager
from ...shared.config import Config
from ...shared.config_keys import ConfigKeys
from ...shared.constants import CHAT_CACHE_MAX_USERS, CHAT_CACHE_TTL
from ...shared.exceptions import ConfigurationError
from ...shared.utils import get_memory_usage, resolve_history_limit
from .connect import StreamingConnector
from .handlers import BotHandlers
from .limits import ResponseLimiter
from .pipe import ResponsePipeline
from .runtime import BotRuntime

__all__ = ("MisskeyBot",)


class MisskeyBot:
    def __init__(self, config: Config):
        self.config = config
        try:
            instance_url = config.get_required(ConfigKeys.MISSKEY_INSTANCE_URL)
            access_token = config.get_required(ConfigKeys.MISSKEY_ACCESS_TOKEN)
            self._misskey_transport = TCPClient()
            self.misskey = MisskeyAPI(
                instance_url, access_token, transport=self._misskey_transport
            )
            self.streaming = StreamingClient(
                instance_url,
                access_token,
                log_dump_events=bool(config.get(ConfigKeys.LOG_DUMP_EVENTS)),
                transport=self._misskey_transport,
            )
            self.openai = OpenAIAPI(
                config.get_required(ConfigKeys.OPENAI_API_KEY),
                config.get(ConfigKeys.OPENAI_MODEL),
                config.get(ConfigKeys.OPENAI_API_BASE),
                config.get(ConfigKeys.OPENAI_API_MODE),
            )
            self.scheduler = AsyncIOScheduler()
        except (ValueError, TypeError, KeyError) as e:
            logger.error(f"Initialization failed: {e}")
            raise ConfigurationError() from e
        self.db = DBManager(config.get(ConfigKeys.DB_PATH), config=config)
        self.runtime = BotRuntime(self)
        self.limits = ResponseLimiter(
            config=config,
            db=self.db,
            instance_url=getattr(self.misskey, "instance_url", None),
        )
        self.plugin_manager = PluginManager(
            config,
            db=self.db,
            misskey=self.misskey,
            openai=self.openai,
            bot=self,
        )
        self.pipeline = ResponsePipeline(limits=self.limits)
        self.system_prompt = config.get(ConfigKeys.BOT_SYSTEM_PROMPT, "")
        self.bot_user_id = None
        self.bot_username = None
        self._chat_histories: TTLCache[str, list[dict[str, str]]] = TTLCache(
            maxsize=CHAT_CACHE_MAX_USERS,
            ttl=CHAT_CACHE_TTL,
            timer=time.monotonic,
        )
        self.handlers = BotHandlers(self)
        self.connect = StreamingConnector(
            config=config,
            misskey=self.misskey,
            streaming=self.streaming,
            runtime=self.runtime,
            handlers=self.handlers,
        )
        admin_config = config.get("bot.admin", {})
        self.admin = AdminCommandService(
            self, admin_config if isinstance(admin_config, dict) else {}
        )
        logger.info("Bot initialized")

    def is_response_blacklisted_user(self, *, user_id: str, handle: str | None) -> bool:
        return self.limits.is_response_blacklisted_user(user_id=user_id, handle=handle)

    def load_timeline_channels(self) -> set[str]:
        return self.connect.load_timeline_channels()

    def get_timeline_channels(self) -> set[str]:
        return self.connect.get_timeline_channels()

    def set_timeline_channels(self, channels: set[str]) -> set[str]:
        return self.connect.set_timeline_channels(channels)

    async def restart_streaming(self) -> None:
        await self.connect.restart_streaming()

    async def get_or_load_chat_history(
        self,
        conversation_id: str,
        *,
        limit: int | None,
        user_id: str | None = None,
        room_id: str | None = None,
    ) -> list[dict[str, str]]:
        limit_value = resolve_history_limit(
            self.config.get(ConfigKeys.BOT_RESPONSE_CHAT_MEMORY), limit
        )
        if (cached := self._chat_histories.get(conversation_id)) is not None:
            return self._trim_chat_history(list(cached), limit_value)
        if conversation_id.startswith("room:"):
            room_id = room_id or conversation_id.removeprefix("room:")
        history = await self.handlers.chat.get_chat_history(
            user_id=user_id, room_id=room_id, limit=limit_value
        )
        trimmed = self._trim_chat_history(history, limit_value)
        self._chat_histories[conversation_id] = trimmed
        return list(trimmed)

    @staticmethod
    def _trim_chat_history(
        history: list[dict[str, str]], limit_value: int
    ) -> list[dict[str, str]]:
        return history[-limit_value * 2 :] if limit_value > 0 else []

    def append_chat_turn(
        self,
        conversation_id: str,
        user_text: str,
        assistant_text: str,
        limit: int | None,
    ) -> None:
        limit_value = resolve_history_limit(
            self.config.get(ConfigKeys.BOT_RESPONSE_CHAT_MEMORY), limit
        )
        history = list(self._chat_histories.get(conversation_id) or [])
        last = next(reversed(history), None)
        if user_text and not (
            isinstance(last, dict)
            and last.get("role") == "user"
            and last.get("content") == user_text
        ):
            history.append({"role": "user", "content": user_text})
        last = next(reversed(history), None)
        if assistant_text and not (
            isinstance(last, dict)
            and last.get("role") == "assistant"
            and last.get("content") == assistant_text
        ):
            history.append({"role": "assistant", "content": assistant_text})
        self._chat_histories[conversation_id] = self._trim_chat_history(
            history, limit_value
        )

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()
        return False

    async def start(self) -> None:
        if self.runtime.running:
            logger.warning("Bot is already running")
            return
        logger.info("Starting services...")
        self.runtime.running = True
        await self._initialize_services()
        self._setup_scheduler()
        await self._setup_streaming()
        logger.info("Services ready; awaiting new tasks...")
        memory_usage = get_memory_usage()
        logger.debug(f"Memory usage: {memory_usage['rss_mb']} MB")

    async def _initialize_services(self) -> None:
        await self.db.initialize()
        current_user = await self.misskey.get_current_user()
        self.bot_user_id = current_user.get("id")
        self.bot_username = current_user.get("username")
        logger.info(
            f"Connected to Misskey instance: bot_id={self.bot_user_id}, @{self.bot_username}"
        )
        await self.plugin_manager.load_plugins()
        if self.config.get(ConfigKeys.BOT_ADMIN_ENABLED):
            await self.admin.start()
        await self.plugin_manager.startup_plugins()

    def _setup_scheduler(self) -> None:
        cron_jobs = [
            (self.handlers.auto_post.reset_daily_counters, 0),
            (self.db.vacuum, 2),
            (self.db.cleanup_response_limit_state, 3),
        ]
        for func, hour in cron_jobs:
            self.scheduler.add_job(func, "cron", hour=hour, minute=0, second=0)
        interval_minutes = self.config.get(ConfigKeys.BOT_AUTO_POST_INTERVAL)
        enabled = bool(self.config.get(ConfigKeys.BOT_AUTO_POST_ENABLED))
        logger.info(
            f"Auto-post scheduler ready; enabled={enabled}; interval: {interval_minutes} minutes"
        )
        self.scheduler.add_job(
            self.handlers.on_auto_post,
            "interval",
            minutes=interval_minutes,
            next_run_time=datetime.now(UTC) + timedelta(minutes=1),
            id="auto_post",
            replace_existing=True,
        )
        self.scheduler.start()

    async def _setup_streaming(self) -> None:
        await self.connect.setup_streaming()

    @staticmethod
    async def _run_stop_steps(steps: tuple[tuple[str, Any], ...]) -> None:
        for action, operation in steps:
            try:
                await operation()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception(f"Error {action}: {e}")

    async def stop(self) -> None:
        if not self.runtime.running:
            logger.warning("Bot is already stopped")
            return
        logger.info("Stopping services...")
        self.runtime.running = False
        try:
            await self._run_stop_steps(
                (
                    ("shutting down plugins", self.plugin_manager.shutdown_plugins),
                    ("cleaning up plugins", self.plugin_manager.cleanup_plugins),
                )
            )
            try:
                if self.scheduler.running:
                    self.scheduler.shutdown(wait=False)
            except Exception as e:
                logger.exception(f"Error stopping scheduler: {e}")
            await self._run_stop_steps(
                (
                    ("cleaning up tasks", self.runtime.cleanup_tasks),
                    ("closing streaming client", self.streaming.close),
                    ("closing Misskey client", self.misskey.close),
                    ("closing OpenAI client", self.openai.close),
                    ("closing database", self.db.close),
                )
            )
        finally:
            logger.info("Services stopped")

    def is_bot_mentioned(self, text: str) -> bool:
        return bool(text and self.bot_username and f"@{self.bot_username}" in text)

    @staticmethod
    def format_log_text(text: str, max_length: int = 50) -> str:
        if not text:
            return "None"
        suffix = "..." if len(text) > max_length else ""
        return f"{text[:max_length]}{suffix}"

    @property
    def ai_config(self) -> dict[str, Any]:
        return {
            "max_tokens": self.config.get(ConfigKeys.OPENAI_MAX_TOKENS),
            "temperature": self.config.get(ConfigKeys.OPENAI_TEMPERATURE),
        }
