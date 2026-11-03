import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Annotated, Any, Literal
from uuid import uuid4

from apscheduler.job import Job
from apscheduler.triggers.base import BaseTrigger
from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger
from pydantic import Field

from ...clients.misskey.models import MentionEvent, MessageEvent
from ...plugin.base import tool
from ...plugin.contracts import AutoPostItem
from ...plugin.manager import HookResult
from ...shared.config import PostConfig
from ...shared.logs import format_log_text

if TYPE_CHECKING:
    from ..engine.core import Neuro


JOB_ID = "auto_post"
_NOTE_JOB_PREFIX = "note_"


class AutoPostService:
    _PLUGIN_POST_INTERVAL_SECONDS = 10
    _MISFIRE_GRACE_SECONDS = 300

    def __init__(self, bot: "Neuro"):
        self.bot = bot
        self._state = (self._today(), 0)

    @staticmethod
    def _today() -> str:
        return datetime.now().astimezone().date().isoformat()

    @property
    def posts_today(self) -> int:
        date, posts = self._state
        return posts if date == self._today() else 0

    async def _save(self, posts: int) -> None:
        self._state = (self._today(), posts)
        await self.bot.db.set_auto_post_state(*self._state)

    @property
    def _settings(self) -> PostConfig:
        return self.bot.config.settings.autopost

    @property
    def mode(self) -> str:
        return self._settings.mode

    def _trigger(self) -> BaseTrigger | None:
        settings = self._settings
        match settings.mode:
            case "rotation":
                return IntervalTrigger(seconds=round(settings.interval.total_seconds()))
            case "schedule":
                return OrTrigger(
                    [
                        CronTrigger(hour=int(t[:2]), minute=int(t[3:]))
                        for t in settings.times
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

    async def start(self) -> None:
        if state := await self.bot.db.get_auto_post_state():
            self._state = state
        expired = datetime.now().astimezone() - timedelta(
            seconds=self._MISFIRE_GRACE_SECONDS
        )
        for note_id, run_at, *note in await self.bot.db.get_scheduled_notes():
            if (when := datetime.fromisoformat(run_at)) < expired:
                logger.warning(f"Scheduled note expired while offline: {note_id}")
                await self.bot.db.delete_scheduled_note(note_id)
            else:
                self._add_note_job(note_id, when, *note)

    async def post_count(self) -> None:
        await self._save(self.posts_today + 1)

    async def reset_daily_counters(self) -> None:
        await self._save(0)
        logger.debug("Post counter reset")

    @tool(
        "Publish a note immediately, or at a given time. Write the complete note "
        "text yourself. Use only when the user explicitly asks you to post.",
        access="admin",
        effect=True,
    )
    async def publish(
        self,
        event: MessageEvent | MentionEvent,
        text: Annotated[str, Field(description="The complete note text to publish")],
        visibility: Annotated[
            Literal["public", "home", "followers"] | None,
            Field(
                description="Note visibility; omit or null unless the user specifies it"
            ),
        ] = None,
        local_only: Annotated[
            bool | None,
            Field(
                description="true = do not federate; the note stays on this server. "
                "Omit or null unless the user specifies it"
            ),
        ] = None,
        at: Annotated[
            str | None,
            Field(
                description="Local date-time 'YYYY-MM-DD HH:MM' (24-hour, bot host "
                "timezone) to publish later; call time_now first to get the current "
                "time. Omit or null to publish immediately"
            ),
        ] = None,
    ) -> str:
        if not (text := text.strip()):
            return "Error: the note text is empty"
        logger.debug(
            f"Manual post: visibility={visibility} local_only={local_only} at={at}"
        )
        if at is not None:
            return await self._schedule_note(text, visibility, local_only, at)
        await self._send_note(text, visibility, local_only)
        logger.info(f"Manual post succeeded: {format_log_text(text)}")
        return "Note published."

    @tool(
        "List notes scheduled for later publication, with their ids and times.",
        access="admin",
        effect=True,
    )
    async def pending(self, event: MessageEvent | MentionEvent) -> str:
        if not (jobs := self._scheduled_jobs()):
            return "No scheduled notes."
        return "\n".join(
            f"{job.id} | {job.next_run_time:%Y-%m-%d %H:%M %z} | {job.name}"
            for job in jobs
        )

    @tool(
        "Cancel a scheduled note by id; call pending first to find the id.",
        access="admin",
        effect=True,
    )
    async def cancel(
        self,
        event: MessageEvent | MentionEvent,
        note_id: Annotated[str, Field(description="Scheduled note id from pending")],
    ) -> str:
        if note_id not in {job.id for job in self._scheduled_jobs()}:
            return "Error: no scheduled note with this id"
        self.bot.scheduler.remove_job(note_id)
        await self.bot.db.delete_scheduled_note(note_id)
        logger.info(f"Scheduled note cancelled: {note_id}")
        return "Scheduled note cancelled."

    def _scheduled_jobs(self) -> list[Job]:
        return sorted(
            (
                job
                for job in self.bot.scheduler.get_jobs()
                if job.id.startswith(_NOTE_JOB_PREFIX)
            ),
            key=lambda job: job.next_run_time,
        )

    async def _schedule_note(
        self, text: str, visibility: str | None, local_only: bool | None, at: str
    ) -> str:
        try:
            run_at = datetime.fromisoformat(at.strip()).astimezone()
        except ValueError:
            return "Error: at must be 'YYYY-MM-DD HH:MM'"
        if run_at <= datetime.now().astimezone():
            return "Error: the time has already passed; call time_now to check the time"
        note_id = f"{_NOTE_JOB_PREFIX}{uuid4().hex[:6]}"
        await self.bot.db.add_scheduled_note(
            note_id, run_at.isoformat(), text, visibility, local_only
        )
        self._add_note_job(note_id, run_at, text, visibility, local_only)
        logger.info(f"Note scheduled: {note_id} at {run_at}")
        return f"Scheduled as {note_id} for {run_at:%Y-%m-%d %H:%M %z}."

    def _add_note_job(
        self,
        note_id: str,
        run_at: datetime,
        text: str,
        visibility: str | None,
        local_only: bool | None,
    ) -> None:
        self.bot.scheduler.add_job(
            self._send_scheduled,
            "date",
            run_date=run_at,
            args=[note_id, text, visibility, local_only],
            id=note_id,
            name=format_log_text(text),
            misfire_grace_time=self._MISFIRE_GRACE_SECONDS,
        )

    async def _send_scheduled(
        self, note_id: str, text: str, visibility: str | None, local_only: bool | None
    ) -> None:
        if not self.bot.runtime.running:
            return
        try:
            await self._send_note(text, visibility, local_only)
            logger.info(f"Scheduled post succeeded: {format_log_text(text)}")
        except Exception as e:
            logger.error(f"Scheduled post failed: {e}")
        await self.bot.db.delete_scheduled_note(note_id)

    def _allowed(self, limit: int | None) -> bool:
        if not self.bot.runtime.running:
            return False
        if limit is not None and self.posts_today >= limit:
            logger.debug(f"Daily post limit reached ({limit}); skipping auto-post")
            return False
        return True

    async def _record(self, limit: int | None) -> None:
        if limit is not None:
            await self.post_count()
            logger.info(f"Daily post count: {self.posts_today}/{limit}")

    async def run(self) -> None:
        if (mode := self.mode) == "off":
            return
        max_posts = self._settings.daily_max if mode == "rotation" else None
        local_only = self._settings.local_only
        if not self._allowed(max_posts):
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
        self,
        plugin_results: list[HookResult],
        max_posts: int | None,
        local_only: bool | None,
    ) -> bool:
        default_visibility = self._settings.visibility
        for name, result in plugin_results:
            if "contents" in result and await self._post_plugin_contents(
                name,
                result["contents"],
                result.get("visibility", default_visibility),
                max_posts,
                local_only,
            ):
                return True
        return False

    async def _post_plugin_contents(
        self,
        plugin_name: str,
        contents: Sequence[str | AutoPostItem],
        visibility: str | None,
        max_posts: int | None,
        local_only: bool | None,
    ) -> bool:
        posted_any = False
        for i, item in enumerate(contents):
            if not self._allowed(max_posts):
                return posted_any
            content, file_ids = (
                (item, None)
                if isinstance(item, str)
                else (item["text"], item.get("file_ids"))
            )
            await self.bot.misskey.create_note(
                content,
                visibility=visibility,
                local_only=local_only,
                file_ids=file_ids,
            )
            await self.bot.plugin_manager.confirm_auto_post_published(
                plugin_name, content
            )
            posted_any = True
            logger.info(f"Auto-post succeeded: {format_log_text(content)}")
            await self._record(max_posts)
            if i < len(contents) - 1:
                await asyncio.sleep(self._PLUGIN_POST_INTERVAL_SECONDS)
        return posted_any

    async def _generate_ai_post(
        self, plugin_results: list[HookResult], max_posts: int | None
    ) -> None:
        name, result = next(
            ((n, r) for n, r in plugin_results if "prompt" in r), ("", {})
        )
        plugin_prompt = result.get("prompt", "")
        if name:
            logger.info(f"Plugin {name} requested prompt modification: {plugin_prompt}")
        try:
            content = await self._create_ai_post(self._settings.prompt, plugin_prompt)
        except ValueError as e:
            logger.warning(f"Auto-post failed; skipping this run: {e}")
            return
        logger.info(f"Auto-post succeeded: {format_log_text(content)}")
        await self._record(max_posts)

    async def _send_note(
        self, content: str, visibility: str | None, local_only: bool | None
    ) -> None:
        await self.bot.misskey.create_note(
            content,
            visibility=(
                visibility if visibility is not None else self._settings.visibility
            ),
            local_only=(
                local_only if local_only is not None else self._settings.local_only
            ),
        )

    async def _create_ai_post(self, prompt: str, plugin_prompt: str = "") -> str:
        content = await self.generate(plugin_prompt, prompt)
        await self._send_note(content, None, None)
        return content

    async def generate(self, plugin_prompt: str = "", prompt: str | None = None) -> str:
        if prompt is None:
            prompt = self._settings.prompt
        if not prompt:
            raise ValueError("Missing prompt")
        return await self.bot.openai.generate_text(
            f"{plugin_prompt}{prompt}", self.bot.system_prompt, **self.bot.ai_config
        )
