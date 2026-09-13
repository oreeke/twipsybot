import asyncio
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from ...admin import AdminCommandService
from ...clients.misskey.api import MisskeyAPI
from ...clients.misskey.streaming import StreamingClient
from ...clients.misskey.transport import TCPClient
from ...clients.openai import OpenAIAPI
from ...db.sqlite import DBManager
from ...plugin.manager import PluginManager
from ...shared.config import Config
from ...shared.config_keys import ConfigKeys
from ...shared.exceptions import ConfigurationError
from ..flows.chat import ChatHandler
from ..flows.image import ImageGenerationService
from ..flows.mention import MentionHandler
from ..flows.notification import NotificationHandler
from ..flows.post import AutoPostService
from .connect import StreamingConnector
from .limits import ResponseLimiter
from .pipeline import ResponsePipeline
from .runtime import BotRuntime

__all__ = ("MisskeyBot",)

_SHUTDOWN_TIMEOUT_SECONDS = 5.0


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
                config.get(ConfigKeys.OPENAI_IMAGE_MODEL),
                config.get(ConfigKeys.OPENAI_IMAGE_SIZE),
                config.get(ConfigKeys.OPENAI_IMAGE_QUALITY),
            )
            self.scheduler = AsyncIOScheduler()
        except (ValueError, TypeError, KeyError) as e:
            logger.error(f"Initialization failed: {e}")
            raise ConfigurationError() from e
        self.db = DBManager(config.get(ConfigKeys.DB_PATH), config=config)
        self.runtime = BotRuntime()
        self.limits = ResponseLimiter(
            config=config,
            db=self.db,
            instance_url=getattr(self.misskey, "instance_url", None),
            blacklist_user=lambda user_id: self.admin.blacklist_response_user(user_id),
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
        self.chat = ChatHandler(self)
        self.mention = MentionHandler(self)
        self.image = ImageGenerationService(self)
        self.notification = NotificationHandler(self)
        self.auto_post = AutoPostService(self)
        self.connect = StreamingConnector(
            config=config,
            misskey=self.misskey,
            streaming=self.streaming,
            runtime=self.runtime,
            on_mention=self.mention.handle,
            on_message=self.chat.handle,
            on_notification=self.notification.handle,
            on_timeline_note=lambda note: self.plugin_manager.call_plugin_hook(
                "on_timeline_note", note
            ),
        )
        admin_config = config.get("bot.admin", {})
        self.admin = AdminCommandService(
            self, admin_config if isinstance(admin_config, dict) else {}
        )
        logger.info("Bot initialized")

    def is_response_blacklisted_user(self, *, user_id: str, handle: str | None) -> bool:
        return self.limits.is_response_blacklisted_user(user_id=user_id, handle=handle)

    async def start(self) -> None:
        if self.runtime.running:
            logger.warning("Bot is already running")
            return
        logger.info("Starting services...")
        self.runtime.running = True
        await self._initialize_services()
        self._setup_scheduler()
        await self.connect.setup_streaming()
        logger.info("Services ready; awaiting new tasks...")

    async def _initialize_services(self) -> None:
        await self.db.initialize()
        await self.auto_post.start()
        current_user = await self.misskey.get_current_user()
        self.bot_user_id = current_user.get("id")
        self.bot_username = current_user.get("username")
        logger.info(
            f"Connected to Misskey instance: bot_id={self.bot_user_id}, @{self.bot_username}"
        )
        await self.plugin_manager.load_plugins()
        await self.admin.start()
        await self.plugin_manager.startup_plugins()

    def _setup_scheduler(self) -> None:
        cron_jobs = [
            (self.auto_post.reset_daily_counters, 0),
            (self.db.vacuum, 2),
            (self.db.cleanup_response_limit_state, 3),
        ]
        for func, hour in cron_jobs:
            self.scheduler.add_job(func, "cron", hour=hour, minute=0, second=0)
        interval = self.config.get(ConfigKeys.BOT_AUTO_POST_INTERVAL)
        enabled = bool(self.config.get(ConfigKeys.BOT_AUTO_POST_ENABLED))
        logger.info(
            f"Auto-post scheduler ready; enabled={enabled}; interval: {interval}"
        )
        self.scheduler.add_job(
            self.auto_post.run,
            "interval",
            seconds=interval.total_seconds(),
            next_run_time=datetime.now(UTC) + timedelta(minutes=1),
            id="auto_post",
            replace_existing=True,
        )
        self.scheduler.start()

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
            async with asyncio.timeout(_SHUTDOWN_TIMEOUT_SECONDS):
                await self._stop_services()
        except TimeoutError:
            logger.warning(
                f"Shutdown timed out after {_SHUTDOWN_TIMEOUT_SECONDS:g}s; "
                "remaining cleanup cancelled"
            )
        finally:
            logger.info("Services stopped")

    async def _stop_services(self) -> None:
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

    def is_bot_mentioned(self, text: str) -> bool:
        if not text or not self.bot_username:
            return False
        pattern = re.compile(
            rf"(?<![A-Za-z0-9_@])@{re.escape(self.bot_username)}"
            rf"(?:(?:@(?P<host>[A-Za-z0-9.-]+))|(?![A-Za-z0-9_@]))",
            re.IGNORECASE,
        )
        current_host = urlparse(self.misskey.instance_url).hostname
        return any(
            not (host := match.group("host"))
            or bool(current_host and host.rstrip(".").lower() == current_host.lower())
            for match in pattern.finditer(text)
        )

    @property
    def ai_config(self) -> dict[str, Any]:
        return {
            "max_tokens": self.config.get(ConfigKeys.OPENAI_MAX_TOKENS),
            "temperature": self.config.get(ConfigKeys.OPENAI_TEMPERATURE),
        }
