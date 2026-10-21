import asyncio
import inspect
import re
from typing import Any
from urllib.parse import urlparse

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from ...clients.fetch.public import PublicFetcher
from ...clients.misskey.api import MisskeyAPI
from ...clients.misskey.bridge import SharedSession
from ...clients.misskey.stream import StreamingClient
from ...clients.openai.api import OpenAIAPI
from ...db.sqlite import DBManager
from ...plugin.manager import PluginManager
from ...shared.config import Config
from ...shared.errors import ConfigurationError
from ...shared.keys import ConfigKeys
from ..admin.service import AdminCommandService
from ..flows.chat import ChatHandler
from ..flows.mention import MentionHandler
from ..flows.notification import NotificationHandler
from ..flows.post import AutoPostService
from .connect import StreamingConnector
from .limits import ResponseLimiter
from .pipeline import ResponsePipeline
from .reload import SettingsReloader
from .runtime import BotRuntime

_SHUTDOWN_TIMEOUT_SECONDS = 5.0


class Neuro:
    def __init__(self, config: Config):
        self.config = config
        try:
            instance_url = config.get_required(ConfigKeys.MISSKEY_URL)
            access_token = config.get_required(ConfigKeys.MISSKEY_TOKEN)
            shared = SharedSession()
            nets = config.get(ConfigKeys.SYSTEM_ALLOW_NETS, [])
            self.fetcher = PublicFetcher(nets)
            self.misskey = MisskeyAPI(
                instance_url,
                access_token,
                shared=shared,
                fetcher=PublicFetcher(nets, origin=instance_url),
            )
            self.streaming = StreamingClient(
                instance_url,
                access_token,
                log_dump_events=bool(config.get(ConfigKeys.SYSTEM_DUMP_EVENTS)),
                shared=shared,
            )
            self.openai = OpenAIAPI(
                config.get_required(ConfigKeys.AI_API_KEY),
                config.get(ConfigKeys.BOT_MODEL),
                base_url=config.get(ConfigKeys.AI_BASE_URL),
                api_mode=config.get(ConfigKeys.BOT_API_MODE),
            )
            self.scheduler = AsyncIOScheduler()
        except (ValueError, TypeError, KeyError) as e:
            logger.error(f"Initialization failed: {e}")
            raise ConfigurationError() from e
        self.db = DBManager(config.db_path, config=config)
        self.runtime = BotRuntime()
        self.limits = ResponseLimiter(
            config=config,
            db=self.db,
            instance_url=self.misskey.instance_url,
            blacklist_user=lambda user_id: self.admin.blacklist_response_user(user_id),
        )
        self.plugin_manager = PluginManager(
            config,
            db=self.db,
            misskey=self.misskey,
            openai=self.openai,
            fetcher=self.fetcher,
            bot=self,
        )
        self.pipeline = ResponsePipeline(
            limits=self.limits, plugins=self.plugin_manager, runtime=self.runtime
        )
        self.bot_user_id = None
        self.bot_username = None
        self.chat = ChatHandler(self)
        self.mention = MentionHandler(self)
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
            on_timeline_note=lambda event: self.plugin_manager.call_plugin_hook(
                "on_timeline_note", event
            ),
        )
        self.admin = AdminCommandService(self)
        self.settings = SettingsReloader(self)
        logger.info("Bot initialized")

    async def start(self) -> None:
        if self.runtime.running:
            logger.warning("Bot is already running")
            return
        logger.info("Starting services...")
        self.runtime.running = True
        await self._initialize_services()
        self._setup_scheduler()
        await self.connect.setup_streaming()
        self.runtime.add_task("settings", self.settings.watch())
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
        await self.plugin_manager.startup_plugins()

    def _setup_scheduler(self) -> None:
        cron_jobs = [
            (self.auto_post.reset_daily_counters, 0),
            (self.db.vacuum, 2),
            (self.db.cleanup_response_limit_state, 3),
        ]
        for func, hour in cron_jobs:
            self.scheduler.add_job(func, "cron", hour=hour, minute=0, second=0)
        self.auto_post.apply_schedule(initial=True)
        self.scheduler.start()

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

    def _stop_scheduler(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    async def _stop_services(self) -> None:
        for action, step in (
            ("shutting down plugins", self.plugin_manager.shutdown_plugins),
            ("cleaning up plugins", self.plugin_manager.cleanup_plugins),
            ("stopping scheduler", self._stop_scheduler),
            ("cleaning up tasks", self.runtime.cleanup_tasks),
            ("closing streaming client", self.streaming.close),
            ("closing Misskey client", self.misskey.close),
            ("closing web fetcher", self.fetcher.close),
            ("closing OpenAI client", self.openai.close),
            ("closing database", self.db.close),
        ):
            try:
                if inspect.isawaitable(result := step()):
                    await result
            except Exception as e:
                logger.exception(f"Error {action}: {e}")

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
    def system_prompt(self) -> str:
        return self.config.get(ConfigKeys.BOT_SYSTEM_PROMPT, "")

    @property
    def ai_config(self) -> dict[str, Any]:
        return {
            "max_tokens": self.config.get(ConfigKeys.BOT_MAX_TOKENS),
            "temperature": self.config.get(ConfigKeys.BOT_TEMPERATURE),
        }
