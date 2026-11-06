import asyncio
import inspect
import re
from dataclasses import dataclass
from functools import cache, partial
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger
from yarl import URL

from ...clients.fetch.public import PublicFetcher
from ...clients.misskey.api import MisskeyAPI
from ...clients.misskey.bridge import SharedSession
from ...clients.misskey.stream import StreamingClient
from ...clients.openai.api import OpenAIAPI
from ...db.sqlite import DBManager
from ...plugin.manager import PluginManager
from ...shared.config import Config
from ..admin.service import AdminCommandService
from ..flows.chat import ChatHandler
from ..flows.mention import MentionHandler
from ..flows.post import AutoPostService
from .connect import StreamingConnector
from .limits import ResponseLimiter
from .pipeline import ClockTools, ResponsePipeline, Toolbox
from .reload import SettingsReloader
from .runtime import BotRuntime

_SHUTDOWN_TIMEOUT_SECONDS = 5.0


@cache
def _mention_pattern(username: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?<![A-Za-z0-9_@])@{re.escape(username)}"
        rf"(?:(?:@(?P<host>[A-Za-z0-9.-]+))|(?![A-Za-z0-9_@]))",
        re.IGNORECASE,
    )


@dataclass(frozen=True, slots=True)
class BotIdentity:
    id: str
    username: str
    host: str | None

    def mentioned_in(self, text: str) -> bool:
        return any(
            not (host := match["host"])
            or host.rstrip(".").lower() == (self.host or "").lower()
            for match in _mention_pattern(self.username).finditer(text)
        )


class Neuro:
    me: BotIdentity

    def __init__(self, config: Config):
        self.config = config
        connect, system = config.connect, config.settings.system
        shared = SharedSession()
        self.fetcher = PublicFetcher(system.allow_nets)
        self.misskey = MisskeyAPI(
            connect.misskey_instance_url,
            connect.misskey_access_token,
            shared=shared,
            fetcher=PublicFetcher(
                system.allow_nets, origin=connect.misskey_instance_url
            ),
        )
        self.streaming = StreamingClient(
            connect.misskey_instance_url,
            connect.misskey_access_token,
            log_dump_events=system.dump_events,
            shared=shared,
        )
        self.openai = OpenAIAPI(
            connect.ai_api_key,
            config.settings.bot.model,
            base_url=connect.ai_base_url,
            api_mode=config.settings.bot.api_mode,
        )
        self.scheduler = AsyncIOScheduler()
        self.db = DBManager(config.db_path)
        self.runtime = BotRuntime()
        self.admin = AdminCommandService(self)
        self.limits = ResponseLimiter(
            config=config,
            db=self.db,
            instance_url=self.misskey.instance_url,
            blacklist_user=self.admin.blacklist_response_user,
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
            limits=self.limits,
            plugins=self.plugin_manager,
            runtime=self.runtime,
            is_admin=lambda user: self.admin.is_admin(user.id, user.handle),
            is_follower=self.admin.is_follower,
        )
        self.chat = ChatHandler(self)
        self.mention = MentionHandler(self)
        self.auto_post = AutoPostService(self)
        self.pipeline.add_tools("note", self.auto_post)
        self.pipeline.add_tools("time", ClockTools())
        self.connect = StreamingConnector(
            config=config,
            misskey=self.misskey,
            streaming=self.streaming,
            runtime=self.runtime,
            on_mention=self.mention.handle,
            on_message=self.chat.handle,
            on_notification=partial(
                self.plugin_manager.call_plugin_hook, "on_notification"
            ),
            on_timeline_note=partial(
                self.plugin_manager.call_plugin_hook, "on_timeline_note"
            ),
        )
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
        user = await self.misskey.get_current_user()
        self.me = BotIdentity(
            user["id"], user["username"], URL(self.misskey.instance_url).host
        )
        logger.info(
            f"Connected to Misskey instance: bot_id={self.me.id}, @{self.me.username}"
        )
        await self.plugin_manager.load_plugins()
        await self.plugin_manager.startup_plugins()

    def _setup_scheduler(self) -> None:
        self.scheduler.add_job(self._maintain_db, "cron", hour=3)
        self.auto_post.apply_schedule(initial=True)
        self.scheduler.start()

    async def _maintain_db(self) -> None:
        await self.db.maintain(self.config.settings.system.db_clear_days)

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

    @property
    def system_prompt(self) -> str:
        return self.config.settings.bot.system_prompt

    @property
    def ai_config(self) -> dict[str, Any]:
        bot = self.config.settings.bot
        return {"max_tokens": bot.max_tokens, "temperature": bot.temperature}

    async def generate_reply(
        self,
        history: list[dict[str, str]],
        content: str | list[dict[str, Any]],
        tools: Toolbox,
    ) -> str:
        messages: list[dict[str, Any]] = (
            [{"role": "system", "content": self.system_prompt}]
            if self.system_prompt
            else []
        )
        messages += [*history, {"role": "user", "content": content}]
        return await self.openai.generate_chat(messages, **self.ai_config, tools=tools)
