import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from apscheduler.triggers.base import BaseTrigger
from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from ...shared.config_keys import ConfigKeys
from ...shared.utils import format_log_text
from ..engine.pipeline import AIResponse

if TYPE_CHECKING:
    from ..engine.core import Neuro


JOB_ID = "auto_post"


class AutoPostService:
    _PLUGIN_POST_INTERVAL_SECONDS = 10
    _MISFIRE_GRACE_SECONDS = 300

    def __init__(self, bot: "Neuro"):
        self.bot = bot
        self._counter_lock = asyncio.Lock()
        self._post_date = self._today()
        self.posts_today = 0

    @staticmethod
    def _today() -> str:
        return datetime.now().astimezone().date().isoformat()

    @property
    def mode(self) -> str:
        get = self.bot.config.get
        if get(ConfigKeys.POST_ROTATION):
            return "rotation"
        return "schedule" if get(ConfigKeys.POST_SCHEDULE) else "off"

    def _trigger(self) -> BaseTrigger | None:
        get = self.bot.config.get
        match self.mode:
            case "rotation":
                return IntervalTrigger(
                    seconds=get(ConfigKeys.POST_INTERVAL).total_seconds()
                )
            case "schedule":
                return OrTrigger(
                    [
                        CronTrigger(hour=int(t[:2]), minute=int(t[3:]))
                        for t in get(ConfigKeys.POST_TIMES)
                    ]
                )
        return None

    def apply_schedule(self, *, initial: bool = False) -> None:
        scheduler = self.bot.scheduler
        if scheduler.get_job(JOB_ID):
            scheduler.remove_job(JOB_ID)
        if (trigger := self._trigger()) is None:
            logger.info("Auto-post off")
            return
        options: dict[str, Any] = {}
        if initial and self.mode == "rotation":
            options["next_run_time"] = datetime.now(UTC) + timedelta(minutes=1)
        scheduler.add_job(
            self.run,
            trigger,
            id=JOB_ID,
            coalesce=True,
            misfire_grace_time=self._MISFIRE_GRACE_SECONDS,
            **options,
        )
        logger.info(f"Auto-post {self.mode}: {trigger}")

    @property
    def daily_post_count(self) -> int:
        return self.posts_today if self._post_date == self._today() else 0

    async def start(self) -> None:
        today = self._today()
        state = await self.bot.db.get_auto_post_state()
        self._post_date = today
        if state and state[0] == today:
            self.posts_today = state[1]
            return
        await self.bot.db.set_auto_post_state(today, 0)
        self.posts_today = 0

    async def _ensure_current_day(self) -> None:
        today = self._today()
        if self._post_date == today:
            return
        await self.bot.db.set_auto_post_state(today, 0)
        self._post_date = today
        self.posts_today = 0

    async def post_count(self) -> None:
        async with self._counter_lock:
            today = self._today()
            if self._post_date != today:
                self._post_date = today
                self.posts_today = 0
            self.posts_today += 1
            await self.bot.db.set_auto_post_state(self._post_date, self.posts_today)

    async def check_post_counter(self, max_posts: int) -> bool:
        async with self._counter_lock:
            await self._ensure_current_day()
            if self.posts_today >= max_posts:
                logger.debug(
                    f"Daily post limit reached ({max_posts}); skipping auto-post"
                )
                return False
            return True

    async def reset_daily_counters(self) -> None:
        async with self._counter_lock:
            today = self._today()
            await self.bot.db.set_auto_post_state(today, 0)
            self._post_date = today
            self.posts_today = 0
        logger.debug("Post counter reset")

    async def generate_response(self, prompt: str) -> AIResponse:
        prompt, visibility, local_only = self._parse_manual_options(prompt)
        try:
            content = await self._create_ai_post(
                prompt, visibility=visibility, local_only=local_only
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Manual post failed")
            return AIResponse("发帖失败，请稍后再试。")
        logger.info(f"Manual post succeeded: {format_log_text(content)}")
        return AIResponse("发帖完成")

    @staticmethod
    def _parse_manual_options(prompt: str) -> tuple[str, str | None, bool | None]:
        visibility = None
        local_only = None
        remaining = prompt
        while (parts := remaining.split(maxsplit=1)) and parts[0] in {
            "-p",
            "-h",
            "-f",
            "-l",
        }:
            option = parts[0]
            remaining = parts[1] if len(parts) == 2 else ""
            if option == "-l":
                local_only = True
            else:
                visibility = {"-p": "public", "-h": "home", "-f": "followers"}[option]
                local_only = local_only or False
        return remaining, visibility, local_only

    async def _allowed(self, limit: int | None) -> bool:
        return self.bot.runtime.running and (
            limit is None or await self.check_post_counter(limit)
        )

    async def _record(self, limit: int | None) -> None:
        if limit is not None:
            await self.post_count()
            logger.info(f"Daily post count: {self.posts_today}/{limit}")

    async def run(self) -> None:
        if (mode := self.mode) == "off":
            return
        max_posts = (
            self.bot.config.get(ConfigKeys.POST_DAILY_MAX)
            if mode == "rotation"
            else None
        )
        local_only = self.bot.config.get(ConfigKeys.POST_LOCAL_ONLY)
        if not await self._allowed(max_posts):
            return
        try:
            plugin_results = await self.bot.plugin_manager.call_plugin_hook(
                "on_auto_post"
            )
            if await self._try_plugin_post(plugin_results, max_posts, local_only):
                return
            await self._generate_ai_post(plugin_results, max_posts)
        except asyncio.CancelledError:
            if self.bot.runtime.running:
                raise
            logger.debug("Auto-post cancelled during shutdown")
        except Exception as e:
            logger.error(f"Error during auto-post: {e}")

    async def _try_plugin_post(
        self, plugin_results: list[Any], max_posts: int | None, local_only: bool | None
    ) -> bool:
        default_visibility = self.bot.config.get(ConfigKeys.POST_VISIBILITY)
        for result in plugin_results:
            if "contents" in result and await self._post_plugin_contents(
                result,
                result["contents"],
                result.get("visibility", default_visibility),
                max_posts,
                local_only,
            ):
                return True
        return False

    async def _post_plugin_contents(
        self,
        result: dict[str, Any],
        contents: list[str],
        visibility: str | None,
        max_posts: int | None,
        local_only: bool | None,
    ) -> bool:
        posted_any = False
        for i, content in enumerate(contents):
            if not await self._allowed(max_posts):
                return posted_any
            await self.bot.misskey.create_note(
                content, visibility=visibility, local_only=local_only
            )
            await self.bot.plugin_manager.confirm_auto_post_published(result, content)
            posted_any = True
            logger.info(f"Auto-post succeeded: {format_log_text(content)}")
            await self._record(max_posts)
            if i < len(contents) - 1:
                await asyncio.sleep(self._PLUGIN_POST_INTERVAL_SECONDS)
        return posted_any

    async def _generate_ai_post(
        self, plugin_results: list[Any], max_posts: int | None
    ) -> None:
        result = next((item for item in plugin_results if "prompt" in item), None)
        plugin_prompt = result["prompt"] if result else ""
        timestamp_override = result.get("timestamp") if result else None
        if result:
            logger.info(
                f"Plugin {result.get('plugin_name')} requested prompt modification: {plugin_prompt}"
            )
        try:
            content = await self._create_ai_post(
                self.bot.config.get(ConfigKeys.POST_PROMPT, ""),
                plugin_prompt,
                timestamp_override,
            )
        except ValueError as e:
            logger.warning(f"Auto-post failed; skipping this run: {e}")
            return
        logger.info(f"Auto-post succeeded: {format_log_text(content)}")
        await self._record(max_posts)

    async def _create_ai_post(
        self,
        prompt: str,
        plugin_prompt: str = "",
        timestamp_override: int | None = None,
        *,
        visibility: str | None = None,
        local_only: bool | None = None,
    ) -> str:
        content = await self._generate_post(
            self.bot.system_prompt,
            prompt,
            plugin_prompt,
            timestamp_override,
        )
        await self.bot.misskey.create_note(
            content,
            visibility=(
                visibility
                if visibility is not None
                else self.bot.config.get(ConfigKeys.POST_VISIBILITY)
            ),
            local_only=(
                local_only
                if local_only is not None
                else self.bot.config.get(ConfigKeys.POST_LOCAL_ONLY)
            ),
        )
        return content

    async def _generate_post(
        self,
        system_prompt: str,
        prompt: str,
        plugin_prompt: str,
        timestamp_override: int | None = None,
    ) -> str:
        if not prompt:
            raise ValueError("Missing prompt")
        timestamp_min = (
            timestamp_override
            if timestamp_override is not None
            else int(datetime.now(UTC).timestamp() // 60)
        )
        full_prompt = f"[{timestamp_min}] {plugin_prompt}{prompt}"
        return await self.bot.openai.generate_text(
            full_prompt, system_prompt, **self.bot.ai_config
        )
