import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from loguru import logger

from ...shared.config_keys import ConfigKeys
from ..engine.pipeline import AIResponse

if TYPE_CHECKING:
    from ..engine.core import MisskeyBot


class AutoPostService:
    _PLUGIN_POST_INTERVAL_SECONDS = 10

    def __init__(self, bot: "MisskeyBot"):
        self.bot = bot
        self._counter_lock = asyncio.Lock()
        self._post_date = self._today()
        self.posts_today = 0

    @staticmethod
    def _today() -> str:
        return datetime.now().astimezone().date().isoformat()

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
        logger.info(f"Manual post succeeded: {self.bot.format_log_text(content)}")
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

    async def run(self) -> None:
        if not self.bot.config.get(ConfigKeys.BOT_AUTO_POST_ENABLED):
            return
        max_posts = self.bot.config.get(ConfigKeys.BOT_AUTO_POST_MAX_PER_DAY)
        local_only = self.bot.config.get(ConfigKeys.BOT_AUTO_POST_LOCAL_ONLY)
        if not self.bot.runtime.running or not await self.check_post_counter(max_posts):
            return
        try:
            plugin_results = await self.bot.plugin_manager.call_plugin_hook(
                "on_auto_post"
            )
            if await self._try_plugin_post(plugin_results, max_posts, local_only):
                return
            await self._generate_ai_post(plugin_results, max_posts)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Error during auto-post: {e}")

    async def _try_plugin_post(
        self, plugin_results: list[Any], max_posts: int, local_only: bool | None
    ) -> bool:
        for result in plugin_results:
            extracted = self._extract_plugin_post_request(result)
            if not extracted:
                continue
            visibility, contents = extracted
            posted_any = await self._post_plugin_contents(
                result, contents, visibility, max_posts, local_only
            )
            if posted_any:
                return True
        return False

    def _extract_plugin_post_request(
        self, result: Any
    ) -> tuple[str | None, list[str]] | None:
        if not isinstance(result, dict):
            return None
        visibility = result.get(
            "visibility",
            self.bot.config.get(ConfigKeys.BOT_AUTO_POST_VISIBILITY),
        )
        contents = self._extract_plugin_contents(result)
        if not contents:
            return None
        return visibility, contents

    @staticmethod
    def _extract_plugin_contents(result: Any) -> list[str]:
        contents_value = result.get("contents")
        if isinstance(contents_value, list):
            return [c for c in contents_value if isinstance(c, str) and c]
        return []

    async def _post_plugin_contents(
        self,
        result: dict[str, Any],
        contents: list[str],
        visibility: str | None,
        max_posts: int,
        local_only: bool | None,
    ) -> bool:
        posted_any = False
        for i, content in enumerate(contents):
            if not self.bot.runtime.running or not await self.check_post_counter(
                max_posts
            ):
                return posted_any
            await self.bot.misskey.create_note(
                content, visibility=visibility, local_only=local_only
            )
            await self.bot.plugin_manager.confirm_auto_post_published(result, content)
            await self.post_count()
            posted_any = True
            logger.info(f"Auto-post succeeded: {self.bot.format_log_text(content)}")
            logger.info(f"Daily post count: {self.posts_today}/{max_posts}")
            if i < len(contents) - 1:
                await asyncio.sleep(self._PLUGIN_POST_INTERVAL_SECONDS)
        return posted_any

    async def _generate_ai_post(
        self, plugin_results: list[Any], max_posts: int
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
                self.bot.config.get(ConfigKeys.BOT_AUTO_POST_PROMPT, ""),
                plugin_prompt,
                timestamp_override,
            )
        except ValueError as e:
            logger.warning(f"Auto-post failed; skipping this run: {e}")
            return
        await self.post_count()
        logger.info(f"Auto-post succeeded: {self.bot.format_log_text(content)}")
        logger.info(f"Daily post count: {self.posts_today}/{max_posts}")

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
                else self.bot.config.get(ConfigKeys.BOT_AUTO_POST_VISIBILITY)
            ),
            local_only=(
                local_only
                if local_only is not None
                else self.bot.config.get(ConfigKeys.BOT_AUTO_POST_LOCAL_ONLY)
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
